"""Orquestador de agentes.

De momento coordina la ejecución: en cada ciclo recorre los agentes activos,
les pide su análisis y ejecuta las señales válidas. Mantiene además un registro
de rendimiento por agente que servirá de base para la fase de optimización
(ajuste automático de parámetros / modelo de cada agente).
"""
import os
import io
import sys
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from core import console
from core.state import bot_state
from core.bot_state import Trade
from core.logger import log_trade, log_closed_trade, log_equity
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


# Estado por hilo: marca si el código corre dentro de un worker del análisis
# paralelo (para silenciar el spinner animado, cuyos '\r' ensucian el buffer).
_worker_local = threading.local()


class _ThreadRoutedStdout:
    """stdout que enruta la salida de cada hilo a su propio buffer.

    Durante el análisis paralelo, los `print()` de cada agente se desvían a un
    StringIO aislado (registrado por id de hilo); el resto sigue yendo a la
    consola real. Así los reportes no se entremezclan y luego se vuelcan en
    orden. Delega cualquier otro atributo en el stream real."""

    def __init__(self, real):
        self._real = real
        self._buffers = {}

    def register(self, tid, buf):
        self._buffers[tid] = buf

    def unregister(self, tid):
        self._buffers.pop(tid, None)

    def _target(self):
        return self._buffers.get(threading.get_ident(), self._real)

    def write(self, s):
        return self._target().write(s)

    def flush(self):
        try:
            self._target().flush()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._real, name)


class _Spinner:
    """Spinner animado en un hilo aparte mientras se genera el análisis.

    A diferencia de un sleep fijo, gira de verdad durante toda la llamada al
    LLM (que puede tardar varios segundos). Al terminar deja un rastro corto
    (``✓ (7.0s)``) en vez de desaparecer sin huella, para que el log conserve
    cuánto tardó cada paso. Se usa como context manager: ``with _Spinner("..."):``.
    """

    FRAMES = ["|", "/", "-", "\\"]  # ASCII: se ve bien en cualquier consola Windows

    def __init__(self, message: str, interval: float = 0.12, leave: bool = True):
        self.message = message
        self.interval = interval
        self.leave = leave
        self._stop = threading.Event()
        self._thread = None
        self._start = 0.0
        self.elapsed = 0.0

    def __enter__(self):
        self._start = time.time()
        # En un worker del análisis paralelo no animamos: los '\r' ensuciarían el
        # buffer del hilo. Se conserva el rastro de duración al salir.
        if getattr(_worker_local, "active", False):
            self._thread = None
            return self
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
        return self

    def _spin(self):
        i = 0
        while not self._stop.is_set():
            frame = self.FRAMES[i % len(self.FRAMES)]
            elapsed = time.time() - self._start
            print(f"\r{self.message} {console.info(frame)} "
                  f"{console.dim(f'({elapsed:4.1f}s)')}", end="", flush=True)
            i += 1
            time.sleep(self.interval)

    def __exit__(self, *exc):
        self._stop.set()
        self.elapsed = time.time() - self._start
        if self._thread is None:
            # Modo worker (sin animación): solo el rastro de duración.
            if self.leave:
                print(f"{self.message} {console.dim(f'✓ ({self.elapsed:4.1f}s)')}")
            return
        self._thread.join()
        # Limpia la línea del spinner y, si procede, deja el rastro de duración.
        print(f"\r{' ' * (len(self.message) + 24)}\r", end="", flush=True)
        if self.leave:
            print(f"{self.message} {console.dim(f'✓ ({self.elapsed:4.1f}s)')}")


class AgentOrchestrator:

    def __init__(self, agents: list, client, platform: str = "mt4",
                 optimize_every_cycles: int = 0, coordinator=None, risk_book=None,
                 schedule_cfg: dict = None):
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

        # --- Planificador de cadencias (un único bucle, sin hilos extra) ---
        # El tick base es la rotación; noticias/junta/reporte se "abren" por
        # tiempo (time.monotonic) comparando contra su último disparo.
        self.schedule_cfg = schedule_cfg or {}
        self.rotation_seconds = self.schedule_cfg.get("rotation_seconds", 60)
        self.news_poll_seconds = self.schedule_cfg.get("news_poll_seconds", 30 * 60)
        self.junta_interval_seconds = self.schedule_cfg.get("junta_interval_seconds", 60 * 60)
        self.report_interval_seconds = self.schedule_cfg.get("report_interval_seconds", 2 * 60 * 60)
        # Claves de eventos RED ya atendidos (para reaccionar una sola vez).
        self._reacted_news_keys: set = set()
        # Junta global / reporte: marcas para el dashboard.
        self.last_junta_at = None
        self.last_report = None
        self.last_report_at = None
        # Relojes del planificador (monotónicos): se inicializan a "ahora" para
        # que el primer disparo ocurra tras un intervalo completo.
        clock = time.monotonic()
        self._last_news_poll = clock
        self._last_junta = clock
        self._last_report = clock
        # Registro de la evolución de la cartera (equity.csv) para el gráfico del
        # dashboard. Se loguea como mucho cada equity_log_seconds (default 60s).
        self.equity_log_seconds = self.schedule_cfg.get("equity_log_seconds", 60)
        self._last_equity_log = 0.0  # 0 = registrar en la primera rotación
        # Análisis en paralelo: lanza el análisis (LLM) de los agentes a la vez en
        # la fase de recolección. Solo aporta si el backend LLM atiende peticiones
        # concurrentes (nube, u Ollama con NUM_PARALLEL). Los accesos al EA se
        # serializan vía MT4Client._send_lock; la salida se vuelca por agente.
        self.parallel_analysis = (
            os.getenv("PARALLEL_ANALYSIS", "true").lower() in ("1", "true", "yes", "on"))
        # Control de pérdida por ventana móvil (0 = desactivado). Al tocar el
        # límite dentro de la ventana se entra en cooldown (no se abren
        # operaciones), no se detiene el bot. La ventana se rearma cada
        # RISK_LOSS_WINDOW_SECONDS (6 h por defecto) fijando un nuevo equity base.
        self.max_daily_loss_pct = float(os.getenv("MAX_DAILY_LOSS_PCT", "0") or 0)
        self.risk_loss_window_seconds = float(
            os.getenv("RISK_LOSS_WINDOW_SECONDS", str(6 * 3600)) or (6 * 3600))
        self._risk_window_start = None   # time.monotonic al inicio de la ventana
        self._window_start_equity = None  # equity de referencia de la ventana
        self._cooldown_active = False     # cooldown vigente en la ventana actual
        # Última instantánea de posiciones por símbolo {symbol: {ticket: snap}}:
        # base para detectar cierres y registrarlos en el historial.
        self._prev_positions: dict = {}
        # Momento del último análisis por símbolo, para espaciarlo al estar en
        # el máximo de posiciones abiertas.
        self._last_analysis_at: dict = {}
        # Nº de rotación (para la cabecera de cada ciclo en la terminal).
        self._rotation_count = 0

    # ----- Ejecución -----

    @staticmethod
    def _due(last: float, interval: float, now: float) -> bool:
        """True si ya transcurrió `interval` segundos desde `last` (0 = nunca)."""
        return interval > 0 and (now - last) >= interval

    def run_forever(self, poll_seconds: int = 60):
        bot_state.set_bot_running(True)
        self.rotation_seconds = poll_seconds
        # Arranque: la mesa revisa la cuenta y la disponibilidad de agentes
        # antes de la primera rotación.
        self._startup_review()
        cycle = 0
        try:
            while True:
                account_info = self.client.get_account_info()
                if account_info:
                    bot_state.update_account(account_info)
                    self._check_daily_loss_guard(account_info)
                    self._log_equity_snapshot(account_info)

                if not bot_state.bot_running:
                    time.sleep(5)
                    continue

                # Sonda de noticias RED (cada NEWS_POLL_SECONDS): los símbolos con
                # un evento de alto impacto nuevo se fuerzan en esta rotación
                # (se saltan el throttle de análisis).
                now = time.monotonic()
                forced_symbols: set = set()
                if self._due(self._last_news_poll, self.news_poll_seconds, now):
                    self._last_news_poll = now
                    forced_symbols = self._poll_red_news()

                # Rotación (cada tick): analizar y, si procede, coordinar/ejecutar.
                self._run_rotation(forced_symbols)

                # Junta de la mesa (cada JUNTA_INTERVAL_SECONDS): revisión global
                # del libro aunque la rotación no haya tenido actividad.
                now = time.monotonic()
                if self._due(self._last_junta, self.junta_interval_seconds, now):
                    self._last_junta = now
                    self._run_junta()

                # Reporte (cada REPORT_INTERVAL_SECONDS): genera y (si SMTP está
                # activo) envía el informe de estado.
                now = time.monotonic()
                if self._due(self._last_report, self.report_interval_seconds, now):
                    self._last_report = now
                    self._run_report()

                cycle += 1
                if self.optimize_every_cycles and cycle % self.optimize_every_cycles == 0:
                    self.optimize()

                time.sleep(poll_seconds)
        except KeyboardInterrupt:
            print("\n\nOrquestador detenido por el usuario.")

    def _startup_review(self):
        """Secuencia de arranque: la mesa revisa el estado de la cuenta y la
        disponibilidad de cada agente (símbolo presente en el broker) antes de
        la primera rotación."""
        print("\n" + console.header("ARRANQUE — REVISIÓN DE LA MESA", char="#"))
        if self.risk_book is not None:
            try:
                snap = self.risk_book.snapshot(
                    self.client, self.agents,
                    day_start_equity=self._window_start_equity, in_cooldown=False)
                exp = snap.get("total_exposure_pct", 0)
                tope = snap.get("max_total_exposure_pct", 0)
                print(console.kv("Equity", console.money(snap.get("equity", 0))))
                print(console.kv("Exposición total",
                                 f"{exp:.1%} {console.dim(f'/ tope {tope:.0%}')}"))
                print(console.kv("Posiciones abiertas", snap.get("open_positions_total", 0)))
                print(console.kv("Cobertura (hedge)",
                                 console.ok("sí") if snap.get("hedging") else console.dim("no")))
            except Exception as exc:  # noqa: BLE001 — el arranque nunca debe fallar
                print(console.kv("Snapshot inicial", console.err(f"no disponible: {exc}")))

        available = set(self.client.get_symbols() or [])
        print(console.dim("  Disponibilidad de agentes:"))
        for agent in self.agents:
            in_broker = (not available) or (agent.symbol in available)
            if not in_broker:
                estado = console.err("NO DISPONIBLE en el broker")
            elif not self.client.is_market_open(agent.symbol):
                estado = console.warn("NO DISPONIBLE — mercado cerrado")
            else:
                estado = console.ok("disponible")
            print(f"    {console.dim('·')} {agent.name} [{console.info(agent.symbol)}]: {estado}")

        mode = "mesa de dirección" if self.coordinator is not None else "clásico por agente"
        print(console.kv("Modo", console.accent(mode)))
        print(console.dim(f"  Cadencias: rotación {self.rotation_seconds}s · "
                          f"noticias {self.news_poll_seconds // 60}min · "
                          f"junta {self.junta_interval_seconds // 60}min · "
                          f"reporte {self.report_interval_seconds // 60}min."))

    def _run_rotation(self, forced_symbols: set):
        """Una rotación: con coordinador, ciclo coordinado; sin él, ruta clásica.
        Los símbolos en `forced_symbols` (noticia RED) se analizan saltándose el
        throttle, incluso si la cuenta está en su máximo global de posiciones."""
        self._rotation_count += 1
        forced_tag = (console.warn(f" · forzados: {', '.join(sorted(forced_symbols))}")
                      if forced_symbols else "")
        print("\n" + console.rule(
            f"Rotación #{self._rotation_count} · {time.strftime('%H:%M:%S')}{forced_tag}",
            style=console.info))

        if self.coordinator is not None:
            self._run_coordinated_cycle(force_symbols=forced_symbols)
            return

        # Ruta clásica: si estamos en el máximo global y no hay símbolos forzados
        # por noticias, saltamos el análisis (ahorro de LLM) pero seguimos con
        # junta/reporte/optimización del bucle.
        all_positions = self.client.get_positions() or []
        max_global = max((a.params.max_open_positions for a in self.agents), default=0)
        at_global_max = bool(max_global) and len(all_positions) >= max_global
        if at_global_max and not forced_symbols:
            symbols_profit = {}
            for p in all_positions:
                sym = _pos_get(p, "symbol", default="?")
                profit = _pos_to_float(_pos_get(p, "profit"))
                symbols_profit[sym] = symbols_profit.get(sym, 0.0) + profit
            total_profit = sum(symbols_profit.values())
            print(f"\n  {console.warn('⏭️ Máximo global de posiciones')} "
                  f"({max_global}) alcanzado.")
            print(f"  💰 Profit no realizado total: {console.pnl(total_profit)}")
            for sym, prof in sorted(symbols_profit.items()):
                print(f"     {sym}: {console.pnl(prof)}")
            return

        for agent in self.agents:
            forced = agent.symbol in forced_symbols
            if at_global_max and not forced:
                continue
            self._run_agent(agent, force=forced)

    def _poll_red_news(self) -> set:
        """Sondea noticias de alto impacto (RED) de los símbolos con agente.

        Devuelve el conjunto de símbolos con un evento RED *nuevo* (no atendido
        antes). Imprime cada disparo. Fail-safe: set() ante cualquier error."""
        try:
            from core.news import news_provider
        except Exception:
            return set()
        forced, seen = set(), set()
        for agent in self.agents:
            if agent.symbol in seen:
                continue
            seen.add(agent.symbol)
            try:
                events = news_provider.get_high_impact_events(agent.symbol)
            except Exception:
                events = []
            for ev in events:
                key = ev.get("key")
                if not key or key in self._reacted_news_keys:
                    continue
                self._reacted_news_keys.add(key)
                forced.add(agent.symbol)
                meta = console.dim(f"({ev.get('country', '?')}, {ev.get('when', '')})")
                print(f"\n  {console.warn('⚡ [NOTICIA RED]')} {console.bold(agent.symbol)}: "
                      f"{ev.get('title', '?')} {meta}. "
                      f"Forzando análisis del especialista vía mesa.")
        return forced

    def _log_equity_snapshot(self, account_info: dict):
        """Registra una instantánea de la cartera en equity.csv, con throttle
        (equity_log_seconds) para no inflar el fichero. Tolerante a fallos: un
        error de escritura nunca debe tumbar el loop."""
        now = time.monotonic()
        if not self._due(self._last_equity_log, self.equity_log_seconds, now):
            return
        self._last_equity_log = now
        equity = account_info.get("equity") or 0
        if equity <= 0:
            return
        platform = (account_info.get("platform") or "mt4").lower()
        try:
            log_equity(
                balance=account_info.get("balance") or 0,
                equity=equity,
                free_margin=account_info.get("free_margin") or 0,
                platform=platform,
            )
        except OSError:
            pass

    def _check_daily_loss_guard(self, account_info: dict):
        """Control de pérdida por *cooldown* en ventana móvil (no detiene el bot).

        Fija el equity de referencia al inicio de cada ventana de
        `risk_loss_window_seconds` (6 h por defecto) y, si dentro de la ventana el
        equity cae por debajo del límite configurado, activa el cooldown: el bot
        sigue corriendo (actualiza estado, evalúa memoria y detecta cierres) pero
        deja de abrir nuevas operaciones y espacia el análisis. Al expirar la
        ventana se rearma con un nuevo equity base (cooldown desactivado)."""
        if not self.max_daily_loss_pct:
            return
        equity = account_info.get("equity") or 0
        if equity <= 0:
            return

        now = time.monotonic()
        # Primera vez o ventana expirada: rearmar con el equity actual de referencia.
        if (self._risk_window_start is None
                or (now - self._risk_window_start) >= self.risk_loss_window_seconds):
            self._risk_window_start = now
            self._window_start_equity = equity
            self._cooldown_active = False
            return

        baseline = self._window_start_equity or equity
        drawdown = (baseline - equity) / baseline
        if drawdown >= self.max_daily_loss_pct and not self._cooldown_active:
            self._cooldown_active = True
            ventana_h = self.risk_loss_window_seconds / 3600
            print("\n" + console.err("!" * console.WIDTH))
            print("  " + console.err(console.bold(
                f"PÉRDIDA EN {ventana_h:.0f}H: {drawdown:.1%} >= límite {self.max_daily_loss_pct:.1%}")))
            print(console.kv("Equity ventana", f"{baseline:.2f} → {equity:.2f}"))
            print("  " + console.warn("COOLDOWN: no se abren nuevas operaciones; análisis espaciado."))
            print(console.dim(f"  El bot sigue activo; la ventana se rearma en ~{ventana_h:.0f} h."))
            print(console.err("!" * console.WIDTH))

    def _risk_cooldown_active(self) -> bool:
        """True si se tocó el límite dentro de la ventana actual (cooldown vigente).

        Si la ventana ya expiró se considera rearmado (False) aunque el guard aún
        no haya corrido para fijar la nueva base."""
        if not self._cooldown_active:
            return False
        if (self._risk_window_start is not None
                and (time.monotonic() - self._risk_window_start) >= self.risk_loss_window_seconds):
            return False
        return True

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
        # Persistir a CSV para que el rendimiento sobreviva al reinicio.
        log_closed_trade(
            symbol=symbol,
            action=snap.get("direction", "?"),
            volume=snap.get("volume", 0.0),
            entry_price=snap.get("open_price", 0.0),
            exit_price=exit_price,
            pnl=snap.get("profit", 0.0),
            commission=0.0,  # se rellenará cuando el broker lo devuelva
            duration_seconds=duration,
            platform=self.platform,
        )
        print(f"  {console.accent('⟳ Cierre registrado')}: {console.side(trade.action)} "
              f"{symbol} | P/L≈{console.pnl(trade.pnl)}")

    def _print_positions_summary(self, symbol: str, positions: list):
        """Resumen de posiciones abiertas con su profit no realizado."""
        if positions:
            total_profit = 0.0
            print(f"\n  📊 {console.bold(f'Posiciones abiertas en {symbol}')}:")
            for i, pos in enumerate(positions, 1):
                ticket = _pos_get(pos, "ticket", default="?")
                direction = _pos_direction(pos)
                volume = _pos_to_float(_pos_get(pos, "volume"))
                open_price = _pos_to_float(_pos_get(pos, "open_price", "price_open"))
                current_price = _pos_to_float(_pos_get(pos, "current_price"))
                profit = _pos_to_float(_pos_get(pos, "profit"))
                total_profit += profit
                print(f"    {i}. {console.dim(f'#{ticket}')} | {console.side(direction)} "
                      f"{volume} lotes | {console.dim(f'{open_price} → {current_price}')} | "
                      f"P/L: {console.pnl(profit)}")
            print(f"  💰 Profit no realizado total ({symbol}): {console.pnl(total_profit)}")
        else:
            print(f"  {console.ok('✅ Sin posiciones abiertas')} en {symbol}.")

    def _gather_signals_parallel(self, force_symbols: set) -> dict:
        """Recolecta las señales de todos los agentes EN PARALELO.

        Lanza `_gather_signal` de cada agente en su propio hilo (la parte lenta,
        la llamada al LLM, se solapa). Los accesos al EA se serializan en
        `MT4Client._send` (canal único). La salida de cada agente se captura en un
        buffer aislado y se vuelca en orden al terminar, para no entremezclar los
        reportes. Devuelve ``{symbol: signal}`` igual que la ruta secuencial."""
        real_out = sys.stdout
        router = (real_out if isinstance(real_out, _ThreadRoutedStdout)
                  else _ThreadRoutedStdout(real_out))
        buffers = {a.name: io.StringIO() for a in self.agents}
        results: dict = {}

        def work(agent):
            tid = threading.get_ident()
            router.register(tid, buffers[agent.name])
            _worker_local.active = True
            try:
                return agent.symbol, self._gather_signal(
                    agent, force=agent.symbol in force_symbols)
            except Exception as e:  # un agente que falle no tumba al resto
                print("  " + console.err(f"✗ Error analizando {agent.symbol}: {e}"))
                return agent.symbol, None
            finally:
                _worker_local.active = False
                router.unregister(tid)

        sys.stdout = router
        # El hilo principal queda libre mientras los workers generan el análisis
        # (escriben a sus buffers); animamos un spinner en la consola real para
        # que se note que la recolección está en marcha. El spinner corre en su
        # propio hilo, NO registrado en el router, así que escribe a real_out; se
        # detiene antes de volcar los buffers para no pisar su '\r'.
        n = len(self.agents)
        try:
            with _Spinner(f"  Analizando {n} símbolos en paralelo"):
                with ThreadPoolExecutor(max_workers=n,
                                        thread_name_prefix="analyze") as ex:
                    collected = list(ex.map(work, self.agents))
        finally:
            sys.stdout = real_out

        # Vuelca los reportes en el orden de los agentes (lectura estable).
        for agent in self.agents:
            out = buffers[agent.name].getvalue()
            if out:
                real_out.write(out)
        real_out.flush()

        for symbol, sig in collected:
            if sig:
                results[symbol] = sig
        return results

    def _gather_signal(self, agent, force: bool = False):
        """Prepara el símbolo (detecta cierres, sincroniza estado, respeta
        throttle/cooldown) y genera la señal del especialista. Devuelve el dict
        de señal (incluido HOLD) o None si no se analizó / no hubo señal.

        `force=True` (noticia RED) salta el throttle de análisis (al-máximo /
        espaciado de cooldown) para mirar el símbolo igualmente; la EJECUCIÓN
        sigue gobernada por la validación y la mesa (no se salta el riesgo).

        NO ejecuta órdenes ni imprime el detalle de la señal: eso es de la fase
        de ejecución (clásica o coordinada). Sirve a ambas rutas."""
        symbol = agent.symbol
        print(f"\n  {console.accent('▸')} {console.bold(agent.name)} "
              f"{console.dim('analiza')} {console.info(symbol)}")

        # Mercado cerrado (fin de semana / fuera de sesión): no analizamos ni
        # gastamos LLM. El símbolo figura como NO DISPONIBLE hasta que reabra.
        if not self.client.is_market_open(symbol):
            print("  " + console.warn(f"⏸ {symbol}: mercado cerrado — no disponible, análisis omitido."))
            return None

        tick = self.client.get_tick(symbol)
        if tick:
            print(console.kv("Precio", f"ask {tick.ask} {console.dim('/')} bid {tick.bid}"))

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
        if (in_cooldown or at_max) and not force:
            interval = (RISK_COOLDOWN_ANALYSIS_INTERVAL if in_cooldown
                        else AT_MAX_ANALYSIS_INTERVAL)
            if self._throttled(symbol, interval):
                if in_cooldown:
                    print(console.dim(f"  ⏸ Cooldown por pérdida diaria; análisis aplazado "
                                      f"(cada {interval // 60} min, esperando el cierre de posiciones)."))
                else:
                    print(console.dim(f"  ⏸ {len(positions)} posiciones abiertas (máx {max_pos}); "
                                      f"análisis aplazado (cada {interval // 60} min salvo conf>=90%)."))
                return None
        elif force and (in_cooldown or at_max):
            print("  " + console.warn("⚡ análisis forzado por noticia pese al throttle "
                                       "(la ejecución sigue sujeta a validación/mesa)."))

        with _Spinner("  Generando análisis" + (" (forzado por noticia)" if force else "")):
            signal = agent.analyze(self.client, platform=self.platform)
        self._last_analysis_at[symbol] = time.time()
        if not signal:
            print("  " + console.err("✗ No se generó señal."))
            return None

        self.stats[agent.name]["signals"] += 1
        bot_state.update_signal(signal)
        if signal["action"] == "HOLD":
            self.stats[agent.name]["holds"] += 1
        # El especialista deja su "reporte" a la vista antes de que la mesa
        # decida: así se ve qué propuso cada agente, se apruebe o se vete.
        self._print_signal_brief(agent, signal)
        return signal

    def _print_signal_brief(self, agent, signal):
        """Reporte compacto de lo que el especialista propone a la mesa.

        Es el resumen que faltaba en terminal: acción, confianza, tendencia,
        riesgo, niveles y un extracto de la razón. El detalle completo con
        métricas de P/L se imprime en la fase de ejecución (`_print_signal_details`)."""
        action = signal.get("action", "?")
        conf = signal.get("confidence", 0) or 0
        trend = signal.get("trend", "N/A")
        risk = signal.get("risk_level", "N/A")
        label = "📨 reporte → mesa" if self.coordinator is not None else "📨 señal"
        dot = console.dim("·")
        head = (f"  {console.accent(label)}: {console.side(action)} "
                f"{dot} conf {conf:.0%} {dot} tendencia {trend} {dot} riesgo {risk}")
        print(head)
        if signal.get("entry"):
            print(console.dim(f"     niveles: entry {signal['entry']} · "
                              f"SL {signal.get('stop_loss')} · TP {signal.get('take_profit')}"))
        reason = str(signal.get("reason", "")).strip()
        if reason:
            if len(reason) > 140:
                reason = reason[:139].rstrip() + "…"
            print(console.dim(f"     “{reason}”"))

    def _effective_commission(self, agent) -> float:
        """Comisión por lote a usar en las métricas: prioriza el valor observado
        de MT (deducido de posiciones reales) y, mientras no haya datos del
        símbolo, recurre al de `.env` (semilla en `agent.config`). Persiste lo
        aprendido en la config del agente para los ciclos siguientes."""
        learned = self.client.get_commission_per_lot(agent.symbol)
        if learned is not None:
            agent.config.commission_per_lot = learned
        return agent.config.commission_per_lot

    def _print_signal_details(self, agent, signal):
        """Imprime la señal y, si tiene niveles, sus métricas de profit/pérdida."""
        symbol = agent.symbol
        volume = agent.resolve_volume(self.client, signal)
        conf = signal["confidence"]
        bar = console.dim("|")
        print(f"  Señal: {console.side(signal['action'])} {bar} "
              f"Confianza: {console.bold(f'{conf:.0%}')}")
        print(f"  Tendencia: {signal.get('trend', 'N/A')} {bar} "
              f"Riesgo: {signal.get('risk_level', 'N/A')}")
        if signal.get("entry"):
            print(f"  Entry: {signal['entry']} {bar} SL: {signal['stop_loss']} "
                  f"{bar} TP: {signal['take_profit']} {bar} Lote: {volume}")
            metrics = calc_trade_metrics(
                self.client, symbol, signal["action"],
                signal["entry"], signal["stop_loss"], signal["take_profit"],
                volume,
                commission_per_lot=self._effective_commission(agent),
            )
            if metrics:
                pips_tp = console.dim(f"({metrics['pips_tp']:.0f} pips)")
                pips_sl = console.dim(f"({metrics['pips_sl']:.0f} pips)")
                print(f"  Profit potencial: {console.pnl(metrics['net_profit'])}  {pips_tp}")
                print(f"  Pérdida potencial: {console.pnl(-metrics['net_loss'])}  {pips_sl}")
                print(console.dim(f"  Comisión estimada: ${metrics['commission']:.2f} | "
                                  f"R:R = 1:{metrics['rr']}"))
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
            print("  " + console.warn("⚠ Señal no validada para ejecución."))
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
            ticket = str(result.get("order", ""))
            trade_id = f"{agent.name}/{symbol}/{ticket}" if ticket else ""
            # Marcar la señal como ejecutada y vincular con el trade.
            signal["executed"] = True
            signal["trade_id"] = trade_id
            from core.logger import log_signal as _log_sig
            _log_sig(signal, platform=self.platform)
            
            print(f"  {console.ok('✓ Orden ejecutada')}: {console.side(signal['action'])} "
                  f"{symbol} {console.dim('· ticket')} {result.get('order')} "
                  f"{console.dim('@')} {result.get('price')}")
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
            print("  " + console.warn("[!] TIMEOUT esperando al EA: la orden NO se confirmó."))
            print(console.dim("      La orden PUEDE haberse ejecutado igualmente. Revisa MT4"))
            print(console.dim("      antes de que el orquestador reintente en el próximo ciclo."))
        else:
            err = (result or {}).get("error") or (result or {}).get("comment") or "sin respuesta"
            print("  " + console.err(f"✗ Error al ejecutar orden: {err}"))
        return False

    def _run_agent(self, agent, force: bool = False):
        """Ruta clásica (sin coordinador): un agente analiza y ejecuta su propia
        señal de forma aislada. `force` (noticia RED) salta el throttle de
        análisis."""
        signal = self._gather_signal(agent, force=force)
        if not signal:
            return

        if signal["action"] == "HOLD":
            return
        self._print_signal_details(agent, signal)
        # En cooldown por pérdida diaria la señal queda registrada (memoria y
        # contexto) pero NO se abre operación: esperamos al cierre de las abiertas.
        if self._risk_cooldown_active():
            print("  " + console.warn("Cooldown por pérdida diaria: señal registrada, "
                                       "no se abre operación."))
            return
        self._open_from_signal(agent, signal)

    # ----- Ciclo coordinado (mesa de dirección) -----

    def _run_coordinated_cycle(self, force_symbols: set = frozenset()):
        """Ciclo con coordinador: recolectar señales -> coordinar cartera ->
        ejecutar por prioridad (entradas aprobadas + cierres/reducciones).

        `force_symbols` (noticia RED) fuerza el análisis de esos símbolos aunque
        estén throttled; la coordinación y el clamp se aplican igual a todos."""
        # Fase 1/3: recolectar las señales de todos los especialistas.
        parallel = self.parallel_analysis and len(self.agents) > 1
        modo = console.dim(" (en paralelo)") if parallel else ""
        print(console.dim("  [1/3] Recolección de señales") + modo)
        if parallel:
            signals = self._gather_signals_parallel(force_symbols)
        else:
            signals = {}
            for agent in self.agents:
                sig = self._gather_signal(agent, force=agent.symbol in force_symbols)
                if sig:
                    signals[agent.symbol] = sig

        # Fase 2/3: coordinar. Snapshot determinista + decisión LLM + clamp duro.
        in_cooldown = self._risk_cooldown_active()
        snapshot = self.risk_book.snapshot(
            self.client, self.agents,
            day_start_equity=self._window_start_equity, in_cooldown=in_cooldown)

        has_positions = snapshot.get("open_positions_total", 0) > 0
        if not signals and not (has_positions and self.risk_book.can_close):
            print(console.dim("\n  [Mesa] Sin señales nuevas ni posiciones que gestionar; "
                              "se omite coordinación."))
            self._store_coordination(snapshot, {"rationale": "sin actividad este ciclo", "decisions": []})
            return

        print("\n" + console.header("[2/3] MESA DE DIRECCIÓN · coordinando cartera", char="#"))
        with _Spinner("  Decidiendo asignación"):
            result = self.coordinator.decide(
                snapshot, signals, self.agents_overview(),
                news_context=self._coordinator_news())
        self._print_coordination(result, snapshot, signals)
        self._store_coordination(snapshot, result)

        # Fase 3/3: ejecutar por prioridad (1 = primero).
        print(console.dim("\n  [3/3] Ejecución por prioridad"))
        self._execute_decisions(result, signals)

    def _execute_decisions(self, result: dict, signals: dict, manage_only: bool = False):
        """Ejecuta las decisiones de la mesa por prioridad (1 = primero).
        `manage_only` (junta) limita la ejecución a gestionar posiciones abiertas
        (close/reduce/hedge), sin abrir entradas nuevas a partir de señales que
        podrían estar desactualizadas."""
        agent_by_symbol = {a.symbol: a for a in self.agents}
        decisions = sorted(result.get("decisions", []), key=lambda d: d.get("priority", 99))
        for d in decisions:
            agent = agent_by_symbol.get(d.get("symbol"))
            if agent is None:
                continue
            self._execute_decision(agent, signals.get(agent.symbol), d,
                                   manage_only=manage_only)

    def _execute_decision(self, agent, signal, decision, manage_only: bool = False):
        """Aplica una decisión del coordinador: gestiona las posiciones abiertas
        (close/reduce/hedge) y abre la entrada si está aprobada. Con
        `manage_only` solo gestiona posiciones (no abre entradas)."""
        symbol = agent.symbol
        action = decision.get("position_action", "hold")
        direction = decision.get("manage_direction")  # lado del libro a tratar
        if action in ("close", "reduce") and self.risk_book.can_close:
            self._manage_open_positions(symbol, action, decision.get("reason", ""),
                                        direction=direction)
        elif action == "hedge" and self.risk_book.can_close:
            self._hedge_position(symbol, direction, decision.get("reason", ""))

        if manage_only:
            return
        actionable = signal and signal.get("action") in ("BUY", "SELL")
        if not actionable:
            return
        if decision.get("approve"):
            alloc = decision.get("allocation_pct", 0.0)
            prio = decision.get("priority")
            meta = console.dim(f"(prio {prio}, asignación {alloc:.0%})")
            label = console.ok(f"✓ [Mesa → {agent.name}] Entrada APROBADA")
            print(f"\n  {label} {console.side(signal['action'])} {symbol} {meta}")
            reason = decision.get("reason", "")
            if reason:
                print(console.dim(f"     {reason}"))
            self._print_signal_details(agent, signal)
            self._open_from_signal(agent, signal, scale=self._alloc_to_scale(alloc))
        else:
            motivo = decision.get("clamp") or decision.get("reason", "decisión del coordinador")
            print(f"  {console.err('✗ [Mesa] Entrada VETADA')} en {symbol}: {console.dim(motivo)}")

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
        (BUY/SELL) se indica, actúa solo sobre ese lado del libro (MT4 best-effort);
        MT4 lo ignora y cierra lo que el EA decida — best-effort). Los cierres se
        registran en el historial en el siguiente ciclo (_detect_closed_trades)."""
        positions = self.client.get_positions(symbol) or []
        if direction:
            positions = [p for p in positions if _pos_direction(p) == direction]
        if not positions:
            return
        tag = f" {direction}" if direction else ""
        if action == "close":
            print(f"  {console.warn('⊗ [Mesa] Cerrando')} {len(positions)} posición(es)"
                  f"{tag} de {symbol}: {console.dim(reason)}")
            for _ in range(len(positions)):
                res = self.client.close_position(symbol, direction=direction)
                if not res or not res.get("success"):
                    print(console.dim(f"    Cierre detenido: "
                                      f"{(res or {}).get('error') or 'sin más posiciones'}"))
                    break
        else:  # reduce: cierra una sola posición del símbolo (del lado indicado)
            print(f"  {console.warn('⊖ [Mesa] Reduciendo')} {symbol}{tag} "
                  f"(cierra 1 posición): {console.dim(reason)}")
            res = self.client.close_position(symbol, direction=direction)
            if not res or not res.get("success"):
                print(console.dim(f"    No se pudo reducir: "
                                  f"{(res or {}).get('error') or 'sin posición'}"))

    def _hedge_position(self, symbol: str, net_side: str, reason: str):
        """Cubre el sesgo neto del símbolo abriendo una orden OPUESTA por el
        volumen neto (sin SL/TP). `net_side` (BUY/SELL) es el lado del libro a
        neutralizar; se abre el contrario. Solo tiene efecto real en cuentas
        hedging; en netting el RiskBook ya degrada la cobertura a 'reduce'."""
        if net_side not in ("BUY", "SELL"):
            print(console.dim(f"  [Mesa] Cobertura {symbol}: sin sesgo neto definido, se omite."))
            return
        positions = self.client.get_positions(symbol) or []
        long_vol = sum(_pos_to_float(_pos_get(p, "volume"))
                       for p in positions if _pos_direction(p) == "BUY")
        short_vol = sum(_pos_to_float(_pos_get(p, "volume"))
                        for p in positions if _pos_direction(p) == "SELL")
        net_vol = abs(long_vol - short_vol)
        if net_vol <= 0:
            print(console.dim(f"  [Mesa] Cobertura {symbol}: sin volumen neto que cubrir."))
            return
        sym = self.client.get_symbol_info(symbol)
        vmin = getattr(sym, "volume_min", 0.01) or 0.01
        vstep = getattr(sym, "volume_step", 0.01) or 0.01
        steps = math.floor(net_vol / vstep + 1e-9)
        volume = max(vmin, round(steps * vstep, 10))
        opposite = "SELL" if net_side == "BUY" else "BUY"
        print(f"  {console.warn('⛨ [Mesa] Cobertura')} {symbol}: abre {console.side(opposite)} "
              f"{volume} para neutralizar el neto {net_side}. {console.dim(reason)}")
        result = self.client.place_order(
            symbol=symbol, volume=volume, order_type=opposite,
            stop_loss=None, take_profit=None, comment=f"hedge {symbol}",
        )
        if result and result.get("success"):
            print(f"    {console.ok('✓ Cobertura abierta')}: ticket {result.get('order')} "
                  f"{console.dim('@')} {result.get('price')}")
        else:
            err = (result or {}).get("error") or (result or {}).get("comment") or "sin respuesta"
            print("    " + console.err(f"✗ No se pudo cubrir: {err}"))

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

    def _print_coordination(self, result: dict, snapshot: dict, signals: dict = None):
        """Tabla de la mesa: por símbolo, qué propuso el especialista (señal) y
        qué decidió la mesa (veredicto/prioridad/asignación/gestión + clamp)."""
        signals = signals or {}
        exp = snapshot.get("total_exposure_pct", 0)
        tope = snapshot.get("max_total_exposure_pct", 0)
        print(console.kv("Exposición total",
                         f"{exp:.1%} {console.dim(f'/ tope {tope:.0%}')}"))
        if result.get("rationale"):
            print(console.kv("Razón mesa", console.dim(result["rationale"])))

        decisions = result.get("decisions", [])
        if not decisions:
            print(console.dim("  (sin decisiones)"))
            return

        sym_info = snapshot.get("symbols", {})
        headers = ["Símbolo", "Señal", "Neto", "Veredicto", "Prio", "Asig", "Gestión", "Motivo/clamp"]
        aligns = ["<", "<", "<", "<", ">", ">", "<", "<"]
        rows = []
        for d in sorted(decisions, key=lambda x: x.get("priority", 99)):
            sym = d.get("symbol")
            sig = signals.get(sym) or {}
            sig_action = sig.get("action", "—")
            nd = sym_info.get(sym, {}).get("net_direction", "FLAT")
            approve = d.get("approve")
            verdict = ("APROBADA", console.ok) if approve else ("vetada", console.err)
            pos_action = d.get("position_action", "hold")
            md = f"→{d['manage_direction']}" if d.get("manage_direction") else ""
            pos_cell = f"{pos_action}{md}"
            pos_style = console.warn if pos_action in ("close", "reduce", "hedge") else console.dim
            motivo = d.get("clamp") or ""
            rows.append([
                sym,
                (sig_action, console.side),
                (nd, console.dim),
                verdict,
                str(d.get("priority", "")),
                f"{d.get('allocation_pct', 0):.0%}",
                (pos_cell, pos_style),
                (motivo, console.dim) if motivo else "",
            ])
        for line in console.table(headers, rows, aligns=aligns, indent="  "):
            print(line)

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
            day_start_equity=self._window_start_equity, in_cooldown=in_cooldown)
        result = self.coordinator.decide(
            snapshot, signals, self.agents_overview(),
            news_context=self._coordinator_news())
        self._store_coordination(snapshot, result)
        return self.last_coordination

    # ----- Junta periódica (revisión global) -----

    def _run_junta(self):
        """Junta horaria de la mesa: revisión GLOBAL del libro aunque la rotación
        no haya tenido actividad. Convoca siempre (sin la condición de "omitir si
        no hay señales"), aplica las guardias deterministas (reversión/hard-stop)
        sobre las posiciones abiertas y gestiona (close/reduce/hedge) respetando
        `can_close`. No abre entradas nuevas (usaría señales potencialmente
        viejas): para eso está la rotación."""
        if self.risk_book is None:
            return
        in_cooldown = self._risk_cooldown_active()
        snapshot = self.risk_book.snapshot(
            self.client, self.agents,
            day_start_equity=self._window_start_equity, in_cooldown=in_cooldown)

        print("\n" + console.header("JUNTA DE LA MESA · revisión global del libro", char="#"))
        self.last_junta_at = time.strftime("%Y-%m-%d %H:%M:%S")

        # Sin coordinador: resumen determinista (sin LLM).
        if self.coordinator is None:
            self._print_coordination({"rationale": "junta sin coordinador (resumen determinista)",
                                      "decisions": []}, snapshot)
            self._store_coordination(
                snapshot, {"rationale": "junta sin coordinador (resumen determinista)",
                           "decisions": []})
            return

        state = bot_state.get_state()
        signals = dict(state.get("signals", {}) or {})
        with _Spinner("  Junta: revisando el libro"):
            result = self.coordinator.decide(
                snapshot, signals, self.agents_overview(),
                news_context=self._coordinator_news())
        self._print_coordination(result, snapshot, signals)
        self._store_coordination(snapshot, result)
        # Gestiona posiciones abiertas (no abre entradas en la junta).
        self._execute_decisions(result, signals, manage_only=True)

    # ----- Reporte periódico -----

    def _run_report(self):
        """Genera el reporte de estado y lo envía (si SMTP está activo). Siempre
        lo imprime y lo guarda para el dashboard; el envío puede estar apagado."""
        account = self.client.get_account_info() or {}
        snapshot = None
        if self.risk_book is not None:
            try:
                snapshot = self.risk_book.snapshot(
                    self.client, self.agents,
                    day_start_equity=self._window_start_equity,
                    in_cooldown=self._risk_cooldown_active())
            except Exception:  # noqa: BLE001 — el reporte nunca debe tumbar el bot
                snapshot = None
        state = bot_state.get_state()

        from core.reporting import build_report
        report = build_report(
            account=account, snapshot=snapshot,
            coordination=self.last_coordination,
            agents_overview=self.agents_overview(),
            closed_trades=state.get("closed_trades", []))
        self.last_report = report
        self.last_report_at = time.strftime("%Y-%m-%d %H:%M:%S")

        print("\n" + console.header("REPORTE PERIÓDICO"))
        print(report["text"])

        from core.mailer import send_report
        send_report(report["subject"], report["text"], report.get("html"),
                    cfg=self.schedule_cfg)

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
                # False = mercado cerrado (fin de semana / fuera de sesión): el
                # agente figura como no disponible y no analiza hasta que reabra.
                "market_open": self.client.is_market_open(agent.symbol),
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
            "llm_can_close": self.risk_book.llm_can_close,
            "max_total_exposure_pct": self.risk_book.max_total_exposure_pct,
            "max_symbol_allocation_pct": self.risk_book.max_symbol_allocation_pct,
            "max_net_direction_pct": self.risk_book.max_net_direction_pct,
            "reversal_drawdown_pct": self.risk_book.reversal_drawdown_pct,
            "max_symbol_loss_pct": self.risk_book.max_symbol_loss_pct,
            "min_hold_seconds": self.risk_book.min_hold_seconds,
            "last_coordination": self.last_coordination,
            "last_coordination_at": self.last_coordination_at,
            "last_junta_at": self.last_junta_at,
            "last_report_at": self.last_report_at,
            "rotation_seconds": self.rotation_seconds,
            "news_poll_seconds": self.news_poll_seconds,
            "junta_interval_seconds": self.junta_interval_seconds,
            "report_interval_seconds": self.report_interval_seconds,
        }
