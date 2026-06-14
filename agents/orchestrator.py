"""Orquestador de agentes.

De momento coordina la ejecución: en cada ciclo recorre los agentes activos,
les pide su análisis y ejecuta las señales válidas. Mantiene además un registro
de rendimiento por agente que servirá de base para la fase de optimización
(ajuste automático de parámetros / modelo de cada agente).
"""
import os
import math
import threading
import time
from datetime import date, datetime

from core.state import bot_state
from core.bot_state import Trade
from core.logger import log_trade
from core.trade_metrics import calc_trade_metrics
from agents.base_agent import AgentParams
from agents.positions import _pos_get, _pos_to_float, _pos_direction


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

# Tras tocar el límite de pérdida diaria el bot NO se detiene: entra en cooldown,
# deja de abrir operaciones y espacia el análisis a este intervalo para no perder
# contexto/memoria mientras espera a que las posiciones abiertas se cierren.
RISK_COOLDOWN_ANALYSIS_INTERVAL = 15 * 60


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
                 optimize_every_cycles: int = 0, coordinator=None, risk_book=None):
        self.agents = agents
        self.client = client
        self.platform = platform
        # Cada cuántos ciclos auto-optimizar (0 = desactivado).
        self.optimize_every_cycles = optimize_every_cycles
        # Coordinador (mesa de dirección). Si es None, el orquestador usa la
        # ruta clásica por agente (comportamiento original, sin cartera global).
        self.coordinator = coordinator
        self.risk_book = risk_book
        # Contadores por agente: base para optimizar.
        self.stats = {a.name: {"signals": 0, "trades": 0, "holds": 0} for a in agents}
        # Reporte de la última optimización (para exponer al dashboard).
        self.last_optimization = None
        self.last_optimization_at = None
        # Última coordinación de cartera (para exponer al dashboard).
        self.last_coordination = None
        self.last_coordination_at = None
        # Control de pérdida diaria (0 = desactivado). Al tocarlo se entra en
        # cooldown (no se abren operaciones), no se detiene el bot.
        self.max_daily_loss_pct = float(os.getenv("MAX_DAILY_LOSS_PCT", "0") or 0)
        self._risk_day = None          # día (ISO) cuyo equity inicial guardamos
        self._day_start_equity = None  # equity al primer ciclo del día
        self._risk_cooldown_day = None  # día en el que se activó el cooldown
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

                if self.coordinator is not None:
                    # Ruta coordinada: recolectar -> coordinar -> ejecutar.
                    self._run_coordinated_cycle()
                else:
                    # Check global de posiciones abiertas antes de analizar:
                    # si ya estamos en el máximo global, saltar directamente al
                    # siguiente ciclo y ahorrar llamadas al LLM.
                    all_positions = self.client.get_positions() or []
                    max_global = max((a.params.max_open_positions for a in self.agents), default=0)
                    if bool(max_global) and len(all_positions) >= max_global:
                        symbols_profit = {}
                        for p in all_positions:
                            sym = _pos_get(p, "symbol", default="?")
                            profit = _pos_to_float(_pos_get(p, "profit"))
                            symbols_profit[sym] = symbols_profit.get(sym, 0.0) + profit
                        total_profit = sum(symbols_profit.values())
                        print(f"\n  ⏭️ Máximo global de posiciones ({max_global}) alcanzado.")
                        print(f"  💰 Profit no realizado total: ${total_profit:+.2f}")
                        for sym, prof in sorted(symbols_profit.items()):
                            print(f"     {sym}: ${prof:+.2f}")
                        time.sleep(poll_seconds)
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
        """Control de pérdida diaria por *cooldown* (no detiene el bot).

        Fija el equity al primer ciclo de cada día y, si en ciclos posteriores
        cae por debajo del límite configurado, activa el cooldown: el bot sigue
        corriendo (actualiza estado, evalúa memoria y detecta cierres) pero deja
        de abrir nuevas operaciones y espacia el análisis, a la espera de que las
        posiciones abiertas se cierren. Se activa una vez por día y se rearma al
        cambiar de día."""
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
            self._risk_cooldown_day = None
            return

        baseline = self._day_start_equity or equity
        drawdown = (baseline - equity) / baseline
        if drawdown >= self.max_daily_loss_pct and self._risk_cooldown_day != today:
            self._risk_cooldown_day = today
            print(f"\n{'!' * 50}")
            print(f"  PÉRDIDA DIARIA: {drawdown:.1%} >= límite {self.max_daily_loss_pct:.1%}")
            print(f"  Equity inicio del día: {baseline:.2f} -> actual: {equity:.2f}")
            print("  COOLDOWN: no se abren nuevas operaciones; análisis espaciado.")
            print("  El bot sigue activo esperando el cierre de las posiciones abiertas.")
            print(f"{'!' * 50}")

    def _risk_cooldown_active(self) -> bool:
        """True si hoy se tocó el límite de pérdida diaria (cooldown vigente)."""
        return self._risk_cooldown_day == date.today().isoformat()

    def _throttled(self, symbol: str, interval: float) -> bool:
        """True si aún no han pasado `interval` segundos desde el último análisis
        del símbolo (sirve para espaciar análisis en máximo de posiciones/cooldown)."""
        return (time.time() - self._last_analysis_at.get(symbol, 0)) < interval

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

    def _print_positions_summary(self, symbol: str, positions: list):
        """Resumen de posiciones abiertas con su profit no realizado."""
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

    def _gather_signal(self, agent):
        """Prepara el símbolo (detecta cierres, sincroniza estado, respeta
        throttle/cooldown) y genera la señal del especialista. Devuelve el dict
        de señal (incluido HOLD) o None si no se analizó / no hubo señal.

        NO ejecuta órdenes ni imprime el detalle de la señal: eso es de la fase
        de ejecución (clásica o coordinada). Sirve a ambas rutas."""
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
        self._print_positions_summary(symbol, positions)

        # Estados que espacian el análisis y bloquean abrir nuevas operaciones:
        #  - cooldown por pérdida diaria: esperamos a que las posiciones abiertas
        #    se cierren sin asumir más riesgo, pero seguimos analizando de vez en
        #    cuando para no perder contexto/memoria.
        #  - símbolo en su máximo de posiciones: igual, salvo señal de conf >=90%.
        in_cooldown = self._risk_cooldown_active()
        max_pos = agent.params.max_open_positions
        at_max = bool(max_pos) and len(positions) >= max_pos
        if in_cooldown or at_max:
            interval = (RISK_COOLDOWN_ANALYSIS_INTERVAL if in_cooldown
                        else AT_MAX_ANALYSIS_INTERVAL)
            if self._throttled(symbol, interval):
                if in_cooldown:
                    print(f"  Cooldown por pérdida diaria; análisis aplazado "
                          f"(cada {interval // 60} min, esperando el cierre de posiciones).")
                else:
                    print(f"  {len(positions)} posiciones abiertas (máx {max_pos}); "
                          f"análisis aplazado (cada {interval // 60} min salvo conf>=90%).")
                return None

        with _Spinner("  Generando análisis"):
            signal = agent.analyze(self.client, platform=self.platform)
        self._last_analysis_at[symbol] = time.time()
        if not signal:
            print("  No se generó señal.")
            return None

        self.stats[agent.name]["signals"] += 1
        bot_state.update_signal(signal)
        if signal["action"] == "HOLD":
            self.stats[agent.name]["holds"] += 1
        return signal

    def _print_signal_details(self, agent, signal):
        """Imprime la señal y, si tiene niveles, sus métricas de profit/pérdida."""
        symbol = agent.symbol
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

    def _scale_volume(self, symbol: str, base_volume: float, scale: float) -> float:
        """Escala el lote base por `scale` (0..1) y lo redondea al step del
        símbolo, con suelo en el volumen mínimo. Permite que la asignación de
        capital del coordinador module el tamaño sin romper el step del bróker."""
        scale = max(0.1, min(1.0, scale))
        sym = self.client.get_symbol_info(symbol)
        vmin = getattr(sym, "volume_min", 0.01) or 0.01
        vstep = getattr(sym, "volume_step", 0.01) or 0.01
        steps = math.floor((base_volume * scale) / vstep + 1e-9)
        lot = round(steps * vstep, 10)
        return max(vmin, lot)

    def _open_from_signal(self, agent, signal, scale: float = 1.0) -> bool:
        """Valida y ejecuta una entrada a partir de la señal. `scale` (0..1)
        reduce el lote base según la asignación del coordinador. Devuelve True
        si la orden se ejecutó."""
        symbol = agent.symbol
        base_volume = agent.resolve_volume(self.client, signal)
        volume = base_volume if scale >= 1.0 else self._scale_volume(symbol, base_volume, scale)

        # Contexto extra para la validación: spread actual (filtro de coste) y
        # nº de posiciones de TODA la cuenta (límite global, no por símbolo).
        tick = self.client.get_tick(symbol)
        positions = self.client.get_positions(symbol)
        spread_points = self._spread_points(symbol, tick)
        total_open = len(self.client.get_positions() or [])
        if not agent.validate(signal, positions, tick=tick,
                              spread_points=spread_points, total_open_positions=total_open):
            print("  Señal no validada para ejecución.")
            return False

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
            return True
        elif result and result.get("timeout"):
            print("  [!] TIMEOUT esperando al EA: la orden NO se confirmó.")
            print("      La orden PUEDE haberse ejecutado igualmente. Revisa MT4")
            print("      antes de que el orquestador reintente en el próximo ciclo.")
        else:
            err = (result or {}).get("error") or (result or {}).get("comment") or "sin respuesta"
            print(f"  Error al ejecutar orden: {err}")
        return False

    def _run_agent(self, agent):
        """Ruta clásica (sin coordinador): un agente analiza y ejecuta su propia
        señal de forma aislada."""
        signal = self._gather_signal(agent)
        if not signal:
            return
        self._print_signal_details(agent, signal)

        if signal["action"] == "HOLD":
            return
        # En cooldown por pérdida diaria la señal queda registrada (memoria y
        # contexto) pero NO se abre operación: esperamos al cierre de las abiertas.
        if self._risk_cooldown_active():
            print("  Cooldown por pérdida diaria: señal registrada, no se abre operación.")
            return
        self._open_from_signal(agent, signal)

    # ----- Ciclo coordinado (mesa de dirección) -----

    def _run_coordinated_cycle(self):
        """Ciclo con coordinador: recolectar señales -> coordinar cartera ->
        ejecutar por prioridad (entradas aprobadas + cierres/reducciones)."""
        # Fase 1: recolectar las señales de todos los especialistas.
        signals = {}
        for agent in self.agents:
            sig = self._gather_signal(agent)
            if sig:
                signals[agent.symbol] = sig

        # Fase 2: coordinar. Snapshot determinista + decisión LLM + clamp duro.
        in_cooldown = self._risk_cooldown_active()
        snapshot = self.risk_book.snapshot(
            self.client, self.agents,
            day_start_equity=self._day_start_equity, in_cooldown=in_cooldown)

        has_positions = snapshot.get("open_positions_total", 0) > 0
        if not signals and not (has_positions and self.risk_book.can_close):
            print("\n  [Mesa] Sin señales nuevas ni posiciones que gestionar; se omite coordinación.")
            self._store_coordination(snapshot, {"rationale": "sin actividad este ciclo", "decisions": []})
            return

        print(f"\n{'#' * 50}")
        print("  [MESA DE DIRECCIÓN] Coordinando cartera...")
        with _Spinner("  Decidiendo asignación"):
            result = self.coordinator.decide(
                snapshot, signals, self.agents_overview(),
                news_context=self._coordinator_news())
        self._print_coordination(result, snapshot)
        self._store_coordination(snapshot, result)

        # Fase 3: ejecutar por prioridad (1 = primero).
        agent_by_symbol = {a.symbol: a for a in self.agents}
        decisions = sorted(result.get("decisions", []), key=lambda d: d.get("priority", 99))
        for d in decisions:
            agent = agent_by_symbol.get(d.get("symbol"))
            if agent is None:
                continue
            self._execute_decision(agent, signals.get(agent.symbol), d)

    def _execute_decision(self, agent, signal, decision):
        """Aplica una decisión del coordinador: gestiona las posiciones abiertas
        (close/reduce/hedge) y abre la entrada si está aprobada."""
        symbol = agent.symbol
        action = decision.get("position_action", "hold")
        direction = decision.get("manage_direction")  # lado del libro a tratar
        if action in ("close", "reduce") and self.risk_book.can_close:
            self._manage_open_positions(symbol, action, decision.get("reason", ""),
                                        direction=direction)
        elif action == "hedge" and self.risk_book.can_close:
            self._hedge_position(symbol, direction, decision.get("reason", ""))

        actionable = signal and signal.get("action") in ("BUY", "SELL")
        if not actionable:
            return
        if decision.get("approve"):
            alloc = decision.get("allocation_pct", 0.0)
            print(f"\n  [Mesa -> {agent.name}] Entrada APROBADA {signal['action']} {symbol} "
                  f"(prioridad {decision.get('priority')}, asignación {alloc:.0%}). "
                  f"{decision.get('reason', '')}")
            self._print_signal_details(agent, signal)
            self._open_from_signal(agent, signal, scale=self._alloc_to_scale(alloc))
        else:
            motivo = decision.get("clamp") or decision.get("reason", "decisión del coordinador")
            print(f"  [Mesa] Entrada VETADA en {symbol}: {motivo}")

    def _alloc_to_scale(self, alloc: float) -> float:
        """Traduce la asignación (% del equity) a un multiplicador del lote base
        en [0.25, 1.0]. Heurística v1: fracción del presupuesto del símbolo que
        el coordinador decide usar. Sin asignación -> lote base (1.0)."""
        if not alloc or alloc <= 0:
            return 1.0
        cap = self.risk_book.max_symbol_allocation_pct or alloc
        return max(0.25, min(1.0, alloc / cap))

    def _manage_open_positions(self, symbol: str, action: str, reason: str,
                               direction: str = None):
        """Cierra (close) o reduce (cierra 1 posición; el cliente no soporta
        cierre parcial) la exposición abierta del símbolo. Si `direction`
        (BUY/SELL) se indica, actúa solo sobre ese lado del libro (MT5 lo filtra;
        MT4 lo ignora y cierra lo que el EA decida — best-effort). Los cierres se
        registran en el historial en el siguiente ciclo (_detect_closed_trades)."""
        positions = self.client.get_positions(symbol) or []
        if direction:
            positions = [p for p in positions if _pos_direction(p) == direction]
        if not positions:
            return
        tag = f" {direction}" if direction else ""
        if action == "close":
            print(f"  [Mesa] Cerrando {len(positions)} posición(es){tag} de {symbol}: {reason}")
            for _ in range(len(positions)):
                res = self.client.close_position(symbol, direction=direction)
                if not res or not res.get("success"):
                    print(f"    Cierre detenido: {(res or {}).get('error') or 'sin más posiciones'}")
                    break
        else:  # reduce: cierra una sola posición del símbolo (del lado indicado)
            print(f"  [Mesa] Reduciendo {symbol}{tag} (cierra 1 posición): {reason}")
            res = self.client.close_position(symbol, direction=direction)
            if not res or not res.get("success"):
                print(f"    No se pudo reducir: {(res or {}).get('error') or 'sin posición'}")

    def _hedge_position(self, symbol: str, net_side: str, reason: str):
        """Cubre el sesgo neto del símbolo abriendo una orden OPUESTA por el
        volumen neto (sin SL/TP). `net_side` (BUY/SELL) es el lado del libro a
        neutralizar; se abre el contrario. Solo tiene efecto real en cuentas
        hedging; en netting el RiskBook ya degrada la cobertura a 'reduce'."""
        if net_side not in ("BUY", "SELL"):
            print(f"  [Mesa] Cobertura {symbol}: sin sesgo neto definido, se omite.")
            return
        positions = self.client.get_positions(symbol) or []
        long_vol = sum(_pos_to_float(_pos_get(p, "volume"))
                       for p in positions if _pos_direction(p) == "BUY")
        short_vol = sum(_pos_to_float(_pos_get(p, "volume"))
                        for p in positions if _pos_direction(p) == "SELL")
        net_vol = abs(long_vol - short_vol)
        if net_vol <= 0:
            print(f"  [Mesa] Cobertura {symbol}: sin volumen neto que cubrir.")
            return
        sym = self.client.get_symbol_info(symbol)
        vmin = getattr(sym, "volume_min", 0.01) or 0.01
        vstep = getattr(sym, "volume_step", 0.01) or 0.01
        steps = math.floor(net_vol / vstep + 1e-9)
        volume = max(vmin, round(steps * vstep, 10))
        opposite = "SELL" if net_side == "BUY" else "BUY"
        print(f"  [Mesa] Cobertura {symbol}: abre {opposite} {volume} para neutralizar "
              f"el neto {net_side}. {reason}")
        result = self.client.place_order(
            symbol=symbol, volume=volume, order_type=opposite,
            stop_loss=None, take_profit=None, comment=f"hedge {symbol}",
        )
        if result and result.get("success"):
            print(f"    Cobertura abierta: ticket {result.get('order')} @ {result.get('price')}")
        else:
            err = (result or {}).get("error") or (result or {}).get("comment") or "sin respuesta"
            print(f"    No se pudo cubrir: {err}")

    def _coordinator_news(self) -> str:
        """Resumen de noticias por símbolo para el coordinador (news_provider
        cachea, así que reutilizar es barato). Fail-safe: '' ante errores."""
        try:
            from core.news import news_provider
        except Exception:
            return ""
        parts, seen = [], set()
        for agent in self.agents:
            if agent.symbol in seen:
                continue
            seen.add(agent.symbol)
            ctx = news_provider.get_news_context(agent.symbol)
            if ctx:
                parts.append(f"[{agent.symbol}]\n{ctx}")
        return "\n\n".join(parts)

    def _print_coordination(self, result: dict, snapshot: dict):
        print(f"  Exposición total: {snapshot['total_exposure_pct']:.1%} / "
              f"tope {snapshot['max_total_exposure_pct']:.0%}")
        if result.get("rationale"):
            print(f"  Razón de la mesa: {result['rationale']}")
        sym_info = snapshot.get("symbols", {})
        for d in result.get("decisions", []):
            tag = "APROBADA" if d.get("approve") else "vetada  "
            nd = sym_info.get(d.get("symbol"), {}).get("net_direction", "FLAT")
            md = f" -> {d['manage_direction']}" if d.get("manage_direction") else ""
            extra = f" | clamp: {d['clamp']}" if d.get("clamp") else ""
            print(f"    {d.get('symbol')} [neto {nd}]: {tag} | prio {d.get('priority')} | "
                  f"asignación {d.get('allocation_pct', 0):.0%} | "
                  f"pos: {d.get('position_action')}{md}{extra}")

    def _store_coordination(self, snapshot: dict, result: dict):
        """Guarda la última coordinación para el dashboard y la emite por WS."""
        self.last_coordination = {
            "snapshot": snapshot,
            "rationale": result.get("rationale", ""),
            "decisions": result.get("decisions", []),
        }
        self.last_coordination_at = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            from api.server import broadcast_coordinator_decision
            broadcast_coordinator_decision(self.last_coordination)
        except Exception:
            pass

    def coordinate_now(self) -> dict:
        """Decisión de coordinación bajo demanda (dry-run: NO ejecuta órdenes).

        Usa las últimas señales conocidas de cada símbolo (bot_state), sin
        relanzar el análisis LLM de los especialistas, para que sea seguro
        llamarla desde el hilo del API. Almacena y emite el resultado."""
        if self.coordinator is None or self.risk_book is None:
            return {"enabled": False}
        state = bot_state.get_state()
        signals = dict(state.get("signals", {}) or {})
        in_cooldown = self._risk_cooldown_active()
        snapshot = self.risk_book.snapshot(
            self.client, self.agents,
            day_start_equity=self._day_start_equity, in_cooldown=in_cooldown)
        result = self.coordinator.decide(
            snapshot, signals, self.agents_overview(),
            news_context=self._coordinator_news())
        self._store_coordination(snapshot, result)
        return self.last_coordination

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

    def coordinator_overview(self) -> dict:
        """Estado del coordinador para el dashboard (/api/coordinator). Si no hay
        coordinador activo, {"enabled": False}."""
        if self.coordinator is None or self.risk_book is None:
            return {"enabled": False}
        return {
            "enabled": True,
            "provider": self.coordinator.provider,
            "model": self.coordinator.model,
            "can_close": self.risk_book.can_close,
            "max_total_exposure_pct": self.risk_book.max_total_exposure_pct,
            "max_symbol_allocation_pct": self.risk_book.max_symbol_allocation_pct,
            "max_net_direction_pct": self.risk_book.max_net_direction_pct,
            "reversal_drawdown_pct": self.risk_book.reversal_drawdown_pct,
            "max_symbol_loss_pct": self.risk_book.max_symbol_loss_pct,
            "last_coordination": self.last_coordination,
            "last_coordination_at": self.last_coordination_at,
        }
