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
from agents.positions import _pos_get, _pos_to_float, _pos_direction


class RiskBook:
    """Capa determinista de riesgo/capital. Fuente única de verdad de la
    economía de la cartera y guardarraíl de los límites duros."""

    def __init__(self, config: dict):
        self.max_total_exposure_pct = float(config.get("max_total_exposure_pct", 0.5))
        self.max_symbol_allocation_pct = float(config.get("max_symbol_allocation_pct", 0.4))
        self.can_close = bool(config.get("can_close", True))
        # Control de concentración direccional / reversión de tendencia.
        self.max_net_direction_pct = float(config.get("max_net_direction_pct", 0.6))
        self.reversal_drawdown_pct = float(config.get("reversal_drawdown_pct", 0.015))
        self.max_symbol_loss_pct = float(config.get("max_symbol_loss_pct", 0.0))

    # ----- Helpers de dirección (estáticos, reutilizados por el prompt) -----

    @staticmethod
    def _trend_dir(trend) -> Optional[str]:
        """Normaliza la tendencia del especialista a LONG/SHORT (o None si es
        lateral/desconocida). El esquema del LLM usa bullish|bearish|sideways."""
        t = str(trend or "").strip().lower()
        if t in ("bullish", "alcista", "up", "long"):
            return "LONG"
        if t in ("bearish", "bajista", "down", "short"):
            return "SHORT"
        return None

    @staticmethod
    def _side_to_net(side) -> Optional[str]:
        """BUY->LONG, SELL->SHORT (None en otro caso)."""
        s = str(side or "").upper()
        if s == "BUY":
            return "LONG"
        if s == "SELL":
            return "SHORT"
        return None

    @staticmethod
    def _net_to_side(net_direction) -> Optional[str]:
        """LONG->BUY, SHORT->SELL (None si FLAT). Es el lado del libro a tratar."""
        if net_direction == "LONG":
            return "BUY"
        if net_direction == "SHORT":
            return "SELL"
        return None

    # Orden de "fuerza" de una acción sobre posiciones abiertas: una guardia
    # determinista puede subir la acción pero nunca bajarla.
    _ACTION_RANK = {"hold": 0, "hedge": 1, "reduce": 2, "close": 3}

    @classmethod
    def _stronger_action(cls, a: str, b: str) -> str:
        return a if cls._ACTION_RANK.get(a, 0) >= cls._ACTION_RANK.get(b, 0) else b

    @staticmethod
    def _contract_size(client, symbol: str) -> float:
        """Tamaño de contrato del símbolo. MT4 expone `trade_contract_size`
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
        hedging = bool(account.get("hedging", False))

        all_positions = client.get_positions() or []
        per_symbol: dict = {}
        for p in all_positions:
            sym = _pos_get(p, "symbol", default="?")
            vol = _pos_to_float(_pos_get(p, "volume"))
            price = _pos_to_float(_pos_get(p, "current_price", "open_price", "price_open"))
            profit = _pos_to_float(_pos_get(p, "profit"))
            direction = _pos_direction(p)
            notional = vol * price * self._contract_size(client, sym)
            d = per_symbol.setdefault(sym, {
                "notional": 0.0, "profit": 0.0, "count": 0,
                "long_notional": 0.0, "short_notional": 0.0,
                "long_vol": 0.0, "short_vol": 0.0,
                "long_count": 0, "short_count": 0,
            })
            d["notional"] += notional
            d["profit"] += profit
            d["count"] += 1
            if direction == "BUY":
                d["long_notional"] += notional
                d["long_vol"] += vol
                d["long_count"] += 1
            elif direction == "SELL":
                d["short_notional"] += notional
                d["short_vol"] += vol
                d["short_count"] += 1

        total_exposure_pct = (used_margin / equity) if equity > 0 else 0.0
        daily_pnl_pct = None
        if day_start_equity and day_start_equity > 0:
            daily_pnl_pct = (equity - day_start_equity) / day_start_equity

        empty = {"notional": 0.0, "profit": 0.0, "count": 0,
                 "long_notional": 0.0, "short_notional": 0.0,
                 "long_vol": 0.0, "short_vol": 0.0, "long_count": 0, "short_count": 0}
        symbols = {}
        for agent in agents:
            sym = agent.symbol
            ps = per_symbol.get(sym, empty)
            used_pct = (ps["notional"] / equity) if equity > 0 else 0.0
            # Sesgo neto: nocional de largos - cortos (con signo). FLAT si se netea.
            net_notional = ps["long_notional"] - ps["short_notional"]
            net_volume = round(ps["long_vol"] - ps["short_vol"], 6)
            eps = 1e-9
            if net_notional > eps:
                net_dir = "LONG"
            elif net_notional < -eps:
                net_dir = "SHORT"
            else:
                net_dir = "FLAT"
            symbols[sym] = {
                "exposure_notional": round(ps["notional"], 2),
                "exposure_pct": round(used_pct, 4),
                "gross_exposure_pct": round(used_pct, 4),
                "net_exposure_pct": round((net_notional / equity) if equity > 0 else 0.0, 4),
                "net_volume": net_volume,
                "net_direction": net_dir,
                "long_positions": ps["long_count"],
                "short_positions": ps["short_count"],
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
            "max_net_direction_pct": self.max_net_direction_pct,
            "reversal_drawdown_pct": self.reversal_drawdown_pct,
            "max_symbol_loss_pct": self.max_symbol_loss_pct,
            "hedging": hedging,
            "daily_pnl_pct": round(daily_pnl_pct, 4) if daily_pnl_pct is not None else None,
            "in_cooldown": bool(in_cooldown),
            "open_positions_total": len(all_positions),
            "can_close": self.can_close,
            "symbols": symbols,
        }

    def clamp(self, decisions: list, snapshot: dict, signals: dict = None) -> list:
        """Aplica los topes duros a las decisiones del coordinador.

        Devuelve una lista nueva; cada decisión lleva un campo ``clamp`` legible
        con el ajuste aplicado (o "" si no se tocó). ``signals`` (opcional,
        ``{symbol: signal}``) habilita las guardias que cruzan el sesgo abierto
        con la tendencia nueva; sin él, esas guardias no se evalúan.

        Reglas de entrada:
        - la asignación nunca supera ``max_symbol_allocation_pct``;
        - en cooldown por pérdida diaria no se aprueban entradas;
        - si la exposición total ya alcanza el tope, no se aprueban entradas;
        - si el símbolo ya está en su tope de asignación, no se aprueban entradas;
        - NO se apila en la dirección ya saturada (``max_net_direction_pct``).

        Guardias de posiciones abiertas (deterministas, solo si ``can_close``):
        - hard-stop por símbolo (``max_symbol_loss_pct``) -> close;
        - reversión: sesgo abierto vs tendencia nueva con pérdida flotante
          (``reversal_drawdown_pct``) -> reduce (o close si la pérdida es grande),
          fijando ``manage_direction`` al lado a cerrar;
        - ``hedge`` se degrada a ``reduce`` si la cuenta no es hedging o si la
          exposición total ya está en el tope; a ``hold`` si ``can_close`` está off.
        """
        symbols = snapshot.get("symbols", {})
        total_exposure = snapshot.get("total_exposure_pct", 0.0) or 0.0
        max_total = self.max_total_exposure_pct
        in_cooldown = snapshot.get("in_cooldown", False)
        hedging = bool(snapshot.get("hedging", False))
        equity = snapshot.get("equity", 0.0) or 0.0
        signals = signals or {}

        out = []
        for raw in decisions:
            d = dict(raw)
            notes = []
            sym = d.get("symbol")
            approve = bool(d.get("approve"))
            action = str(d.get("position_action", "hold") or "hold").lower()
            alloc = float(d.get("allocation_pct") or 0.0)
            manage_direction = d.get("manage_direction")

            sym_info = symbols.get(sym, {})
            net_direction = sym_info.get("net_direction", "FLAT")
            net_side = self._net_to_side(net_direction)
            net_exposure_pct = sym_info.get("net_exposure_pct", 0.0) or 0.0
            floating_pnl = sym_info.get("floating_pnl", 0.0) or 0.0
            open_positions = sym_info.get("open_positions", 0) or 0
            loss_pct = (-floating_pnl / equity) if (equity > 0 and floating_pnl < 0) else 0.0

            sig = signals.get(sym) or {}
            entry_side = str(sig.get("action", "")).upper()
            entry_net = self._side_to_net(entry_side)
            trend_dir = self._trend_dir(sig.get("trend"))

            # 1) Asignación: nunca supera el tope del símbolo.
            if alloc < 0:
                alloc = 0.0
            if alloc > self.max_symbol_allocation_pct:
                notes.append(f"asignación {alloc:.0%}->{self.max_symbol_allocation_pct:.0%} (tope símbolo)")
                alloc = self.max_symbol_allocation_pct

            # --- Guardias deterministas sobre las posiciones abiertas ---
            forced = False
            if open_positions > 0 and self.can_close:
                # 2) Hard-stop por símbolo (independiente de la tendencia).
                if self.max_symbol_loss_pct > 0 and loss_pct >= self.max_symbol_loss_pct:
                    action = self._stronger_action(action, "close")
                    manage_direction = net_side
                    forced = True
                    notes.append(f"hard-stop símbolo: pérdida {loss_pct:.1%} >= "
                                 f"{self.max_symbol_loss_pct:.1%} -> {action}")
                # 3) Reversión: conflicto sesgo-vs-tendencia con pérdida flotante.
                elif (self.reversal_drawdown_pct > 0 and trend_dir is not None
                        and net_direction in ("LONG", "SHORT")
                        and trend_dir != net_direction
                        and loss_pct >= self.reversal_drawdown_pct):
                    needed = "close" if loss_pct >= 2 * self.reversal_drawdown_pct else "reduce"
                    action = self._stronger_action(action, needed)
                    manage_direction = net_side
                    forced = True
                    notes.append(f"reversión: libro {net_direction} vs tendencia "
                                 f"{trend_dir.lower()}, pérdida {loss_pct:.1%} -> {action}")

            # 4) Cobertura (hedge): degradar según cuenta/exposición.
            if action == "hedge":
                if not self.can_close:
                    action = "hold"
                    notes.append("cobertura desactivada (COORDINATOR_CAN_CLOSE=false)")
                elif open_positions == 0 or net_direction == "FLAT":
                    action = "hold"
                    notes.append("sin posición neta que cubrir -> hold")
                elif not hedging:
                    action = "reduce"
                    manage_direction = net_side
                    notes.append("cuenta sin hedging: cobertura -> reduce")
                elif total_exposure >= max_total:
                    action = "reduce"
                    manage_direction = net_side
                    notes.append(f"exposición total {total_exposure:.0%} >= tope: cobertura -> reduce")
                else:
                    manage_direction = net_side  # lado a neutralizar (se abre el opuesto)

            # 5) close/reduce propuestos por el LLM requieren can_close.
            if action in ("close", "reduce") and not self.can_close:
                notes.append("cierre desactivado (COORDINATOR_CAN_CLOSE=false)")
                action = "hold"
                manage_direction = None

            # --- Vetos de entrada ---
            if approve and in_cooldown:
                notes.append("cooldown pérdida diaria: entrada vetada")
                approve = False

            if approve and total_exposure >= max_total:
                notes.append(f"exposición total {total_exposure:.0%} >= tope {max_total:.0%}: entrada vetada")
                approve = False

            if approve and sym_info.get("remaining_pct", 1.0) <= 0:
                notes.append("símbolo en su tope de asignación: entrada vetada")
                approve = False

            # Anti-apilamiento: no añadir más en la dirección neta ya saturada.
            if (approve and entry_net is not None and entry_net == net_direction
                    and abs(net_exposure_pct) >= self.max_net_direction_pct):
                notes.append(f"sesgo neto {net_direction} {abs(net_exposure_pct):.0%} >= "
                             f"tope {self.max_net_direction_pct:.0%}: no apilar")
                approve = False

            # En reversión/hard-stop forzado, no abrir en la dirección que se está cortando.
            if forced and approve and entry_net is not None and entry_net == net_direction:
                notes.append("posición en reversión: no se añade en esa dirección")
                approve = False

            d["approve"] = approve
            d["allocation_pct"] = round(alloc, 4)
            d["position_action"] = action
            if manage_direction:
                d["manage_direction"] = manage_direction
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
  (recortar exposición), "close" (cerrar todo) o "hedge" (cubrir abriendo en sentido contrario
  para neutralizar el riesgo SIN cerrar).
- Reparte el capital, no lo concentres todo en un símbolo. No apruebes entradas que disparen la
  exposición total por encima de lo razonable.
- Prioriza señales de mayor confianza y mejor relación riesgo/beneficio, y los agentes con mejor
  rendimiento reciente.
- CONCENTRACIÓN DIRECCIONAL: vigila el "sesgo abierto" (neto LONG/SHORT) de cada símbolo. NO
  apiles más posiciones en una dirección ya muy cargada. Si el libro está claramente sesgado en
  una dirección y la TENDENCIA del especialista gira en contra (marcado como ⚠ CONFLICTO), no
  añadas en esa dirección y protege la pérdida: usa "reduce"/"close" del lado perdedor, o "hedge"
  si conviene mantener las posiciones pero frenar la sangría.
- "hedge" solo tiene sentido si la cuenta permite cobertura (ver "Cobertura disponible" en el
  contexto); si no, la capa de riesgo lo convertirá en "reduce".
- Si la cartera ya está muy expuesta o en pérdidas del día, sé conservador: menos entradas y
  considera reduce/close/hedge en lo que vaya en contra.
- Una señal "hold" del especialista normalmente NO se aprueba como entrada nueva.
- Tus números son propuestas: una capa de riesgo posterior recortará lo que exceda los límites
  duros (incluidos topes de dirección neta y guardias de reversión). Aun así, respeta los topes
  que aparecen en el contexto.

Responde SOLO con JSON válido, sin texto adicional:
{
  "rationale": "razón global breve de tus decisiones de cartera",
  "decisions": [
    {"symbol": "BTCUSD", "approve": true, "priority": 1, "allocation_pct": 0.25,
     "position_action": "hold", "reason": "explicación breve"}
  ]
}
position_action admite: "hold" | "reduce" | "close" | "hedge"."""


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

        # Garantiza una decisión por símbolo con posiciones abiertas, para que las
        # guardias deterministas del RiskBook puedan actuar aunque el LLM lo omita.
        decisions = self._ensure_coverage(decisions, snapshot)
        clamped = self.risk_book.clamp(decisions, snapshot, signals)
        self.last_rationale = rationale
        return {"rationale": rationale, "decisions": clamped}

    @staticmethod
    def _ensure_coverage(decisions: list, snapshot: dict) -> list:
        """Añade una decisión `hold` por cada símbolo con posiciones abiertas que
        no aparezca ya en `decisions`, para que el clamp pueda protegerlo."""
        covered = {d.get("symbol") for d in decisions}
        extra = []
        for sym, si in (snapshot.get("symbols") or {}).items():
            if sym not in covered and (si.get("open_positions", 0) or 0) > 0:
                extra.append({
                    "symbol": sym, "approve": False, "priority": 99,
                    "allocation_pct": 0.0, "position_action": "hold",
                    "reason": "(sin señal; gestión de posiciones abiertas)",
                })
        return decisions + extra

    # ----- Prompt -----

    def _build_user_prompt(self, snapshot: dict, signals: dict,
                           agents_overview: dict, news_context: str) -> str:
        lines = ["=== ESTADO DE CARTERA ==="]
        lines.append(f"Equity: {snapshot.get('equity', 0)} | "
                     f"Balance: {snapshot.get('balance', 0)} | "
                     f"Margen libre: {snapshot.get('free_margin', 0)}")
        lines.append(f"Exposición total: {snapshot.get('total_exposure_pct', 0):.1%} "
                     f"(tope {snapshot.get('max_total_exposure_pct', 0):.0%})")
        lines.append(f"Tope de asignación por símbolo: {snapshot.get('max_symbol_allocation_pct', 0):.0%}"
                     f" | Tope de dirección neta por símbolo: {snapshot.get('max_net_direction_pct', 0):.0%}")
        lines.append(f"Cobertura (hedge) disponible en la cuenta: {'sí' if snapshot.get('hedging') else 'no'}")
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
            nd = si.get("net_direction", "FLAT")
            lines.append(f"  Sesgo abierto: {si.get('long_positions', 0)}L / "
                         f"{si.get('short_positions', 0)}S · neto {nd} "
                         f"({self._pct(si.get('net_exposure_pct'))})")
            trend_dir = RiskBook._trend_dir(sig.get("trend"))
            if nd in ("LONG", "SHORT") and trend_dir and trend_dir != nd:
                lines.append(f"  ⚠ CONFLICTO: libro {nd} vs tendencia {sig.get('trend')} "
                             f"-> considera reduce/close/hedge del lado {nd}.")
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
