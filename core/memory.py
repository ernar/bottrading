"""Memoria persistente de señales con evaluación de resultados.

Registra cada señal con el precio del momento; en ciclos posteriores
evalúa si el precio se movió a favor o en contra (o tocó SL/TP) y genera
un resumen de rendimiento por símbolo que se inyecta en el prompt para
que el modelo tenga feedback de sus señales recientes.
"""
import json
import os
import threading
from datetime import datetime
from typing import Optional

MEMORY_PATH = "logs/memory.json"
MAX_RECORDS_PER_SYMBOL = 30
MIN_EVAL_AGE_SECONDS = 30 * 60        # primera evaluación a partir de 30 min
MAX_EVAL_AGE_SECONDS = 24 * 60 * 60   # tras 24h se cierra como terminal aunque no toque SL/TP


class SignalMemory:

    def __init__(self, path: str = MEMORY_PATH):
        self._path = path
        self._lock = threading.Lock()
        self._data: dict = self._load()

    def _load(self) -> dict:
        if os.path.exists(self._path):
            try:
                with open(self._path, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save(self):
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self._path)

    def record_signal(self, symbol: str, signal: dict, price: float):
        """Guarda una señal junto al precio de mercado del momento."""
        if not price:
            return
        with self._lock:
            records = self._data.setdefault(symbol, [])
            records.append({
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "action": signal.get("action", "HOLD"),
                "confidence": signal.get("confidence", 0),
                "price": price,
                "stop_loss": signal.get("stop_loss") or None,
                "take_profit": signal.get("take_profit") or None,
                "outcome": None,    # provisional (favorable/adverso) hasta ser terminal
                "move_pct": None,
                "final": False,     # terminal: tocó SL/TP o superó MAX_EVAL_AGE_SECONDS
            })
            del records[:-MAX_RECORDS_PER_SYMBOL]
            self._save()

    @staticmethod
    def _is_final(rec: dict) -> bool:
        """¿El resultado de la señal es definitivo? Para registros antiguos sin
        el campo 'final', se considera definitivo si ya tenían un outcome."""
        final = rec.get("final")
        if final is None:
            return rec.get("outcome") is not None
        return bool(final)

    def evaluate_pending(self, symbol: str, current_price: float):
        """Reevalúa señales BUY/SELL no terminales contra el precio actual.

        El outcome provisional (favorable/adverso) se actualiza en cada ciclo, no
        se congela en la primera evaluación. Se vuelve terminal al tocar SL/TP o
        al superar MAX_EVAL_AGE_SECONDS, momento en que refleja el movimiento neto
        del periodo en lugar de un instante arbitrario."""
        if not current_price:
            return
        now = datetime.now()
        changed = False
        with self._lock:
            for rec in self._data.get(symbol, []):
                if rec["action"] not in ("BUY", "SELL") or self._is_final(rec):
                    continue
                try:
                    age = (now - datetime.fromisoformat(rec["timestamp"])).total_seconds()
                except ValueError:
                    continue
                if age < MIN_EVAL_AGE_SECONDS:
                    continue
                entry = rec["price"]
                direction = 1 if rec["action"] == "BUY" else -1
                move_pct = direction * (current_price - entry) / entry * 100

                sl, tp = rec.get("stop_loss"), rec.get("take_profit")
                if tp and direction * (current_price - tp) >= 0:
                    outcome, final = "TP alcanzado", True
                elif sl and direction * (sl - current_price) >= 0:
                    outcome, final = "SL tocado", True
                else:
                    outcome = "favorable" if move_pct > 0 else "adverso"
                    final = age >= MAX_EVAL_AGE_SECONDS

                new_move = round(move_pct, 3)
                if (rec.get("outcome") != outcome or rec.get("move_pct") != new_move
                        or rec.get("final") != final):
                    rec["outcome"] = outcome
                    rec["move_pct"] = new_move
                    rec["final"] = final
                    changed = True
            if changed:
                self._save()

    def get_summary(self, symbol: str, last_n: int = 5) -> str:
        """Resumen legible de las últimas señales evaluadas, para el prompt."""
        with self._lock:
            evaluated = [r for r in self._data.get(symbol, []) if r["outcome"] is not None]
        if not evaluated:
            return ""
        recent = evaluated[-last_n:]
        wins = sum(1 for r in recent if r["outcome"] in ("favorable", "TP alcanzado"))
        lines = [f"Aciertos recientes: {wins}/{len(recent)}"]
        for r in recent:
            ts = r["timestamp"][5:16].replace("T", " ")
            lines.append(
                f"- {ts} {r['action']} @ {r['price']} (conf {r['confidence']:.0%}) "
                f"-> {r['outcome']} ({r['move_pct']:+.2f}%)"
            )
        return "\n".join(lines)

    def get_last_signal(self, symbol: str) -> Optional[dict]:
        with self._lock:
            records = self._data.get(symbol, [])
            return dict(records[-1]) if records else None

    def get_performance(self, symbol: str, last_n: int = 10) -> dict:
        """Métricas de rendimiento sobre las últimas señales evaluadas.

        Base cuantitativa para que el orquestador decida cómo ajustar los
        parámetros de un agente. Solo cuenta señales BUY/SELL con resultado
        terminal (SL/TP tocado o ventana de evaluación expirada), no las que aún
        están abiertas con un outcome provisional.
        """
        with self._lock:
            evaluated = [
                r for r in self._data.get(symbol, [])
                if r["action"] in ("BUY", "SELL") and self._is_final(r)
            ]
        recent = evaluated[-last_n:]
        total = len(recent)
        if total == 0:
            return {"samples": 0, "win_rate": 0.0, "sl_hit_rate": 0.0,
                    "tp_hit_rate": 0.0, "avg_move_pct": 0.0}

        wins = sum(1 for r in recent if r["outcome"] in ("favorable", "TP alcanzado"))
        sl_hits = sum(1 for r in recent if r["outcome"] == "SL tocado")
        tp_hits = sum(1 for r in recent if r["outcome"] == "TP alcanzado")
        moves = [r["move_pct"] for r in recent if r.get("move_pct") is not None]

        return {
            "samples": total,
            "win_rate": round(wins / total, 3),
            "sl_hit_rate": round(sl_hits / total, 3),
            "tp_hit_rate": round(tp_hits / total, 3),
            "avg_move_pct": round(sum(moves) / len(moves), 3) if moves else 0.0,
        }
