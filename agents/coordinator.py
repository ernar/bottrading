"""Coordinador "mesa de dirección" sobre los agentes especialistas.

Dos capas que cooperan:

- ``RiskBook`` (determinista): la tesorería. Calcula la economía de la cartera
  (equity, exposición por símbolo y total, presupuesto) e impone TOPES DUROS
  sobre lo que proponga el coordinador LLM. No depende de ningún LLM: es el
  guardarraíl que evita sobreexponerse pase lo que pase.

- ``CoordinatorAgent`` (LLM): el director. Dado el estado de cartera y las
  señales que le pasan los especialistas, decide go/no-go, prioridad y
  asignación de capital por símbolo, y qué hacer con las posiciones abiertas
  (mantener / reducir / cerrar). Sus números son PROPUESTAS: el RiskBook recorta
  después lo que exceda los límites. Si el LLM falla, cae a una decisión
  determinista (fail-safe), igual que el proveedor de noticias.
"""
import json
from typing import Optional

from core.models import BotConfig
from core.strategy import StrategyEngine
from agents.positions import _pos_get, _pos_to_float


class RiskBook:
    """Capa determinista de riesgo/capital. Fuente única de verdad de la
    economía de la cartera y guardarraíl de los límites duros."""

    def __init__(self, config: dict):
        self.max_total_exposure_pct = float(config.get("max_total_exposure_pct", 0.5))
        self.max_symbol_allocation_pct = float(config.get("max_symbol_allocation_pct", 0.4))
        self.can_close = bool(config.get("can_close", True))

    @staticmethod
    def _contract_size(client, symbol: str) -> float:
        """Tamaño de contrato del símbolo. MT5 raw expone `trade_contract_size`;
        el modelo SymbolInfo de MT4 expone `contract_size`. Fallback 1.0 (cripto)."""
        info = client.get_symbol_info(symbol)
        for attr in ("trade_contract_size", "contract_size"):
            v = getattr(info, attr, None)
            if v:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
        return 1.0

    def snapshot(self, client, agents: list, day_start_equity: float = None,
                 in_cooldown: bool = False) -> dict:
        """Estado económico de la cartera para el dashboard y el coordinador.

        Exposición total = margen usado / equity (la restricción real del bróker).
        Exposición por símbolo = nocional aproximado (volume × precio × contrato)
        sobre el equity, para repartir presupuesto."""
        account = client.get_account_info() or {}
        equity = float(account.get("equity") or 0.0)
        balance = float(account.get("balance") or 0.0)
        free_margin = float(account.get("free_margin") or 0.0)
        used_margin = float(account.get("used_margin") or 0.0)

        all_positions = client.get_positions() or []
        per_symbol: dict = {}
        for p in all_positions:
            sym = _pos_get(p, "symbol", default="?")
            vol = _pos_to_float(_pos_get(p, "volume"))
            price = _pos_to_float(_pos_get(p, "current_price", "open_price", "price_open"))
            profit = _pos_to_float(_pos_get(p, "profit"))
            notional = vol * price * self._contract_size(client, sym)
            d = per_symbol.setdefault(sym, {"notional": 0.0, "profit": 0.0, "count": 0})
            d["notional"] += notional
            d["profit"] += profit
            d["count"] += 1

        total_exposure_pct = (used_margin / equity) if equity > 0 else 0.0
        daily_pnl_pct = None
        if day_start_equity and day_start_equity > 0:
            daily_pnl_pct = (equity - day_start_equity) / day_start_equity

        symbols = {}
        for agent in agents:
            sym = agent.symbol
            ps = per_symbol.get(sym, {"notional": 0.0, "profit": 0.0, "count": 0})
            used_pct = (ps["notional"] / equity) if equity > 0 else 0.0
            symbols[sym] = {
                "exposure_notional": round(ps["notional"], 2),
                "exposure_pct": round(used_pct, 4),
                "floating_pnl": round(ps["profit"], 2),
                "open_positions": ps["count"],
                "max_allocation_pct": self.max_symbol_allocation_pct,
                "remaining_pct": round(max(0.0, self.max_symbol_allocation_pct - used_pct), 4),
            }

        return {
            "equity": round(equity, 2),
            "balance": round(balance, 2),
            "free_margin": round(free_margin, 2),
            "used_margin": round(used_margin, 2),
            "total_exposure_pct": round(total_exposure_pct, 4),
            "max_total_exposure_pct": self.max_total_exposure_pct,
            "max_symbol_allocation_pct": self.max_symbol_allocation_pct,
            "daily_pnl_pct": round(daily_pnl_pct, 4) if daily_pnl_pct is not None else None,
            "in_cooldown": bool(in_cooldown),
            "open_positions_total": len(all_positions),
            "can_close": self.can_close,
            "symbols": symbols,
        }

    def clamp(self, decisions: list, snapshot: dict) -> list:
        """Aplica los topes duros a las decisiones del coordinador.

        Devuelve una lista nueva; cada decisión lleva un campo ``clamp`` legible
        con el ajuste aplicado (o "" si no se tocó). Reglas:
        - la asignación nunca supera ``max_symbol_allocation_pct``;
        - en cooldown por pérdida diaria no se aprueban entradas;
        - si la exposición total ya alcanza el tope, no se aprueban entradas;
        - si el símbolo ya está en su tope de asignación, no se aprueban entradas;
        - close/reduce solo si ``can_close`` está activado.
        """
        symbols = snapshot.get("symbols", {})
        total_exposure = snapshot.get("total_exposure_pct", 0.0) or 0.0
        max_total = self.max_total_exposure_pct
        in_cooldown = snapshot.get("in_cooldown", False)

        out = []
        for raw in decisions:
            d = dict(raw)
            notes = []
            sym = d.get("symbol")
            approve = bool(d.get("approve"))
            action = str(d.get("position_action", "hold") or "hold").lower()
            alloc = float(d.get("allocation_pct") or 0.0)

            if alloc < 0:
                alloc = 0.0
            if alloc > self.max_symbol_allocation_pct:
                notes.append(f"asignación {alloc:.0%}->{self.max_symbol_allocation_pct:.0%} (tope símbolo)")
                alloc = self.max_symbol_allocation_pct

            if approve and in_cooldown:
                notes.append("cooldown pérdida diaria: entrada vetada")
                approve = False

            if approve and total_exposure >= max_total:
                notes.append(f"exposición total {total_exposure:.0%} >= tope {max_total:.0%}: entrada vetada")
                approve = False

            sym_info = symbols.get(sym, {})
            if approve and sym_info.get("remaining_pct", 1.0) <= 0:
                notes.append("símbolo en su tope de asignación: entrada vetada")
                approve = False

            if action in ("close", "reduce") and not self.can_close:
                notes.append("cierre desactivado (COORDINATOR_CAN_CLOSE=false)")
                action = "hold"

            d["approve"] = approve
            d["allocation_pct"] = round(alloc, 4)
            d["position_action"] = action
            d["clamp"] = "; ".join(notes)
            out.append(d)
        return out


COORDINATOR_SYSTEM_PROMPT = """Eres el director de riesgo y capital de una mesa de trading (una \
"empresa de broker"). Por debajo tienes agentes especialistas, uno por símbolo, que ya han \
analizado su mercado y te proponen una señal (buy/sell/hold con su confianza y niveles). Tu \
trabajo NO es volver a analizar indicadores: es decidir, a nivel de CARTERA, en qué símbolos \
conviene entrar ahora, con qué prioridad y cuánto capital asignar, controlando la exposición \
global para no sobreexponerte y buscando el mejor retorno ajustado al riesgo.

Reglas:
- Decide solo sobre los símbolos que te llegan. Para cada uno indica: approve (true/false),
  priority (1 = más prioritario), allocation_pct (fracción del equity a asignar, entre 0 y 1) y
  position_action sobre las posiciones ABIERTAS de ese símbolo: "hold" (no tocar), "reduce"
  (recortar exposición) o "close" (cerrar).
- Reparte el capital, no lo concentres todo en un símbolo. No apruebes entradas que disparen la
  exposición total por encima de lo razonable.
- Prioriza señales de mayor confianza y mejor relación riesgo/beneficio, y los agentes con mejor
  rendimiento reciente.
- Si la cartera ya está muy expuesta o en pérdidas del día, sé conservador: menos entradas y
  considera reduce/close en lo que vaya en contra.
- Una señal "hold" del especialista normalmente NO se aprueba como entrada nueva.
- Tus números son propuestas: una capa de riesgo posterior recortará lo que exceda los límites
  duros. Aun así, respeta los topes que aparecen en el contexto.

Responde SOLO con JSON válido, sin texto adicional:
{
  "rationale": "razón global breve de tus decisiones de cartera",
  "decisions": [
    {"symbol": "BTCUSD", "approve": true, "priority": 1, "allocation_pct": 0.25,
     "position_action": "hold", "reason": "explicación breve"}
  ]
}"""


class CoordinatorAgent:
    """Meta-agente LLM que coordina a los especialistas a nivel de cartera."""

    def __init__(self, provider: str, model: str, risk_book: RiskBook,
                 temperature: float = 0.2, debug_mode: bool = True):
        self.provider = provider
        self.model = model
        self.risk_book = risk_book
        self.debug_mode = debug_mode
        config = BotConfig(model=model, debug_mode=debug_mode)
        self.engine = StrategyEngine(config, provider=provider, temperature=temperature)
        self.last_rationale = ""

    # ----- API principal -----

    def decide(self, snapshot: dict, signals: dict, agents_overview: dict,
               news_context: str = "") -> dict:
        """Devuelve ``{'rationale': str, 'decisions': [...]}`` ya acotado por el
        RiskBook. ``signals`` es ``{symbol: signal_dict}`` de este ciclo. Si el
        LLM falla o el JSON es inválido, cae a una decisión determinista."""
        raw = None
        if signals or snapshot.get("open_positions_total"):
            raw = self.engine.chat_json(
                COORDINATOR_SYSTEM_PROMPT,
                self._build_user_prompt(snapshot, signals, agents_overview, news_context),
            )
        parsed = self._parse(raw) if raw else None
        if parsed is None:
            rationale = "fallback determinista (LLM no disponible o respuesta inválida)"
            decisions = self._fallback(signals)
        else:
            rationale, decisions = parsed

        clamped = self.risk_book.clamp(decisions, snapshot)
        self.last_rationale = rationale
        return {"rationale": rationale, "decisions": clamped}

    # ----- Prompt -----

    def _build_user_prompt(self, snapshot: dict, signals: dict,
                           agents_overview: dict, news_context: str) -> str:
        lines = ["=== ESTADO DE CARTERA ==="]
        lines.append(f"Equity: {snapshot['equity']} | Balance: {snapshot['balance']} | "
                     f"Margen libre: {snapshot['free_margin']}")
        lines.append(f"Exposición total: {snapshot['total_exposure_pct']:.1%} "
                     f"(tope {snapshot['max_total_exposure_pct']:.0%})")
        lines.append(f"Tope de asignación por símbolo: {snapshot['max_symbol_allocation_pct']:.0%}")
        if snapshot.get("daily_pnl_pct") is not None:
            lines.append(f"P/L del día: {snapshot['daily_pnl_pct']:+.2%}")
        if snapshot.get("in_cooldown"):
            lines.append("ATENCIÓN: cooldown por pérdida diaria activo (no abrir entradas nuevas).")

        perf_by_symbol = {a["symbol"]: a for a in agents_overview.get("agents", [])}

        lines.append("\n=== SÍMBOLOS (señal del especialista + exposición) ===")
        for sym, sig in signals.items():
            si = snapshot.get("symbols", {}).get(sym, {})
            lines.append(f"\n[{sym}]")
            lines.append(f"  Señal: {sig.get('action')} | conf {self._pct(sig.get('confidence'))} | "
                         f"trend {sig.get('trend')} | riesgo {sig.get('risk_level')}")
            if sig.get("entry"):
                lines.append(f"  Niveles: entry {sig.get('entry')} SL {sig.get('stop_loss')} "
                             f"TP {sig.get('take_profit')}")
            reason = str(sig.get("reason", ""))[:200]
            if reason:
                lines.append(f"  Razón especialista: {reason}")
            lines.append(f"  Exposición actual: {self._pct(si.get('exposure_pct'))} "
                         f"({si.get('open_positions', 0)} pos, "
                         f"P/L flotante {si.get('floating_pnl', 0):+.2f}) | "
                         f"margen para asignar: {self._pct(si.get('remaining_pct'))}")
            a = perf_by_symbol.get(sym)
            if a and a.get("performance"):
                p = a["performance"]
                lines.append(f"  Rendimiento agente: win {self._pct(p.get('win_rate'))} "
                             f"sobre {p.get('samples', 0)} señales")

        if news_context:
            lines.append(f"\n=== NOTICIAS / MACRO ===\n{news_context[:1500]}")

        lines.append("\nDecide la asignación de cartera. Responde solo el JSON.")
        return "\n".join(lines)

    @staticmethod
    def _pct(value) -> str:
        try:
            return f"{float(value):.0%}"
        except (TypeError, ValueError):
            return "n/a"

    # ----- Parseo / fallback -----

    @staticmethod
    def _to_float(value, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_int(value, default: int = 99) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _parse(self, raw: str):
        """Extrae ``(rationale, decisions)`` del texto del LLM, o None si falla."""
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start == -1 or end == 0:
            return None
        try:
            data = json.loads(raw[start:end])
        except json.JSONDecodeError:
            return None
        decisions_raw = data.get("decisions")
        if not isinstance(decisions_raw, list):
            return None
        decisions = []
        for d in decisions_raw:
            if not isinstance(d, dict) or not d.get("symbol"):
                continue
            decisions.append({
                "symbol": str(d["symbol"]),
                "approve": bool(d.get("approve", False)),
                "priority": self._to_int(d.get("priority"), 99),
                "allocation_pct": self._to_float(d.get("allocation_pct"), 0.0),
                "position_action": str(d.get("position_action", "hold") or "hold").lower(),
                "reason": str(d.get("reason", "")),
            })
        return str(data.get("rationale", "")), decisions

    def _fallback(self, signals: dict) -> list:
        """Decisión determinista cuando no hay LLM: aprueba las señales
        accionables con reparto igual del capital (acotado por el tope)."""
        actionable = [s for s in signals.values()
                      if str(s.get("action", "")).upper() in ("BUY", "SELL")]
        n = len(actionable) or 1
        alloc = min(self.risk_book.max_symbol_allocation_pct, 1.0 / n)
        decisions = []
        for i, sig in enumerate(actionable, 1):
            decisions.append({
                "symbol": sig.get("symbol"),
                "approve": True,
                "priority": i,
                "allocation_pct": round(alloc, 4),
                "position_action": "hold",
                "reason": "aprobada por defecto (coordinador LLM no disponible)",
            })
        return decisions
