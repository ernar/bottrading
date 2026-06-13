"""Orquestador de agentes.

De momento coordina la ejecución: en cada ciclo recorre los agentes activos,
les pide su análisis y ejecuta las señales válidas. Mantiene además un registro
de rendimiento por agente que servirá de base para la fase de optimización
(ajuste automático de parámetros / modelo de cada agente).
"""
import os
import threading
import time
from datetime import date, datetime

from core.state import bot_state
from core.bot_state import Trade
from core.logger import log_trade
from core.trade_metrics import calc_trade_metrics
from agents.base_agent import AgentParams


def _pos_get(pos, *fields, default=None):
    """Lee el primer campo presente de una posición, sea Position (pydantic/MT5)
    o dict (MT4)."""
    for f in fields:
        if isinstance(pos, dict):
            if pos.get(f) is not None:
                return pos[f]
        else:
            v = getattr(pos, f, None)
            if v is not None:
                return v
    return default


def _pos_to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _pos_direction(pos) -> str:
    """Normaliza la dirección. MT5 da 'BUY'/'SELL'; MT4 da type entero (0=BUY,1=SELL)."""
    d = _pos_get(pos, "direction", "type")
    if d is None:
        return "?"
    s = str(d).upper()
    if s in ("BUY", "SELL"):
        return s
    if s in ("0", "0.0"):
        return "BUY"
    if s in ("1", "1.0"):
        return "SELL"
    return s


# Límites de seguridad para que la optimización no deje a un agente en una
# configuración absurda. (min, max)
PARAM_BOUNDS = {
    "min_confidence": (0.50, 0.85),
    "min_rr": (1.0, 3.0),
    "atr_sl_mult": (1.0, 3.5),
    "atr_tp_mult": (1.5, 5.0),
}
MIN_SAMPLES_TO_TUNE = 5   # nº mínimo de señales evaluadas para ajustar

# Con el símbolo en su máximo de posiciones, el análisis se espacia a este
# intervalo (en vez de cada ciclo) para no gastar llamadas al LLM sin poder
# operar. Una señal de confianza >= 90% se salta igualmente el límite.
AT_MAX_ANALYSIS_INTERVAL = 15 * 60


def _clamp(value: float, key: str) -> float:
    lo, hi = PARAM_BOUNDS[key]
    return round(min(max(value, lo), hi), 2)


def tune_params(params: AgentParams, perf: dict, hold_rate: float) -> tuple:
    """Deriva nuevos parámetros a partir del rendimiento observado.

    Reglas explicables (no ML):
    - Win rate bajo  -> más selectivo (sube confianza mínima y R:R).
    - Win rate alto pero demasiados HOLD -> afloja confianza para capturar más.
    - Muchos SL tocados -> stops barridos por ruido: amplía SL y TP (mantiene R:R).
    - Casi ningún TP alcanzado con win rate decente -> objetivos demasiado
      lejanos: acerca el TP.

    Devuelve (nuevos_params, [lista de cambios legibles]).
    """
    if perf["samples"] < MIN_SAMPLES_TO_TUNE:
        return params, [f"datos insuficientes ({perf['samples']}/{MIN_SAMPLES_TO_TUNE} señales)"]

    min_conf = params.min_confidence
    min_rr = params.min_rr
    atr_sl = params.atr_sl_mult
    atr_tp = params.atr_tp_mult
    reasons = []

    if perf["win_rate"] < 0.40:
        min_conf += 0.05
        min_rr += 0.10
        reasons.append(f"win rate {perf['win_rate']:.0%} bajo -> +selectivo")
    elif perf["win_rate"] >= 0.65 and hold_rate > 0.60:
        min_conf -= 0.05
        reasons.append(f"win rate {perf['win_rate']:.0%} alto y {hold_rate:.0%} holds -> capturar más")

    if perf["sl_hit_rate"] > 0.40:
        atr_sl += 0.30
        atr_tp += 0.30
        reasons.append(f"{perf['sl_hit_rate']:.0%} SL tocados -> ampliar stops")

    if perf["tp_hit_rate"] < 0.15 and perf["win_rate"] >= 0.50:
        atr_tp -= 0.30
        reasons.append(f"solo {perf['tp_hit_rate']:.0%} TP alcanzados -> acercar objetivo")

    new_params = params.model_copy(update={
        "min_confidence": _clamp(min_conf, "min_confidence"),
        "min_rr": _clamp(min_rr, "min_rr"),
        "atr_sl_mult": _clamp(atr_sl, "atr_sl_mult"),
        "atr_tp_mult": _clamp(atr_tp, "atr_tp_mult"),
    })
    return new_params, (reasons or ["rendimiento dentro de rango, sin cambios"])


def _diff_params(old: AgentParams, new: AgentParams) -> list:
    """Lista legible de los campos que cambiaron."""
    changes = []
    for key in ("min_confidence", "min_rr", "atr_sl_mult", "atr_tp_mult"):
        o, n = getattr(old, key), getattr(new, key)
        if o != n:
            changes.append(f"{key}: {o} -> {n}")
    return changes


class _Spinner:
    """Spinner animado en un hilo aparte mientras se genera el análisis.

    A diferencia de un sleep fijo, gira de verdad durante toda la llamada al
    LLM (que puede tardar varios segundos) y limpia su línea al terminar.
    Se usa como context manager: ``with _Spinner("..."):``.
    """

    FRAMES = ["|", "/", "-", "\\"]  # ASCII: se ve bien en cualquier consola Windows

    def __init__(self, message: str, interval: float = 0.12):
        self.message = message
        self.interval = interval
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
        return self

    def _spin(self):
        i = 0
        start = time.time()
        while not self._stop.is_set():
            frame = self.FRAMES[i % len(self.FRAMES)]
            elapsed = time.time() - start
            print(f"\r{self.message} {frame} ({elapsed:4.1f}s)", end="", flush=True)
            i += 1
            time.sleep(self.interval)

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread:
            self._thread.join()
        # Borra la línea del spinner para que no quede residuo.
        print(f"\r{' ' * (len(self.message) + 16)}\r", end="", flush=True)


class AgentOrchestrator:

    def __init__(self, agents: list, client, platform: str = "mt5",
                 optimize_every_cycles: int = 0):
        self.agents = agents
        self.client = client
        self.platform = platform
        # Cada cuántos ciclos auto-optimizar (0 = desactivado).
        self.optimize_every_cycles = optimize_every_cycles
        # Contadores por agente: base para optimizar.
        self.stats = {a.name: {"signals": 0, "trades": 0, "holds": 0} for a in agents}
        # Reporte de la última optimización (para exponer al dashboard).
        self.last_optimization = None
        self.last_optimization_at = None
        # Circuit breaker de pérdida diaria (0 = desactivado).
        self.max_daily_loss_pct = float(os.getenv("MAX_DAILY_LOSS_PCT", "0") or 0)
        self._risk_day = None          # día (ISO) cuyo equity inicial guardamos
        self._day_start_equity = None  # equity al primer ciclo del día
        self._risk_halted_day = None   # día en que ya saltó el breaker
        # Última instantánea de posiciones por símbolo {symbol: {ticket: snap}}:
        # base para detectar cierres y registrarlos en el historial.
        self._prev_positions: dict = {}
        # Momento del último análisis por símbolo, para espaciarlo al estar en
        # el máximo de posiciones abiertas.
        self._last_analysis_at: dict = {}

    # ----- Ejecución -----

    def run_forever(self, poll_seconds: int = 60):
        bot_state.set_bot_running(True)
        cycle = 0
        try:
            while True:
                account_info = self.client.get_account_info()
                if account_info:
                    bot_state.update_account(account_info)
                    self._check_daily_loss_guard(account_info)

                if not bot_state.bot_running:
                    time.sleep(5)
                    continue

                for agent in self.agents:
                    self._run_agent(agent)

                cycle += 1
                if self.optimize_every_cycles and cycle % self.optimize_every_cycles == 0:
                    self.optimize()

                time.sleep(poll_seconds)
        except KeyboardInterrupt:
            print("\n\nOrquestador detenido por el usuario.")

    def _check_daily_loss_guard(self, account_info: dict):
        """Kill-switch por pérdida diaria.

        Fija el equity al primer ciclo de cada día y, si en ciclos posteriores
        cae por debajo del límite configurado, pausa el bot (deja de abrir
        operaciones). Salta una sola vez por día; se rearma al cambiar de día.
        Un resume manual desde el dashboard lo anula para el resto del día (el
        usuario asume el control)."""
        if not self.max_daily_loss_pct:
            return
        equity = account_info.get("equity") or 0
        if equity <= 0:
            return

        today = date.today().isoformat()
        if self._risk_day != today:
            # Nuevo día: rearmar con el equity actual como referencia.
            self._risk_day = today
            self._day_start_equity = equity
            return

        baseline = self._day_start_equity or equity
        drawdown = (baseline - equity) / baseline
        if (drawdown >= self.max_daily_loss_pct
                and bot_state.bot_running
                and self._risk_halted_day != today):
            self._risk_halted_day = today
            bot_state.set_bot_running(False)
            print(f"\n{'!' * 50}")
            print(f"  CIRCUIT BREAKER: pérdida del día {drawdown:.1%} "
                  f">= límite {self.max_daily_loss_pct:.1%}")
            print(f"  Equity inicio del día: {baseline:.2f} -> actual: {equity:.2f}")
            print("  BOT PAUSADO. Revisa la cuenta antes de reanudar desde el dashboard.")
            print(f"{'!' * 50}")

    def _should_analyze_at_max(self, symbol: str) -> bool:
        """True si toca volver a analizar pese a estar en el máximo de posiciones
        (ha pasado AT_MAX_ANALYSIS_INTERVAL desde el último análisis del símbolo)."""
        last = self._last_analysis_at.get(symbol, 0)
        return (time.time() - last) >= AT_MAX_ANALYSIS_INTERVAL

    def _spread_points(self, symbol: str, tick) -> float:
        """Spread actual en puntos, o None si no se puede calcular."""
        if not tick:
            return None
        sym = self.client.get_symbol_info(symbol)
        point = getattr(sym, "point", 0) or 0
        if point <= 0:
            return None
        return (tick.ask - tick.bid) / point

    # ----- Historial de cierres -----

    def _detect_closed_trades(self, symbol: str, positions: list):
        """Compara las posiciones actuales con la instantánea previa del símbolo
        y registra como cerrada cualquiera que haya desaparecido."""
        current = {}
        for p in positions or []:
            ticket = _pos_get(p, "ticket")
            if ticket is not None:
                current[str(ticket)] = p

        prev = self._prev_positions.get(symbol, {})
        for ticket, snap in prev.items():
            if ticket not in current:
                self._record_closed_trade(symbol, snap)

        self._prev_positions[symbol] = {t: self._snapshot(p) for t, p in current.items()}

    @staticmethod
    def _snapshot(pos) -> dict:
        return {
            "direction": _pos_direction(pos),
            "volume": _pos_to_float(_pos_get(pos, "volume")),
            "open_price": _pos_to_float(_pos_get(pos, "open_price", "price_open")),
            "current_price": _pos_to_float(_pos_get(pos, "current_price")),
            "profit": _pos_to_float(_pos_get(pos, "profit")),
            "open_time": _pos_get(pos, "open_time"),
        }

    def _record_closed_trade(self, symbol: str, snap: dict):
        """Registra en el estado una posición que ya no aparece.

        El P/L es el último flotante observado antes de desaparecer (aprox. del
        realizado; el broker podría diferir por el último tick/slippage)."""
        open_iso, duration = "", None
        open_time = snap.get("open_time")
        if open_time:
            try:
                ot = datetime.fromtimestamp(int(open_time))
                open_iso = ot.isoformat()
                duration = int((datetime.now() - ot).total_seconds())
            except (ValueError, OSError, TypeError):
                pass
        exit_price = snap.get("current_price") or snap.get("open_price") or 0
        trade = Trade(
            symbol=symbol,
            action=snap.get("direction", "?"),
            entry_price=snap.get("open_price", 0.0),
            exit_price=exit_price or None,
            volume=snap.get("volume", 0.0),
            pnl=snap.get("profit", 0.0),
            open_time=open_iso,
            close_time=datetime.now().isoformat(),
            duration_seconds=duration,
        )
        bot_state.add_closed_trade(trade)
        print(f"  Cierre registrado: {trade.action} {symbol} | P/L≈{trade.pnl:.2f}")

    def _run_agent(self, agent):
        symbol = agent.symbol
        print(f"\n{'=' * 50}")
        print(f"  [{agent.name}] Analizando {symbol}...")

        tick = self.client.get_tick(symbol)
        if tick:
            print(f"  Precio: Ask={tick.ask} | Bid={tick.bid}")

        # Posiciones del símbolo: detectar cierres respecto al ciclo previo,
        # sincronizar estado y decidir si conviene analizar.
        positions = self.client.get_positions(symbol)
        self._detect_closed_trades(symbol, positions)
        bot_state.sync_positions(symbol, positions)

        # ----- Resumen de posiciones abiertas con profit no realizado -----
        if positions:
            total_profit = 0.0
            print(f"\n  📊 Posiciones abiertas en {symbol}:")
            for i, pos in enumerate(positions, 1):
                ticket = _pos_get(pos, "ticket", default="?")
                direction = _pos_direction(pos)
                volume = _pos_to_float(_pos_get(pos, "volume"))
                open_price = _pos_to_float(_pos_get(pos, "open_price", "price_open"))
                current_price = _pos_to_float(_pos_get(pos, "current_price"))
                profit = _pos_to_float(_pos_get(pos, "profit"))
                total_profit += profit
                print(f"    {i}. Ticket #{ticket} | {direction} {volume} lotes | "
                      f"Entry: {open_price} | Actual: {current_price} | "
                      f"P/L: ${profit:+.2f}")
            print(f"\n  💰 Profit no realizado total ({symbol}): ${total_profit:+.2f}")
        else:
            print(f"\n  ✅ No hay posiciones abiertas en {symbol}.")

        # Si el símbolo está en su máximo de posiciones, espaciar el análisis a
        # AT_MAX_ANALYSIS_INTERVAL: solo lo justifica una señal de confianza muy
        # alta (>=90%) que se salte el límite, así que no merece la pena consultar
        # al LLM cada ciclo.
        max_pos = agent.params.max_open_positions
        at_max = bool(max_pos) and len(positions) >= max_pos
        if at_max and not self._should_analyze_at_max(symbol):
            print(f"  {len(positions)} posiciones abiertas (máx {max_pos}); "
                  f"análisis aplazado (cada {AT_MAX_ANALYSIS_INTERVAL // 60} min salvo conf>=90%).")
            return

        with _Spinner("  Generando análisis"):
            signal = agent.analyze(self.client, platform=self.platform)
        self._last_analysis_at[symbol] = time.time()
        if not signal:
            print("  No se generó señal.")
            return

        self.stats[agent.name]["signals"] += 1
        bot_state.update_signal(signal)

        # Volumen real (fijo o por riesgo) para que las métricas mostradas y la
        # orden enviada usen el mismo lote.
        volume = agent.resolve_volume(self.client, signal)

        print(f"\n  Señal: {signal['action']} | Confianza: {signal['confidence']:.0%}")
        print(f"  Tendencia: {signal.get('trend', 'N/A')} | Riesgo: {signal.get('risk_level', 'N/A')}")
        if signal.get("entry"):
            print(f"  Entry: {signal['entry']} | SL: {signal['stop_loss']} | "
                  f"TP: {signal['take_profit']} | Lote: {volume}")
            metrics = calc_trade_metrics(
                self.client, symbol, signal["action"],
                signal["entry"], signal["stop_loss"], signal["take_profit"],
                volume,
                commission_per_lot=agent.config.commission_per_lot,
            )
            if metrics:
                print(f"  Profit potencial: +${metrics['net_profit']:.2f}  ({metrics['pips_tp']:.0f} pips)")
                print(f"  Pérdida potencial: -${metrics['net_loss']:.2f}  ({metrics['pips_sl']:.0f} pips)")
                print(f"  Comisión estimada: ${metrics['commission']:.2f} | R:R = 1:{metrics['rr']}")
        print(f"  Razón: {signal['reason']}")

        if signal["action"] == "HOLD":
            self.stats[agent.name]["holds"] += 1
            return

        # Contexto extra para la validación: spread actual (filtro de coste) y
        # nº de posiciones de TODA la cuenta (límite global, no por símbolo).
        spread_points = self._spread_points(symbol, tick)
        total_open = len(self.client.get_positions() or [])
        if not agent.validate(signal, positions, tick=self.client.get_tick(symbol),
                              spread_points=spread_points, total_open_positions=total_open):
            print("  Señal no validada para ejecución.")
            return

        result = self.client.place_order(
            symbol=symbol,
            volume=volume,
            order_type=signal["action"],
            stop_loss=signal.get("stop_loss") or None,
            take_profit=signal.get("take_profit") or None,
            comment=f"{agent.name}: {signal['reason'][:18]}",
        )
        if result and result.get("success"):
            print(f"  Orden ejecutada: ticket {result.get('order')} @ {result.get('price')}")
            self.stats[agent.name]["trades"] += 1
            log_trade(
                symbol=symbol,
                action=signal["action"],
                volume=volume,
                price=result.get("price") or signal.get("entry", 0),
                stop_loss=signal.get("stop_loss", 0),
                take_profit=signal.get("take_profit", 0),
                result=result,
                platform=self.platform,
            )
        elif result and result.get("timeout"):
            print("  [!] TIMEOUT esperando al EA: la orden NO se confirmó.")
            print("      La orden PUEDE haberse ejecutado igualmente. Revisa MT4")
            print("      antes de que el orquestador reintente en el próximo ciclo.")
        else:
            err = (result or {}).get("error") or (result or {}).get("comment") or "sin respuesta"
            print(f"  Error al ejecutar orden: {err}")

    # ----- Optimización -----

    def optimize(self, apply: bool = True) -> list:
        """Ajusta los parámetros de cada agente según su rendimiento real.

        Lee la memoria de señales evaluadas de cada agente, deriva nuevos
        parámetros con reglas explicables (ver tune_params) y, si apply=True,
        los aplica en caliente. Devuelve un reporte por agente.

        Pasa apply=False para una simulación (dry-run) sin modificar nada.
        """
        print(f"\n{'=' * 50}")
        print(f"  OPTIMIZACIÓN DE AGENTES{'  (dry-run)' if not apply else ''}")
        print("=" * 50)

        report = []
        for agent in self.agents:
            perf = agent.memory.get_performance(agent.symbol)
            stats = self.stats[agent.name]
            hold_rate = stats["holds"] / stats["signals"] if stats["signals"] else 0.0

            new_params, reasons = tune_params(agent.params, perf, hold_rate)
            changes = _diff_params(agent.params, new_params)

            print(f"\n  [{agent.name}] {agent.symbol}")
            print(f"    Rendimiento: {perf['samples']} señales | win {perf['win_rate']:.0%} | "
                  f"SL {perf['sl_hit_rate']:.0%} | TP {perf['tp_hit_rate']:.0%} | "
                  f"mov medio {perf['avg_move_pct']:+.2f}% | holds {hold_rate:.0%}")
            print(f"    Diagnóstico: {'; '.join(reasons)}")
            if changes:
                print(f"    Cambios: {', '.join(changes)}")
                if apply:
                    agent.apply_params(new_params)
            else:
                print("    Sin cambios.")

            report.append({
                "agent": agent.name,
                "symbol": agent.symbol,
                "performance": perf,
                "hold_rate": round(hold_rate, 3),
                "reasons": reasons,
                "changes": changes,
                "applied": bool(changes) and apply,
            })

        print("\n" + "=" * 50)
        if apply:
            self.last_optimization = report
            self.last_optimization_at = time.strftime("%Y-%m-%d %H:%M:%S")
        return report

    # ----- Cambio de modelo en caliente (desde el dashboard) -----

    def set_agent_model(self, name: str, provider: str, model: str) -> dict:
        """Cambia el provider/modelo LLM de un agente en caliente.

        `apply_params` reconstruye la estrategia cuando provider/modelo difieren,
        así que el cambio surte efecto en el siguiente análisis del agente.
        Devuelve el nuevo estado o lanza KeyError/ValueError si es inválido."""
        agent = next((a for a in self.agents if a.name == name), None)
        if agent is None:
            raise KeyError(name)
        provider = (provider or "").lower().strip()
        model = (model or "").strip()
        if not provider or not model:
            raise ValueError("provider y model son obligatorios")
        new_params = agent.params.model_copy(update={"provider": provider, "model": model})
        agent.apply_params(new_params)
        print(f"  [{name}] modelo cambiado a {provider.upper()}/{model}")
        return {"name": name, "provider": provider, "model": model}

    # ----- Exposición para el dashboard -----

    def agents_overview(self) -> dict:
        """Resumen de cada agente (config + stats de sesión + rendimiento de
        memoria) y la última optimización aplicada. Lo consume /api/agents."""
        agents = []
        for agent in self.agents:
            p = agent.params
            agents.append({
                "name": agent.name,
                "symbol": agent.symbol,
                "description": agent.description,
                "provider": p.provider,
                "model": p.model,
                "params": {
                    "min_confidence": p.min_confidence,
                    "min_rr": p.min_rr,
                    "atr_sl_mult": p.atr_sl_mult,
                    "atr_tp_mult": p.atr_tp_mult,
                    "lot_size": p.lot_size,
                },
                "stats": self.stats[agent.name],
                "performance": agent.memory.get_performance(agent.symbol),
            })
        return {
            "agents": agents,
            "optimize_every_cycles": self.optimize_every_cycles,
            "last_optimization": self.last_optimization,
            "last_optimization_at": self.last_optimization_at,
        }
