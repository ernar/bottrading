"""Memoria persistente de señales con evaluación de resultados.

Registra cada señal con el precio del momento; en ciclos posteriores
evalúa si el precio se movió a favor o en contra (o tocó SL/TP) y genera
un resumen de rendimiento por símbolo que se inyecta en el prompt para
que el modelo tenga feedback de sus señales recientes.

Persistencia en SQLite (tabla ``signal_memory`` vía ``core/db.py``). Antes era
un JSON por agente; ahora cada instancia opera sobre un ``scope`` (el nombre del
agente o "global") y comparte la misma tabla.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from core.db import ClosedTrade, SignalMemoryRecord, get_session, session_scope

MAX_RECORDS_PER_SYMBOL = 30
MIN_EVAL_AGE_SECONDS = 30 * 60        # primera evaluación a partir de 30 min
MAX_EVAL_AGE_SECONDS = 24 * 60 * 60   # tras 24h se cierra como terminal aunque no toque SL/TP


class SignalMemory:

    def __init__(self, scope: str = "global"):
        self._scope = scope

    # ----- Helpers internos -----

    def _records(self, session, symbol: str):
        """Registros del scope+símbolo ordenados por tiempo (asc)."""
        return session.scalars(
            select(SignalMemoryRecord)
            .where(SignalMemoryRecord.scope == self._scope,
                   SignalMemoryRecord.symbol == symbol)
            .order_by(SignalMemoryRecord.timestamp.asc(), SignalMemoryRecord.id.asc())
        ).all()

    @staticmethod
    def _is_final(rec) -> bool:
        """¿El resultado de la señal es definitivo? En la DB ``final`` siempre
        está presente (los registros antiguos se normalizan en la migración)."""
        return bool(rec.final)

    def record_signal(self, symbol: str, signal: dict, price: float):
        """Guarda una señal junto al precio de mercado del momento y poda los
        registros más antiguos para mantener MAX_RECORDS_PER_SYMBOL por símbolo."""
        if not price:
            return
        with session_scope() as session:
            session.add(SignalMemoryRecord(
                scope=self._scope,
                symbol=symbol,
                timestamp=datetime.now(),
                action=signal.get("action", "HOLD"),
                confidence=signal.get("confidence", 0) or 0,
                price=price,
                stop_loss=signal.get("stop_loss") or None,
                take_profit=signal.get("take_profit") or None,
                trade_id=signal.get("trade_id", "") or "",
                pnl_real=None,
                outcome=None,
                move_pct=None,
                final=False,
            ))
            session.flush()
            # Poda: conserva solo los MAX_RECORDS_PER_SYMBOL más recientes.
            ids = session.scalars(
                select(SignalMemoryRecord.id)
                .where(SignalMemoryRecord.scope == self._scope,
                       SignalMemoryRecord.symbol == symbol)
                .order_by(SignalMemoryRecord.timestamp.desc(),
                          SignalMemoryRecord.id.desc())
                .offset(MAX_RECORDS_PER_SYMBOL)
            ).all()
            if ids:
                for rec in session.scalars(
                    select(SignalMemoryRecord).where(SignalMemoryRecord.id.in_(ids))
                ).all():
                    session.delete(rec)

    def evaluate_pending(self, symbol: str, current_price: float):
        """Reevalúa señales BUY/SELL no terminales contra el precio actual.

        El outcome provisional (favorable/adverso) se actualiza en cada ciclo, no
        se congela en la primera evaluación. Se vuelve terminal al tocar SL/TP o
        al superar MAX_EVAL_AGE_SECONDS, momento en que refleja el movimiento neto
        del periodo en lugar de un instante arbitrario."""
        if not current_price:
            return
        now = datetime.now()
        with session_scope() as session:
            for rec in self._records(session, symbol):
                if rec.action not in ("BUY", "SELL") or self._is_final(rec):
                    continue
                age = (now - rec.timestamp).total_seconds()
                if age < MIN_EVAL_AGE_SECONDS:
                    continue
                entry = rec.price
                direction = 1 if rec.action == "BUY" else -1
                move_pct = direction * (current_price - entry) / entry * 100

                sl, tp = rec.stop_loss, rec.take_profit
                if tp and direction * (current_price - tp) >= 0:
                    outcome, final = "TP alcanzado", True
                elif sl and direction * (sl - current_price) >= 0:
                    outcome, final = "SL tocado", True
                else:
                    outcome = "favorable" if move_pct > 0 else "adverso"
                    final = age >= MAX_EVAL_AGE_SECONDS

                new_move = round(move_pct, 3)
                if (rec.outcome != outcome or rec.move_pct != new_move
                        or rec.final != final):
                    rec.outcome = outcome
                    rec.move_pct = new_move
                    rec.final = final

    def get_summary(self, symbol: str, last_n: int = 5) -> str:
        """Resumen legible de las últimas señales evaluadas, para el prompt."""
        session = get_session()
        try:
            evaluated = [r for r in self._records(session, symbol)
                         if r.outcome is not None]
        finally:
            session.close()
        if not evaluated:
            return ""
        recent = evaluated[-last_n:]
        wins = sum(1 for r in recent if r.outcome in ("favorable", "TP alcanzado"))
        lines = [f"Aciertos recientes: {wins}/{len(recent)}"]
        for r in recent:
            ts = r.timestamp.strftime("%m-%d %H:%M")
            lines.append(
                f"- {ts} {r.action} @ {r.price} (conf {r.confidence:.0%}) "
                f"-> {r.outcome} ({(r.move_pct or 0):+.2f}%)"
            )
        return "\n".join(lines)

    def get_last_signal(self, symbol: str) -> Optional[dict]:
        session = get_session()
        try:
            records = self._records(session, symbol)
            if not records:
                return None
            r = records[-1]
            return {
                "timestamp": r.timestamp.isoformat(timespec="seconds"),
                "action": r.action,
                "confidence": r.confidence,
                "price": r.price,
                "stop_loss": r.stop_loss,
                "take_profit": r.take_profit,
                "trade_id": r.trade_id,
                "pnl_real": r.pnl_real,
                "outcome": r.outcome,
                "move_pct": r.move_pct,
                "final": r.final,
            }
        finally:
            session.close()

    def get_performance(self, symbol: str, last_n: int = 10) -> dict:
        """Métricas de rendimiento sobre las últimas señales evaluadas.

        Solo cuenta señales BUY/SELL con resultado terminal (SL/TP tocado o
        ventana de evaluación expirada), no las que aún están abiertas con un
        outcome provisional."""
        session = get_session()
        try:
            evaluated = [
                r for r in self._records(session, symbol)
                if r.action in ("BUY", "SELL") and self._is_final(r)
            ]
        finally:
            session.close()
        recent = evaluated[-last_n:]
        total = len(recent)
        if total == 0:
            return {"samples": 0, "win_rate": 0.0, "sl_hit_rate": 0.0,
                    "tp_hit_rate": 0.0, "avg_move_pct": 0.0}

        wins = sum(1 for r in recent if r.outcome in ("favorable", "TP alcanzado"))
        sl_hits = sum(1 for r in recent if r.outcome == "SL tocado")
        tp_hits = sum(1 for r in recent if r.outcome == "TP alcanzado")
        moves = [r.move_pct for r in recent if r.move_pct is not None]

        return {
            "samples": total,
            "win_rate": round(wins / total, 3),
            "sl_hit_rate": round(sl_hits / total, 3),
            "tp_hit_rate": round(tp_hits / total, 3),
            "avg_move_pct": round(sum(moves) / len(moves), 3) if moves else 0.0,
        }

    def _sync_pnl_real(self):
        """Sincroniza pnl_real de las señales del scope con los cierres en DB
        (mismo símbolo+acción y entrada ≈ precio registrado)."""
        with session_scope() as session:
            pending = session.scalars(
                select(SignalMemoryRecord).where(
                    SignalMemoryRecord.scope == self._scope,
                    SignalMemoryRecord.pnl_real.is_(None),
                    SignalMemoryRecord.trade_id != "",
                )
            ).all()
            for rec in pending:
                ct = session.scalars(
                    select(ClosedTrade).where(
                        ClosedTrade.symbol == rec.symbol,
                        ClosedTrade.action == rec.action,
                    )
                ).all()
                for c in ct:
                    if c.entry_price is not None and rec.price is not None \
                            and abs(c.entry_price - rec.price) < 1e-10:
                        rec.pnl_real = float(c.pnl or 0)
                        rec.outcome = "ganador" if rec.pnl_real > 0 else "perdedor"
                        rec.final = True
                        break
