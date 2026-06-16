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
from typing import Optional

from core import console
from core.clock import broker_now
from core.state import bot_state
from core.bot_state import Trade
from core.logger import log_trade, log_closed_trade, log_equity, read_close_stats
from core.trade_metrics import calc_trade_metrics
from agents.base_agent import AgentParams
from agents.positions import _pos_get, _pos_to_float, _pos_direction
from clients.mt4_client import describe_mt_error


# Límites de seguridad para que la optimización no deje a un agente en una
# configuración absurda. (min, max)
PARAM_BOUNDS = {
    "min_confidence": (0.50, 0.85),
    "min_rr": (1.0, 3.0),
    "atr_sl_mult": (1.0, 3.5),
    "atr_tp_mult": (1.5, 5.0),
}
MIN_SAMPLES_TO_TUNE = 5   # nº mínimo de señales evaluadas para ajustar

# Tras tocar el límite de pérdida diaria el bot NO se detiene: entra en cooldown,
# deja de abrir operaciones y espacia el análisis a este intervalo para no perder
# contexto/memoria mientras espera a que las posiciones abiertas se cierren.
RISK_COOLDOWN_ANALYSIS_INTERVAL = 15 * 60

# Cuando el broker rechaza una orden con error 133 (TRADE_DISABLED: el símbolo no
# acepta aperturas — sesión cerrada, "close only", rollover del contrato…), el bot
# deja de analizar ese símbolo durante este tiempo. Evita malgastar llamadas al LLM
# y llenar el log de 133 reintentando cada rotación. Se limpia al reactivar el
# agente desde el dashboard. MODE_TRADEALLOWED no siempre refleja este estado, por
# eso el back-off se dispara con el rechazo real del broker.
TRADE_DISABLED_BACKOFF_SECONDS = 30 * 60


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
        # Coordinador (mesa de dirección): SIEMPRE presente. Todo el flujo es
        # coordinado — el RiskBook (topes duros) es la tesorería y el
        # CoordinatorAgent (LLM, con fail-safe determinista) decide go/no-go.
        if coordinator is None or risk_book is None:
            raise ValueError("AgentOrchestrator requiere coordinator y risk_book: "
                             "todo el flujo es coordinado (no hay ruta clásica).")
        self.coordinator = coordinator
        self.risk_book = risk_book
        # Contadores por agente: base para optimizar. Se restauran de la DB para
        # que el resumen del dashboard (señales/trades/holds) SOBREVIVA a los
        # reinicios del bot (antes vivían solo en memoria y se perdían).
        self.stats = {a.name: {"signals": 0, "trades": 0, "holds": 0} for a in agents}
        try:
            from core.db import load_agent_stats
            saved = load_agent_stats()
            for name in self.stats:
                if name in saved:
                    self.stats[name] = saved[name]
        except Exception:
            pass
        # Inyecta la directiva del perfil activo (riesgo + horizonte) en los prompts
        # del especialista y de la mesa, para que el LLM cambie de disposición desde
        # el primer ciclo (no solo al mover el selector en vivo).
        self._refresh_trading_directives()
        # Nota de dirección (instrucción libre del responsable, fijada desde el chat
        # del asistente) que la mesa pondera en sus decisiones. Se recarga del .env
        # (DIRECTOR_NOTE) para que SOBREVIVA a los reinicios del bot.
        self.coordinator.director_note = (os.getenv("DIRECTOR_NOTE", "") or "").strip()
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
        # Heartbeat de cuenta: refresca equity/balance y lo emite por WebSocket
        # cada pocos segundos, INDEPENDIENTE del ciclo pesado (que tarda ≥rotación
        # + el análisis LLM). Así el P/L flotante del dashboard se mueve en vivo.
        self.account_poll_seconds = float(os.getenv("ACCOUNT_POLL_SECONDS", "5") or 5)
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
        # Motivo de cierre pendiente por símbolo {symbol: "Cierre mesa"|...}: lo fija
        # el bot cuando cierra/reduce a propósito (gestión de la mesa), para que
        # _detect_closed_trades lo registre en el historial el ciclo siguiente. Si
        # no hay motivo pendiente, el cierre lo provocó el bróker (SL/TP) y se
        # infiere por proximidad de precio.
        self._pending_close_reasons: dict = {}
        # Posiciones a las que ya se les tomó beneficio parcial. La clave NO es el
        # ticket (MT4 reasigna ticket al remanente tras un cierre parcial) sino
        # symbol|dirección|entry, estable para el "linaje" de la posición, para no
        # volver a parcializar el remanente. Se poda con las posiciones vivas.
        self._partial_taken: set = set()
        # Momento del último análisis por símbolo, para espaciarlo al estar en
        # el máximo de posiciones abiertas.
        self._last_analysis_at: dict = {}
        # Símbolo -> deadline (time.monotonic) hasta el que NO se analiza por
        # haber recibido un error 133 (TRADE_DISABLED) del broker al abrir orden.
        self._trade_disabled_until: dict = {}
        # Símbolos a forzar en la PRÓXIMA rotación (reactivados/añadidos desde el
        # dashboard): se analizan saltándose el throttle/cooldown/back-off, igual
        # que una noticia RED. Lo alimenta el hilo del API; lo consume el del loop.
        self._pending_force: set = set()
        self._pending_force_lock = threading.Lock()
        # Nº de rotación (para la cabecera de cada ciclo en la terminal).
        self._rotation_count = 0

    # ----- Ejecución -----

    @staticmethod
    def _due(last: float, interval: float, now: float) -> bool:
        """True si ya transcurrió `interval` segundos desde `last` (0 = nunca)."""
        return interval > 0 and (now - last) >= interval

    def _broadcast_account(self, account_info: dict):
        """Emite la cuenta (equity/balance/…) por WebSocket para que el dashboard
        actualice el P/L flotante en vivo. Import perezoso para evitar el ciclo
        con api.server; nunca debe tumbar el bot."""
        try:
            from api.server import broadcast_account_update
            broadcast_account_update(account_info)
        except Exception:  # noqa: BLE001
            pass

    def _broadcast_state(self):
        """Emite el estado completo (posiciones incluidas) por WebSocket. Import
        perezoso para evitar el ciclo con api.server; nunca debe tumbar el bot."""
        try:
            from api.server import broadcast_state_update
            broadcast_state_update()
        except Exception:  # noqa: BLE001
            pass

    def _account_heartbeat(self):
        """Bucle daemon: refresca la cuenta Y las posiciones y las emite cada
        account_poll_seconds, independiente del ciclo pesado (rotación + análisis
        LLM), para que el P/L flotante y los cierres por TP/SL se reflejen en vivo
        en el dashboard sin esperar a la siguiente rotación. Sigue emitiendo aunque
        el bot esté en pausa (las posiciones abiertas siguen fluctuando)."""
        if self.account_poll_seconds <= 0:
            return
        while True:
            time.sleep(self.account_poll_seconds)
            try:
                info = self.client.get_account_info()
                if info:
                    bot_state.update_account(info)
                    self._broadcast_account(info)
                # Sincroniza TODAS las posiciones del bróker: refleja en vivo los
                # cierres por TP/SL y el precio/P&L actual. La detección de cierres
                # para el historial sigue en la rotación (hilo único, sin carrera).
                positions = self.client.get_positions()
                bot_state.sync_all_positions(positions or [])
                self._broadcast_state()
            except Exception:  # noqa: BLE001 — un fallo puntual no debe matar el hilo
                pass

    def run_forever(self, poll_seconds: int = 60):
        bot_state.set_bot_running(True)
        self.rotation_seconds = poll_seconds
        # Heartbeat de cuenta en hilo daemon: P/L flotante en vivo en el dashboard.
        threading.Thread(target=self._account_heartbeat, daemon=True,
                         name="account-heartbeat").start()
        # Arranque: la mesa revisa la cuenta y la disponibilidad de agentes
        # antes de la primera rotación.
        self._startup_review()
        cycle = 0
        try:
            while True:
                account_info = self.client.get_account_info()
                if account_info:
                    bot_state.update_account(account_info)
                    self._broadcast_account(account_info)
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

                # Símbolos reactivados/añadidos desde el dashboard: se fuerzan en
                # esta rotación para que se analicen ya, sin esperar al throttle.
                with self._pending_force_lock:
                    if self._pending_force:
                        forced_symbols = forced_symbols | self._pending_force
                        self._pending_force.clear()

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
        try:
            snap = self.risk_book.snapshot(
                self.client, self.agents, in_cooldown=False,
                **self._daily_pnl_kwargs())
            exp = snap.get("total_exposure_pct", 0)
            tope = snap.get("max_total_exposure_pct", 0)
            print(console.kv("Equity", console.money(snap.get("equity", 0))))
            # Apalancamiento de la cuenta (1:N) reportado por el broker.
            leverage = (self.client.get_account_info() or {}).get("leverage")
            if leverage:
                print(console.kv("Apalancamiento", f"1:{int(leverage)}"))
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

        print(console.kv("Modo", console.accent("mesa de dirección")))
        print(console.dim(f"  Cadencias: rotación {self.rotation_seconds}s · "
                          f"noticias {self.news_poll_seconds // 60}min · "
                          f"junta {self.junta_interval_seconds // 60}min · "
                          f"reporte {self.report_interval_seconds // 60}min."))

    def _run_rotation(self, forced_symbols: set):
        """Una rotación: ciclo coordinado (recolectar → coordinar → ejecutar).
        Los símbolos en `forced_symbols` (noticia RED) se analizan saltándose el
        throttle."""
        self._rotation_count += 1
        forced_tag = (console.warn(f" · forzados: {', '.join(sorted(forced_symbols))}")
                      if forced_symbols else "")
        print("\n" + console.rule(
            f"Rotación #{self._rotation_count} · {time.strftime('%H:%M:%S')}{forced_tag}",
            style=console.info))
        # Gestión dinámica de posiciones abiertas (trailing stop / cierre parcial),
        # determinista y previa al análisis: asegura beneficio en lo ya abierto.
        self._manage_position_lifecycle()
        self._run_coordinated_cycle(force_symbols=forced_symbols)
        # Persiste los contadores por agente para que el resumen del dashboard
        # sobreviva a los reinicios (señales/trades/holds). Fail-safe.
        self._persist_stats()

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

    def _risk_window_start_wall(self):
        """Epoch (reloj de pared) aproximado en que empezó la ventana de riesgo
        actual, derivado del `monotonic` (que es lo que se persiste). None si la
        ventana aún no se ha fijado (guardia apagada o primera vuelta)."""
        if self._risk_window_start is None:
            return None
        return time.time() - (time.monotonic() - self._risk_window_start)

    def _daily_pnl_kwargs(self) -> dict:
        """Parámetros del 'P/L del día' para `snapshot()`: el equity de referencia
        y el RANGO temporal al que aplica (la ventana móvil de riesgo). Con la
        guardia de pérdida diaria apagada (MAX_DAILY_LOSS_PCT=0) no hay ventana,
        así que no se reporta rango y el P/L del día queda en n/a."""
        return {
            "day_start_equity": self._window_start_equity,
            "day_window_seconds": (self.risk_loss_window_seconds
                                   if self.max_daily_loss_pct else None),
            "day_window_start_ts": self._risk_window_start_wall(),
        }

    def _throttled(self, symbol: str, interval: float) -> bool:
        """True si aún no han pasado `interval` segundos desde el último análisis
        del símbolo (sirve para espaciar análisis en máximo de posiciones/cooldown)."""
        return (time.time() - self._last_analysis_at.get(symbol, 0)) < interval

    @staticmethod
    def _disabled_error_code(result: dict) -> Optional[str]:
        """Devuelve el código del error si place_order fue rechazado por una causa
        de configuración RECUPERABLE que conviene aparcar (no reintentar cada
        rotación): 133 (TRADE_DISABLED, símbolo no operable) o 4109 (TRADE_NOT_ALLOWED,
        trading automático apagado en el terminal MT4). None en otro caso.
        El EA devuelve el error crudo como 'ERROR|OrderSend failed, error=NNNN'."""
        if not result or result.get("success"):
            return None
        err = str(result.get("error") or "")
        for code in ("133", "4109"):
            if f"error={code}" in err:
                return code
        return None

    @classmethod
    def _is_trade_disabled_error(cls, result: dict) -> bool:
        """True si place_order fue rechazado por una causa de configuración que se
        aparca con back-off (133 símbolo no operable o 4109 AutoTrading apagado)."""
        return cls._disabled_error_code(result) is not None

    def _trade_disabled_remaining(self, symbol: str) -> float:
        """Segundos restantes del back-off por error 133 del símbolo (0 si no hay)."""
        deadline = self._trade_disabled_until.get(symbol)
        if deadline is None:
            return 0.0
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            self._trade_disabled_until.pop(symbol, None)
            return 0.0
        return remaining

    def _mark_trade_disabled(self, symbol: str, code: str = "133") -> None:
        """Aparca el símbolo tras un rechazo de configuración (133/4109): deja de
        analizarlo durante el back-off para no malgastar LLM ni inundar el log
        reintentando. El mensaje distingue la causa (símbolo vs terminal)."""
        self._trade_disabled_until[symbol] = (
            time.monotonic() + TRADE_DISABLED_BACKOFF_SECONDS)
        mins = TRADE_DISABLED_BACKOFF_SECONDS // 60
        if code == "4109":
            # Config del TERMINAL (afecta a toda la cuenta), no del símbolo: el
            # arreglo es activar AutoTrading, no esperar a una sesión de mercado.
            print("  " + console.warn(
                f"⏸ {symbol}: trading automático DESHABILITADO en MT4 (4109). "
                f"Activa el botón «AutoTrading» (Ctrl+E) y marca «Allow live "
                f"trading» en las propiedades del EA (F7 → Common). Símbolo "
                f"aparcado {mins} min (reactívalo desde el dashboard al corregirlo)."))
        else:
            print("  " + console.warn(
                f"⏸ {symbol}: el broker rechazó la orden (133 TRADE_DISABLED). "
                f"Símbolo aparcado {mins} min "
                f"(reactívalo desde el dashboard para reintentar antes)."))

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
        disappeared = [snap for ticket, snap in prev.items() if ticket not in current]
        if disappeared:
            # El motivo pendiente (si lo hay) lo fijó el bot al cerrar/reducir; se
            # consume una vez y se aplica a todas las posiciones cerradas esta pasada.
            pending = self._pending_close_reasons.pop(symbol, "")
            for snap in disappeared:
                self._record_closed_trade(symbol, snap, pending)

        # Reconstruye la instantánea arrastrando `seen_at` (reloj de pared local
        # del primer avistamiento) para medir la duración sin depender del epoch
        # del bróker (otra zona horaria). Tickets nuevos: se sellan con "ahora".
        rebuilt = {}
        now = broker_now()
        for t, p in current.items():
            snap = self._snapshot(p)
            prev_snap = prev.get(t)
            snap["seen_at"] = (prev_snap or {}).get("seen_at") or now
            rebuilt[t] = snap
        self._prev_positions[symbol] = rebuilt

    @staticmethod
    def _snapshot(pos) -> dict:
        return {
            "direction": _pos_direction(pos),
            "volume": _pos_to_float(_pos_get(pos, "volume")),
            "open_price": _pos_to_float(_pos_get(pos, "open_price", "price_open")),
            "current_price": _pos_to_float(_pos_get(pos, "current_price")),
            "profit": _pos_to_float(_pos_get(pos, "profit")),
            "open_time": _pos_get(pos, "open_time"),
            "sl": _pos_to_float(_pos_get(pos, "stop_loss", "sl")),
            "tp": _pos_to_float(_pos_get(pos, "take_profit", "tp")),
        }

    @staticmethod
    def _infer_close_reason(snap: dict, exit_price: float) -> str:
        """Infiere el motivo de cierre comparando el último precio observado con
        el SL/TP de la posición (con tolerancia para absorber el último tick).
        Devuelve "Stop Loss" / "Take Profit" o "Cierre" (manual/bróker, desconocido)."""
        direction = (snap.get("direction") or "").upper()
        sl, tp = snap.get("sl"), snap.get("tp")
        if not exit_price:
            return "Cierre"
        eps = abs(exit_price) * 0.0005  # ~5 pb de margen
        if direction == "BUY":
            if sl and exit_price <= sl + eps:
                return "Stop Loss"
            if tp and exit_price >= tp - eps:
                return "Take Profit"
        elif direction == "SELL":
            if sl and exit_price >= sl - eps:
                return "Stop Loss"
            if tp and exit_price <= tp + eps:
                return "Take Profit"
        return "Cierre"

    def _record_closed_trade(self, symbol: str, snap: dict, reason: str = ""):
        """Registra en el estado una posición que ya no aparece.

        El P/L es el último flotante observado antes de desaparecer (aprox. del
        realizado; el broker podría diferir por el último tick/slippage). `reason`
        viene de la mesa cuando el bot la cerró a propósito; si está vacío, el
        cierre lo provocó el bróker y se infiere del SL/TP."""
        # Apertura y duración ancladas al RELOJ LOCAL (primer avistamiento por el
        # bot), NO al open_time del bróker: su epoch viene en la zona del servidor
        # MT y, mezclado con datetime.now() local, daba horas desplazadas y
        # duraciones NEGATIVAS (open posterior a close). Para posiciones ya
        # abiertas al arrancar, seen_at = primer avistamiento (sesgo conservador),
        # nunca una duración negativa.
        now = broker_now()
        seen_at = snap.get("seen_at")
        open_dt = seen_at if isinstance(seen_at, datetime) else now
        open_iso = open_dt.isoformat()
        duration = max(0, int((now - open_dt).total_seconds()))
        exit_price = snap.get("current_price") or snap.get("open_price") or 0
        close_reason = reason or self._infer_close_reason(snap, exit_price)
        trade = Trade(
            symbol=symbol,
            action=snap.get("direction", "?"),
            entry_price=snap.get("open_price", 0.0),
            exit_price=exit_price or None,
            volume=snap.get("volume", 0.0),
            pnl=snap.get("profit", 0.0),
            open_time=open_iso,
            close_time=now.isoformat(),
            duration_seconds=duration,
        )
        bot_state.add_closed_trade(trade)
        # Persistir en la DB para que el rendimiento sobreviva al reinicio.
        log_closed_trade(
            symbol=symbol,
            action=snap.get("direction", "?"),
            volume=snap.get("volume", 0.0),
            entry_price=snap.get("open_price", 0.0),
            exit_price=exit_price,
            pnl=snap.get("profit", 0.0),
            commission=0.0,  # se rellenará cuando el broker lo devuelva
            duration_seconds=duration,
            close_reason=close_reason,
            platform=self.platform,
        )
        print(f"  {console.accent('⟳ Cierre registrado')}: {console.side(trade.action)} "
              f"{symbol} | P/L≈{console.pnl(trade.pnl)} | {console.dim(close_reason)}")

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

    def _gather_signals_parallel(self, force_symbols: set, agents: list = None) -> dict:
        """Recolecta las señales de los agentes EN PARALELO.

        Lanza `_gather_signal` de cada agente en su propio hilo (la parte lenta,
        la llamada al LLM, se solapa). Los accesos al EA se serializan en
        `MT4Client._send` (canal único). La salida de cada agente se captura en un
        buffer aislado y se vuelca en orden al terminar, para no entremezclar los
        reportes. `agents` limita la recolección a esos especialistas (los
        activos); por defecto todos. Devuelve ``{symbol: signal}`` igual que la
        ruta secuencial."""
        agents = self.agents if agents is None else agents
        real_out = sys.stdout
        router = (real_out if isinstance(real_out, _ThreadRoutedStdout)
                  else _ThreadRoutedStdout(real_out))
        buffers = {a.name: io.StringIO() for a in agents}
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
        n = len(agents)
        try:
            with _Spinner(f"  Analizando {n} símbolos en paralelo"):
                with ThreadPoolExecutor(max_workers=n,
                                        thread_name_prefix="analyze") as ex:
                    collected = list(ex.map(work, agents))
        finally:
            sys.stdout = real_out

        # Vuelca los reportes en el orden de los agentes (lectura estable).
        for agent in agents:
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

        # Back-off por error 133 (TRADE_DISABLED): el broker rechazó una orden
        # reciente de este símbolo; no analizamos hasta que expire (o se reactive
        # el agente). Una noticia RED (force) lo salta para re-evaluar.
        remaining = self._trade_disabled_remaining(symbol)
        if remaining > 0 and not force:
            print("  " + console.warn(
                f"⏸ {symbol}: aparcado por rechazo 133 del broker "
                f"(reintento en {int(remaining // 60)} min)."))
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

        # SIEMPRE se analiza (salvo cooldown): sin análisis no hay confianza con la
        # que decidir, así que NO se omite por estar "al máximo de posiciones". El nº
        # máximo de posiciones lo gobierna ahora la MESA (RiskBook.max_open_positions,
        # derivado del perfil de riesgo + horizonte): el especialista propone su señal
        # cada rotación y la mesa decide si abre, mantiene o gestiona dentro de ese
        # tope. Único espaciado vigente: el cooldown por pérdida diaria, donde NO se
        # abre nada de todos modos, así que reanalizar a cada tick sería malgastar LLM;
        # se sigue mirando de vez en cuando para no perder contexto/memoria.
        in_cooldown = self._risk_cooldown_active()
        if in_cooldown and not force:
            if self._throttled(symbol, RISK_COOLDOWN_ANALYSIS_INTERVAL):
                print(console.dim(
                    f"  ⏸ Cooldown por pérdida diaria; análisis aplazado "
                    f"(cada {RISK_COOLDOWN_ANALYSIS_INTERVAL // 60} min, "
                    f"esperando el cierre de posiciones)."))
                return None
        elif force and in_cooldown:
            print("  " + console.warn("⚡ análisis forzado por noticia pese al cooldown "
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
        label = "📨 reporte → mesa"
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
        """Escala el lote base por `scale` y lo redondea al step del símbolo, con
        suelo en el volumen mínimo. La asignación de capital module a la baja
        (scale<1) y el size_mult de la mesa puede modular al alza (scale>1). El
        techo real lo ponen el size_mult_max del RiskBook y el ajuste por margen
        libre (_fit_volume_to_margin) aguas abajo; aquí solo se evita un lote
        degenerado (~0) con un suelo en 0.1x."""
        scale = max(0.1, scale)
        sym = self.client.get_symbol_info(symbol)
        vmin = getattr(sym, "volume_min", 0.01) or 0.01
        vstep = getattr(sym, "volume_step", 0.01) or 0.01
        steps = math.floor((base_volume * scale) / vstep + 1e-9)
        lot = round(steps * vstep, 10)
        return max(vmin, lot)

    def _contract_size(self, symbol: str) -> float:
        """Tamaño de contrato del símbolo (MT4: `contract_size`). Fallback 1.0
        (cripto). Se usa para estimar el margen cuando el EA no lo reporta."""
        info = self.client.get_symbol_info(symbol)
        for attr in ("trade_contract_size", "contract_size"):
            v = getattr(info, attr, None)
            if v:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
        return 1.0

    def _fit_volume_to_margin(self, symbol: str, volume: float,
                              order_type: str, tick) -> float:
        """Acota el volumen al margen libre para evitar el error 134 (fondos
        insuficientes): el bróker rechaza la orden entera si el margen no alcanza.

        Usa el margen EXACTO por lote del bróker (`margin_required`, vía EA con
        MODE_MARGINREQUIRED) si está disponible; si no, lo estima como
        nocional/leverage. Deja un colchón del 5% (spread/comisión/fluctuación) y
        redondea hacia abajo al step. Devuelve 0.0 si ni el lote mínimo cabe;
        si no hay dato fiable de margen, devuelve el volumen sin tocar (que
        decida el bróker)."""
        account = self.client.get_account_info() or {}
        free_margin = float(account.get("free_margin") or 0.0)
        if free_margin <= 0:
            return volume
        sym = self.client.get_symbol_info(symbol)
        vmin = getattr(sym, "volume_min", 0.01) or 0.01
        vstep = getattr(sym, "volume_step", 0.01) or 0.01

        margin_per_lot = float(getattr(sym, "margin_required", 0.0) or 0.0)
        if margin_per_lot <= 0:
            # Estimación: margen ≈ nocional / leverage, con el precio del lado de entrada.
            price = 0.0
            if tick:
                price = tick.ask if str(order_type).upper() == "BUY" else tick.bid
            leverage = float(account.get("leverage") or 0) or 1.0
            margin_per_lot = (price * self._contract_size(symbol) / leverage) if price > 0 else 0.0
        if margin_per_lot <= 0:
            return volume

        budget = free_margin * 0.95
        steps = math.floor((budget / margin_per_lot) / vstep + 1e-9)
        max_lots = round(steps * vstep, 10)
        if max_lots < vmin:
            return 0.0
        return min(volume, max_lots)

    def _apply_tp_rr(self, agent, signal: dict, tp_rr: float) -> bool:
        """Recalcula el `take_profit` de la señal al R:R objetivo `tp_rr` que fija
        la mesa, conservando entry/stop_loss (la distancia de riesgo del
        especialista). Mantiene el lado correcto del nivel (BUY: TP>entry;
        SELL: TP<entry). No toca el SL. Devuelve True si ajustó el TP.

        Con `tp_rr` <= 0 o niveles incompletos no hace nada (se respeta el TP del
        especialista)."""
        if not tp_rr or tp_rr <= 0:
            return False
        entry = signal.get("entry") or 0
        sl = signal.get("stop_loss") or 0
        if entry <= 0 or sl <= 0:
            return False
        risk = abs(entry - sl)
        if risk <= 0:
            return False
        sign = 1 if signal["action"] == "BUY" else -1
        sym_info = self.client.get_symbol_info(agent.symbol)
        digits = getattr(sym_info, "digits", 5) if sym_info else 5
        signal["take_profit"] = round(entry + sign * tp_rr * risk, digits)
        return True

    def _open_from_signal(self, agent, signal, scale: float = 1.0,
                          enforce_max_positions: bool = True,
                          min_rr_override: float = None) -> bool:
        """Valida y ejecuta una entrada a partir de la señal. `scale` modula el lote
        base según la asignación del coordinador y su size_mult: <1 lo reduce, >1 lo
        agranda (entrada de convicción / piramidación). `enforce_max_positions`
        en False (ruta coordinada) delega el límite global de número de posiciones a
        la mesa, que ya gobierna la exposición real (RiskBook) y puede abrir más si lo
        considera necesario. `min_rr_override` (ruta coordinada) exige ese R:R en la
        validación cuando la mesa fijó un TP objetivo (tp_rr) más corto que el del
        especialista. Devuelve True si la orden se ejecutó."""
        symbol = agent.symbol
        base_volume = agent.resolve_volume(self.client, signal)
        # Solo el lote base exacto (scale == 1) evita el redondeo al step; cualquier
        # otra escala —a la baja por asignación o al alza por size_mult— se aplica.
        volume = (base_volume if abs(scale - 1.0) < 1e-9
                  else self._scale_volume(symbol, base_volume, scale))

        # Contexto extra para la validación: spread actual (filtro de coste) y
        # nº de posiciones de TODA la cuenta (límite global, no por símbolo).
        tick = self.client.get_tick(symbol)
        positions = self.client.get_positions(symbol)
        spread_points = self._spread_points(symbol, tick)
        total_open = len(self.client.get_positions() or [])
        if not agent.validate(signal, positions, tick=tick,
                              spread_points=spread_points, total_open_positions=total_open,
                              enforce_max_positions=enforce_max_positions,
                              min_rr=min_rr_override):
            print("  " + console.warn("⚠ Señal no validada para ejecución."))
            return False

        # Ajuste por margen libre: evita el error 134 (fondos insuficientes)
        # recortando el lote a lo que el margen permite, o saltando la entrada.
        fitted = self._fit_volume_to_margin(symbol, volume, signal["action"], tick)
        if fitted <= 0:
            print("  " + console.warn("⚠ Margen libre insuficiente ni para el lote mínimo; se omite la entrada."))
            return False
        if fitted < volume:
            print(console.dim(f"  Lote ajustado por margen libre: {volume} → {fitted}"))
            volume = fitted

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
        elif self._disabled_error_code(result):
            # Rechazo de configuración recuperable (133 símbolo no operable / 4109
            # AutoTrading apagado): aparcar el símbolo en vez de reintentar cada
            # rotación. El mensaje del aparcado ya explica la causa y el arreglo.
            code = self._disabled_error_code(result)
            print("  " + console.err(
                f"✗ Error al ejecutar orden: {describe_mt_error(result.get('error'))}"))
            self._mark_trade_disabled(symbol, code)
        else:
            err = (result or {}).get("error") or (result or {}).get("comment") or "sin respuesta"
            print("  " + console.err(f"✗ Error al ejecutar orden: {describe_mt_error(err)}"))
        return False

    # ----- Ciclo coordinado (mesa de dirección) -----

    def _run_coordinated_cycle(self, force_symbols: set = frozenset()):
        """Ciclo con coordinador: recolectar señales -> coordinar cartera ->
        ejecutar por prioridad (entradas aprobadas + cierres/reducciones).

        `force_symbols` (noticia RED) fuerza el análisis de esos símbolos aunque
        estén throttled; la coordinación y el clamp se aplican igual a todos."""
        # Fase 1/3: recolectar las señales de los especialistas ACTIVOS. Los
        # agentes desactivados desde el dashboard se omiten: no se analizan ni
        # proponen entradas (sus posiciones abiertas las sigue gobernando la mesa
        # a partir del snapshot, que considera toda la cartera).
        active = [a for a in self.agents if getattr(a, "enabled", True)]
        disabled = [a for a in self.agents if not getattr(a, "enabled", True)]
        if disabled:
            print(console.dim("  Agentes desactivados (omitidos): "
                              + ", ".join(f"{a.name} [{a.symbol}]" for a in disabled)))
        parallel = self.parallel_analysis and len(active) > 1
        modo = console.dim(" (en paralelo)") if parallel else ""
        print(console.dim("  [1/3] Recolección de señales") + modo)
        if parallel:
            signals = self._gather_signals_parallel(force_symbols, active)
        else:
            signals = {}
            for agent in active:
                sig = self._gather_signal(agent, force=agent.symbol in force_symbols)
                if sig:
                    signals[agent.symbol] = sig

        # Fase 2/3: coordinar. Snapshot determinista + decisión LLM + clamp duro.
        in_cooldown = self._risk_cooldown_active()
        snapshot = self.risk_book.snapshot(
            self.client, self.agents, in_cooldown=in_cooldown,
            **self._daily_pnl_kwargs())

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
            # Objetivo de beneficio de la mesa: recorta/amplía el TP del especialista
            # ANTES de imprimir las métricas (para que reflejen el TP real ejecutado).
            tp_rr = decision.get("tp_rr") or 0.0
            if tp_rr > 0:
                old_tp = signal.get("take_profit")
                if self._apply_tp_rr(agent, signal, tp_rr):
                    print(console.dim(f"     TP mesa: {old_tp} → {signal['take_profit']} "
                                      f"(R:R objetivo 1:{tp_rr:.2f})"))
            self._print_signal_details(agent, signal)
            # Escala del lote: la asignación (presupuesto) modula a la baja vía
            # _alloc_to_scale, y el size_mult de la mesa la modula EXPLÍCITAMENTE
            # (puede subirla por encima de 1 para entradas de convicción / piramidar).
            # 0/ausente = lote base ×1. Los topes de margen/exposición recortan después.
            base_scale = self._alloc_to_scale(alloc)
            size_mult = decision.get("size_mult") or 0.0
            scale = base_scale * size_mult if size_mult > 0 else base_scale
            if size_mult > 0 and abs(size_mult - 1.0) > 1e-9:
                verbo = "agranda" if size_mult > 1 else "reduce"
                print(console.dim(f"     Lote mesa: size_mult {size_mult:.2f}x {verbo} "
                                  f"el lote base (escala total {scale:.2f}x)"))
            # La mesa ya aprobó: el límite global de número de posiciones lo decide
            # ella (gobierna la exposición real vía RiskBook), no el filtro per-agente.
            # Si la mesa fijó un tp_rr, la entrada se valida contra ese R:R (el TP
            # más corto es decisión deliberada de la mesa, no del especialista).
            self._open_from_signal(agent, signal, scale=scale,
                                   enforce_max_positions=False,
                                   min_rr_override=(tp_rr if tp_rr > 0 else None))
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
        # Deja constancia del motivo para que _detect_closed_trades lo registre en
        # el historial (el cierre real lo confirma el siguiente ciclo).
        self._pending_close_reasons[symbol] = "Cierre mesa" if action == "close" else "Reducción mesa"
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
            print("    " + console.err(f"✗ No se pudo cubrir: {describe_mt_error(err)}"))

    # ----- Gestión dinámica de posición (trailing stop / cierre parcial) -----

    def _lifecycle_agents(self) -> list:
        """Agentes con trailing stop o cierre parcial activado."""
        return [a for a in self.agents
                if a.params.use_trailing_stop or a.params.partial_profit_trigger_pct > 0]

    @staticmethod
    def _improves_sl(direction: str, new_sl: float, cur_sl: float) -> bool:
        """True si `new_sl` protege más que el SL actual (más alto en BUY, más bajo
        en SELL). Sin SL actual (0) cualquier nivel válido mejora. Nunca afloja."""
        if new_sl <= 0:
            return False
        if not cur_sl or cur_sl <= 0:
            return True
        return new_sl > cur_sl if direction == "BUY" else new_sl < cur_sl

    def _manage_position_lifecycle(self):
        """Pasa determinista por las posiciones abiertas de los agentes con gestión
        dinámica: asegura beneficio a breakeven y sigue el precio (trailing stop) y
        toma beneficio parcial al alcanzar la fracción objetivo del camino al TP.

        No usa el LLM. Toda la I/O al EA pasa por `_send` (serializado). Limitación
        conocida: un cierre parcial en MT4 reasigna el ticket del remanente; se
        sigue por clave symbol|dirección|entry (estable) para no re-parcializar."""
        agents = self._lifecycle_agents()
        if not agents:
            return
        current_keys: set = set()
        header_printed = False
        for agent in agents:
            symbol = agent.symbol
            p = agent.params
            positions = self.client.get_positions(symbol) or []
            if not positions:
                continue
            tick = self.client.get_tick(symbol)
            if not tick:
                continue
            atr = self.client.get_atr(symbol)
            sym_info = self.client.get_symbol_info(symbol)
            digits = getattr(sym_info, "digits", 5) if sym_info else 5
            vmin = getattr(sym_info, "volume_min", 0.01) or 0.01
            vstep = getattr(sym_info, "volume_step", 0.01) or 0.01

            for pos in positions:
                ticket = _pos_get(pos, "ticket")
                direction = _pos_direction(pos)
                if ticket is None or direction not in ("BUY", "SELL"):
                    continue
                entry = _pos_to_float(_pos_get(pos, "open_price", "price_open"))
                cur_sl = _pos_to_float(_pos_get(pos, "sl", "stop_loss"))
                cur_tp = _pos_to_float(_pos_get(pos, "tp", "take_profit"))
                volume = _pos_to_float(_pos_get(pos, "volume"))
                if entry <= 0 or volume <= 0:
                    continue
                sign = 1 if direction == "BUY" else -1
                # Precio al que se cerraría la posición (BUY→bid, SELL→ask).
                price = tick.bid if direction == "BUY" else tick.ask
                favor = sign * (price - entry)  # beneficio flotante en precio
                lineage = f"{symbol}|{direction}|{round(entry, digits)}"
                current_keys.add(lineage)

                def _ensure_header():
                    nonlocal header_printed
                    if not header_printed:
                        print("\n" + console.dim("  [Gestión] Trailing / parcial sobre posiciones abiertas"))
                        header_printed = True

                # ----- Cierre parcial (una vez por linaje) -----
                if (p.partial_profit_trigger_pct > 0 and cur_tp and volume > vmin
                        and lineage not in self._partial_taken):
                    tp_dist = abs(cur_tp - entry)
                    if tp_dist > 0 and (favor / tp_dist) >= p.partial_profit_trigger_pct:
                        steps = math.floor((volume * p.partial_profit_pct) / vstep + 1e-9)
                        part_vol = round(steps * vstep, 10)
                        if vmin <= part_vol < volume:
                            _ensure_header()
                            progreso = favor / tp_dist
                            print(f"  {console.accent('⊟ [Parcial]')} {symbol} #{ticket}: "
                                  f"cierra {part_vol} de {volume} lotes "
                                  f"({progreso:.0%} del camino al TP)")
                            res = self.client.close_position(symbol, volume=part_vol, ticket=int(ticket))
                            if res and res.get("success"):
                                self._partial_taken.add(lineage)
                                # El remanente queda en un ticket nuevo; su SL/TP se
                                # gestiona por trailing en los ciclos siguientes.
                                continue
                            else:
                                err = (res or {}).get("error") or "sin respuesta"
                                print("    " + console.warn(f"no se pudo parcializar: {describe_mt_error(err)}"))

                # ----- Trailing stop -----
                if p.use_trailing_stop and atr > 0:
                    if favor >= p.trailing_breakeven_atr_mult * atr:
                        trail_sl = price - sign * p.trailing_step_atr_mult * atr
                        # Al menos breakeven (entry); por encima, sigue al precio.
                        new_sl = max(entry, trail_sl) if direction == "BUY" else min(entry, trail_sl)
                        new_sl = round(new_sl, digits)
                        if self._improves_sl(direction, new_sl, cur_sl):
                            _ensure_header()
                            res = self.client.modify_position(
                                symbol, int(ticket), stop_loss=new_sl, take_profit=cur_tp or None)
                            if res and res.get("success"):
                                print(f"  {console.ok('⤢ [Trailing]')} {symbol} #{ticket}: "
                                      f"SL {cur_sl or '—'} → {new_sl}")
                            else:
                                err = (res or {}).get("error") or "sin respuesta"
                                print("    " + console.warn(f"no se pudo mover el SL: {describe_mt_error(err)}"))

        # Poda: olvida los linajes que ya no tienen posición abierta.
        self._partial_taken &= current_keys

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
        headers = ["Símbolo", "Señal", "Neto", "Veredicto", "Prio", "Asig", "Lote×", "TP obj.", "Gestión", "Motivo/clamp"]
        aligns = ["<", "<", "<", "<", ">", ">", ">", ">", "<", "<"]
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
            tp_rr = d.get("tp_rr") or 0.0
            tp_cell = f"1:{tp_rr:.2f}" if tp_rr > 0 else console.dim("—")
            size_mult = d.get("size_mult") or 0.0
            mult_cell = f"{size_mult:.2f}x" if size_mult > 0 else console.dim("—")
            rows.append([
                sym,
                (sig_action, console.side),
                (nd, console.dim),
                verdict,
                str(d.get("priority", "")),
                f"{d.get('allocation_pct', 0):.0%}",
                mult_cell,
                tp_cell,
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
        state = bot_state.get_state()
        signals = dict(state.get("signals", {}) or {})
        in_cooldown = self._risk_cooldown_active()
        snapshot = self.risk_book.snapshot(
            self.client, self.agents, in_cooldown=in_cooldown,
            **self._daily_pnl_kwargs())
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
        in_cooldown = self._risk_cooldown_active()
        snapshot = self.risk_book.snapshot(
            self.client, self.agents, in_cooldown=in_cooldown,
            **self._daily_pnl_kwargs())

        print("\n" + console.header("JUNTA DE LA MESA · revisión global del libro", char="#"))
        self.last_junta_at = time.strftime("%Y-%m-%d %H:%M:%S")

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
        try:
            snapshot = self.risk_book.snapshot(
                self.client, self.agents,
                in_cooldown=self._risk_cooldown_active(),
                **self._daily_pnl_kwargs())
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

    def set_coordinator_model(self, provider: str, model: str) -> dict:
        """Cambia el provider/modelo LLM del director (mesa de dirección) en
        caliente. Reconstruye el motor del coordinador (ver
        `CoordinatorAgent.set_model`); surte efecto en la próxima coordinación.
        Lanza ValueError si faltan datos."""
        result = self.coordinator.set_model(provider, model)
        print(f"  [mesa] director LLM cambiado a "
              f"{result['provider'].upper()}/{result['model']}")
        return result

    def set_director_note(self, note: str) -> dict:
        """Fija (o retira, si ``note`` queda vacío) la NOTA DE DIRECCIÓN que la mesa
        pondera en sus decisiones de las siguientes rotaciones. La emite el asistente
        desde el chat cuando el usuario le pide instruir a la mesa. Se aplica en
        caliente (el director la inyecta en su prompt en la próxima coordinación) y se
        persiste en .env (DIRECTOR_NOTE) para sobrevivir a reinicios. Colapsa los
        saltos de línea (el .env es de una sola línea)."""
        clean = " ".join((note or "").split())
        self.coordinator.director_note = clean
        try:
            from core.settings_schema import write_env
            write_env({"DIRECTOR_NOTE": clean})
        except Exception as e:  # noqa: BLE001 — nunca tumbar el API por la persistencia
            print(f"  [mesa] no se pudo persistir DIRECTOR_NOTE: {e}")
        print(f"  [mesa] nota de dirección {'fijada: ' + clean if clean else 'retirada'}")
        return {"director_note": clean}

    # Parámetros editables a mano desde el dashboard (umbrales de señal). Se
    # acotan con PARAM_BOUNDS para no dejar al agente en una config absurda.
    EDITABLE_PARAMS = ("min_confidence", "min_rr", "atr_sl_mult", "atr_tp_mult")
    # Parámetros de elección (string) editables: modo pensamiento de DeepSeek, con
    # sus valores válidos. Solo afectan a los modelos híbridos deepseek-v4-*.
    EDITABLE_CHOICE_PARAMS = {
        "thinking": ("auto", "enabled", "disabled"),
        "reasoning_effort": ("", "high", "max"),
    }

    def set_agent_params(self, name: str, updates: dict) -> dict:
        """Ajusta a mano los parámetros de un agente en caliente (desde el
        dashboard). Acepta los umbrales numéricos de `EDITABLE_PARAMS` (validados
        y recortados a su rango en `PARAM_BOUNDS`) y los de elección de
        `EDITABLE_CHOICE_PARAMS` (modo pensamiento DeepSeek, validados contra su
        lista de valores). El cambio surte efecto en el siguiente análisis del
        agente (no reconstruye la estrategia: provider/modelo no cambian aquí).

        Lanza KeyError si el agente no existe y ValueError si no llega ninguna
        clave válida, algún número no es válido o una elección está fuera de su
        lista."""
        agent = next((a for a in self.agents if a.name == name), None)
        if agent is None:
            raise KeyError(name)
        clean = {}
        for key, value in (updates or {}).items():
            if key in self.EDITABLE_PARAMS:
                try:
                    num = float(value)
                except (TypeError, ValueError):
                    raise ValueError(f"valor no numérico para {key}: {value!r}")
                lo, hi = PARAM_BOUNDS[key]
                clean[key] = _clamp(num, key)
                if num < lo or num > hi:
                    print(f"  [{name}] {key}={num} recortado a {clean[key]} (rango {lo}–{hi})")
            elif key in self.EDITABLE_CHOICE_PARAMS:
                val = ("" if value is None else str(value)).strip().lower()
                allowed = self.EDITABLE_CHOICE_PARAMS[key]
                if val not in allowed:
                    raise ValueError(f"valor inválido para {key}: {value!r} (válidos: {allowed})")
                clean[key] = val
        if not clean:
            raise ValueError("no se recibió ningún parámetro editable válido")
        new_params = agent.params.model_copy(update=clean)
        agent.apply_params(new_params)
        cambios = ", ".join(f"{k}={v}" for k, v in clean.items())
        print(f"  [{name}] parámetros ajustados a mano: {cambios}")
        return {"name": name, "params": clean}

    def set_agent_enabled(self, name: str, enabled: bool) -> dict:
        """Activa/desactiva un agente en caliente. Desactivado, el orquestador lo
        omite en la recolección de las siguientes rotaciones (deja de analizar y
        de proponer entradas). Lanza KeyError si el agente no existe."""
        agent = next((a for a in self.agents if a.name == name), None)
        if agent is None:
            raise KeyError(name)
        agent.enabled = bool(enabled)
        if agent.enabled:
            # Reactivar limpia el back-off por 133: el usuario pide reintentar ya
            # (p. ej. tras reabrir la sesión del símbolo o habilitarlo en el broker).
            self._trade_disabled_until.pop(agent.symbol, None)
            # Y se fuerza el análisis del símbolo en la próxima rotación (sin
            # esperar a que expire un throttle de máximo de posiciones/cooldown).
            with self._pending_force_lock:
                self._pending_force.add(agent.symbol)
        estado = "activado" if agent.enabled else "desactivado"
        print(f"  [{name}] {estado} para las siguientes rotaciones.")
        return {"name": name, "symbol": agent.symbol, "enabled": agent.enabled}

    def add_agent(self, name: str, provider: str | None = None,
                  model: str | None = None) -> dict:
        """Carga en caliente un agente del catálogo que NO se seleccionó al
        arrancar, para que participe desde la siguiente rotación. Lo instancia
        con su blueprint (provider/modelo por defecto, o los indicados), lo añade
        a la lista de agentes, inicializa sus contadores y lo deja activado.

        Lanza KeyError si el nombre no existe en el catálogo y ValueError si el
        agente ya está cargado."""
        from agents.registry import AGENT_BLUEPRINTS, build_agent

        if name not in AGENT_BLUEPRINTS:
            raise KeyError(name)
        if any(a.name == name for a in self.agents):
            raise ValueError(f"el agente '{name}' ya está cargado")
        # Replica el modo debug de los agentes existentes (todos se construyen
        # igual al arrancar); por defecto True como en build_agent.
        debug_mode = getattr(self.agents[0], "debug_mode", True) if self.agents else True
        agent = build_agent(name, debug_mode=debug_mode, provider=provider, model=model)
        agent.enabled = True
        self.agents.append(agent)
        self.stats[agent.name] = {"signals": 0, "trades": 0, "holds": 0}
        # Forzar su análisis en la próxima rotación (sin throttle inicial).
        with self._pending_force_lock:
            self._pending_force.add(agent.symbol)
        print(f"  [{name}] añadido en caliente [{agent.symbol}] "
              f"{agent.params.provider.upper()}/{agent.params.model}; "
              f"analizará desde la siguiente rotación.")
        return {"name": agent.name, "symbol": agent.symbol,
                "provider": agent.params.provider, "model": agent.params.model,
                "enabled": agent.enabled}

    def _refresh_trading_directives(self) -> None:
        """Inyecta la directiva del perfil activo (riesgo + horizonte) en los prompts
        del especialista (`agent.strategy.trading_directive`) y de la mesa
        (`coordinator.risk_directive`). Es lo que hace que el perfil cambie de
        verdad el comportamiento del LLM (los topes/umbrales solo filtran).
        Fail-safe: no propaga errores."""
        try:
            from core.profiles import (get_active_risk, get_active_horizon,
                                       build_agent_directive, build_coordinator_directive,
                                       allows_high_risk_with_positions)
            risk, horizon = get_active_risk(), get_active_horizon()
            agent_directive = build_agent_directive(risk, horizon)
            coord_directive = build_coordinator_directive(risk, horizon)
            # Apetito alto (aggressive/extreme): levanta el veto de validate_trade a
            # las señales risk_level="high" con posiciones abiertas.
            allow_high_risk = allows_high_risk_with_positions(risk)
            for agent in self.agents:
                strat = getattr(agent, "strategy", None)
                if strat is not None:
                    strat.trading_directive = agent_directive
                    strat.allow_high_risk_with_positions = allow_high_risk
            if self.coordinator is not None:
                self.coordinator.risk_directive = coord_directive
        except Exception as e:
            print(f"  refresh de directivas de perfil falló: {e}")

    def _persist_stats(self) -> None:
        """Vuelca los contadores por agente a la DB (upsert). Fail-safe: nunca
        tumba el bucle por un fallo de persistencia."""
        try:
            from core.db import save_agent_stats
            save_agent_stats(self.stats)
        except Exception:
            pass

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
                # False = desactivado desde el dashboard: el orquestador lo omite
                # en la recolección (no analiza ni propone entradas).
                "enabled": getattr(agent, "enabled", True),
                # False = mercado cerrado (fin de semana / fuera de sesión): el
                # agente figura como no disponible y no analiza hasta que reabra.
                "market_open": self.client.is_market_open(agent.symbol),
                "params": {
                    "min_confidence": p.min_confidence,
                    "min_rr": p.min_rr,
                    "atr_sl_mult": p.atr_sl_mult,
                    "atr_tp_mult": p.atr_tp_mult,
                    "lot_size": p.lot_size,
                    "thinking": p.thinking,
                    "reasoning_effort": p.reasoning_effort,
                },
                "stats": self.stats[agent.name],
                "performance": agent.memory.get_performance(agent.symbol),
                # P/L medio real (pnl) de los trades cerrados del símbolo +
                # cuántos lo promedian. Lo muestra la pestaña Agentes.
                "closes": read_close_stats(agent.symbol, platform=self.platform),
            })
        # Agentes del catálogo que NO están cargados: se ofrecen para activarlos
        # en caliente desde el dashboard (participarán en la siguiente rotación).
        from agents.registry import list_agents
        loaded = {a.name for a in self.agents}
        available = []
        for bp in list_agents():
            if bp.name in loaded:
                continue
            available.append({
                "name": bp.name,
                "symbol": bp.symbol,
                "description": bp.description,
                "provider": bp.params.provider,
                "model": bp.params.model,
                "market_open": self.client.is_market_open(bp.symbol),
            })
        return {
            "agents": agents,
            "available": available,
            "optimize_every_cycles": self.optimize_every_cycles,
            "last_optimization": self.last_optimization,
            "last_optimization_at": self.last_optimization_at,
        }

    def reload_runtime_config(self) -> None:
        """Re-lee la config del .env (ya volcada en os.environ por el editor de
        ajustes) y la aplica EN CALIENTE a los objetos vivos: RiskBook, director
        LLM, cadencias del planificador y guardias de riesgo. Las claves que solo
        se leen al arrancar (credenciales, modelo, NEWS_ENABLED…) no se tocan aquí
        —el editor las marca como 'requiere reinicio'."""
        from core.config import (get_coordinator_config, get_schedule_config,
                                  get_agent_param_overrides)

        # --- Cadencias del planificador ---
        sched = get_schedule_config()
        self.schedule_cfg = sched  # el reporte (SMTP) lee de aquí
        self.rotation_seconds = sched.get("rotation_seconds", self.rotation_seconds)
        self.news_poll_seconds = sched.get("news_poll_seconds", self.news_poll_seconds)
        self.junta_interval_seconds = sched.get("junta_interval_seconds", self.junta_interval_seconds)
        self.report_interval_seconds = sched.get("report_interval_seconds", self.report_interval_seconds)

        # --- Guardias de riesgo / análisis (atributos del orquestador) ---
        self.max_daily_loss_pct = float(os.getenv("MAX_DAILY_LOSS_PCT", "0") or 0)
        self.risk_loss_window_seconds = float(
            os.getenv("RISK_LOSS_WINDOW_SECONDS", str(6 * 3600)) or (6 * 3600))
        self.parallel_analysis = (
            os.getenv("PARALLEL_ANALYSIS", "true").lower() in ("1", "true", "yes", "on"))

        # --- RiskBook (topes duros del coordinador) ---
        cfg = get_coordinator_config()
        self.risk_book.max_total_exposure_pct = float(cfg["max_total_exposure_pct"])
        self.risk_book.max_symbol_allocation_pct = float(cfg["max_symbol_allocation_pct"])
        self.risk_book.can_close = bool(cfg["can_close"])
        self.risk_book.llm_can_close = bool(cfg["llm_can_close"])
        self.risk_book.max_net_direction_pct = float(cfg["max_net_direction_pct"])
        self.risk_book.max_pyramid_direction_pct = float(cfg["max_pyramid_direction_pct"])
        self.risk_book.reversal_drawdown_pct = float(cfg["reversal_drawdown_pct"])
        self.risk_book.max_symbol_loss_pct = float(cfg["max_symbol_loss_pct"])
        self.risk_book.min_hold_seconds = float(cfg["min_hold_seconds"])
        # Nº máximo de posiciones por símbolo: lo mueven el perfil de riesgo (base)
        # y el horizonte (multiplicador), ambos del front. Recalculado en
        # get_coordinator_config a partir de ambos ejes.
        self.risk_book.max_open_positions = int(cfg["max_open_positions"])
        # Rango del R:R objetivo (lo mueve el selector de horizonte).
        self.risk_book.tp_rr_min = float(cfg["tp_rr_min"])
        self.risk_book.tp_rr_max = float(cfg["tp_rr_max"])
        # Temperatura del director (best-effort: el motor puede cachearla).
        engine = getattr(self.coordinator, "engine", None)
        if engine is not None and hasattr(engine, "temperature"):
            try:
                engine.temperature = float(cfg["temperature"])
            except (ValueError, TypeError):
                pass
        # Re-inyecta la directiva del perfil (riesgo + horizonte) en los prompts: al
        # cambiar de perfil en vivo, el LLM debe cambiar de disposición YA.
        self._refresh_trading_directives()

        # --- Parámetros de los agentes (umbrales de señal) ---
        # Re-aplica en caliente los overrides de .env (incluidas las claves
        # <PARAM>_DEFAULT que mueven a TODOS los agentes a la vez, p. ej. al
        # cambiar de perfil de riesgo): min_confidence, min_rr, max_open_positions,
        # atr_*, etc. Mismo mecanismo que set_agent_params (apply_params).
        for agent in self.agents:
            try:
                overrides = get_agent_param_overrides(agent.symbol, agent.params.model)
                if overrides:
                    agent.apply_params(agent.params.model_copy(update=overrides))
            except Exception as e:
                print(f"  [{agent.name}] reload de params falló: {e}")

    def coordinator_overview(self) -> dict:
        """Estado del coordinador para el dashboard (/api/coordinator). La mesa
        está siempre activa (`enabled` se mantiene por compatibilidad del API)."""
        return {
            "enabled": True,
            "provider": self.coordinator.provider,
            "model": self.coordinator.model,
            # Nota de dirección activa (instrucción del responsable que la mesa
            # pondera). La fija el asistente desde el chat; "" = sin nota.
            "director_note": self.coordinator.director_note,
            "can_close": self.risk_book.can_close,
            "llm_can_close": self.risk_book.llm_can_close,
            "max_total_exposure_pct": self.risk_book.max_total_exposure_pct,
            "max_symbol_allocation_pct": self.risk_book.max_symbol_allocation_pct,
            "max_net_direction_pct": self.risk_book.max_net_direction_pct,
            "max_pyramid_direction_pct": self.risk_book.max_pyramid_direction_pct,
            "reversal_drawdown_pct": self.risk_book.reversal_drawdown_pct,
            "max_symbol_loss_pct": self.risk_book.max_symbol_loss_pct,
            "min_hold_seconds": self.risk_book.min_hold_seconds,
            # Nº máximo de posiciones por símbolo que gobierna la mesa (riesgo×horizonte).
            "max_open_positions": self.risk_book.max_open_positions,
            # Perfil de riesgo y horizonte activos (selectores del dashboard).
            "risk_profile": os.getenv("RISK_PROFILE", "moderate").strip() or "moderate",
            "horizon": os.getenv("HORIZON", "medio").strip() or "medio",
            # Params de duración (los mueve el horizonte) para mostrarlos en el front.
            "tp_rr_min": self.risk_book.tp_rr_min,
            "tp_rr_max": self.risk_book.tp_rr_max,
            "last_coordination": self.last_coordination,
            "last_coordination_at": self.last_coordination_at,
            "last_junta_at": self.last_junta_at,
            "last_report_at": self.last_report_at,
            "rotation_seconds": self.rotation_seconds,
            "news_poll_seconds": self.news_poll_seconds,
            "junta_interval_seconds": self.junta_interval_seconds,
            "report_interval_seconds": self.report_interval_seconds,
        }
