from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import functools
import logging
import os
from datetime import datetime
from core.state import bot_state
from core.clock import broker_now
from core.llm_config import available_providers

app = Flask(__name__)
CORS(app)
# async_mode="threading": el bot corre en el hilo principal y la API en un hilo
# de fondo; eventlet (auto-detectado si está instalado) rompe los ping/pong en
# esta arquitectura y causa desconexiones. ping_timeout alto porque la
# inferencia local de Ollama satura la CPU y retrasa las respuestas.
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="threading",
    ping_interval=25,
    ping_timeout=60,
)

_mt_client = None
_orchestrator = None
_assistant = None
connected_clients = set()

logging.basicConfig(level=logging.INFO)
# El log de acceso de werkzeug imprime una línea INFO por cada petición HTTP
# (el dashboard hace polling constante -> "INFO:werkzeug:127.0.0.1 ... GET /api/..."
# que saturan la consola). Lo subimos a WARNING: desaparece el ruido de los 200
# y el banner de "development server", pero se siguen viendo errores reales (4xx/5xx).
logging.getLogger("werkzeug").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def set_mt_client(client):
    global _mt_client
    _mt_client = client


def set_orchestrator(orchestrator):
    """Registra el orquestador de agentes para exponer su estado en /api/agents."""
    global _orchestrator
    _orchestrator = orchestrator


def _tag_manual_close(symbol: str):
    """Marca el motivo de un cierre manual (desde el dashboard) para que el
    orquestador lo registre en el historial (_detect_closed_trades lo consume)."""
    orch = _orchestrator
    if orch is not None and hasattr(orch, "_pending_close_reasons"):
        try:
            orch._pending_close_reasons[symbol] = "Cierre manual"
        except Exception:
            pass


def _api_token() -> str:
    """Token compartido leído en tiempo de request (el .env se carga después de
    importar este módulo, así que no se puede capturar en import)."""
    return os.getenv("API_TOKEN", "").strip()


def require_token(f):
    """Protege rutas que mutan estado (operar, cerrar, cambiar modelo...).

    Si API_TOKEN está vacío (uso puramente local) no exige nada; si está
    definido, requiere la cabecera X-API-Token correcta. Así el dashboard en
    127.0.0.1 funciona sin fricción, pero exponer el API a la red sin token
    deja de dar control remoto anónimo del bot."""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        token = _api_token()
        if token and request.headers.get("X-API-Token", "") != token:
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


@app.route("/api/signals", methods=["GET"])
def get_signals():
    return jsonify(bot_state.get_state()["signals"]), 200


@app.route("/api/positions", methods=["GET"])
def get_positions():
    return jsonify(bot_state.get_state()["positions"]), 200


@app.route("/api/account", methods=["GET"])
def get_account():
    return jsonify(bot_state.get_state()["account_info"]), 200


@app.route("/api/history", methods=["GET"])
def get_history():
    return jsonify(bot_state.get_state()["closed_trades"]), 200


@app.route("/api/state", methods=["GET"])
def get_full_state():
    return jsonify(bot_state.get_state()), 200


@app.route("/api/bot/start", methods=["POST"])
@require_token
def start_bot():
    bot_state.set_bot_running(True)
    socketio.emit("bot_status", {"running": True})
    return jsonify({"status": "started"}), 200


@app.route("/api/bot/stop", methods=["POST"])
@require_token
def stop_bot():
    bot_state.set_bot_running(False)
    socketio.emit("bot_status", {"running": False})
    return jsonify({"status": "stopped"}), 200


@app.route("/api/notify-duplicate", methods=["POST"])
def notify_duplicate():
    socketio.emit("duplicate_instance", {})
    return jsonify({"status": "notified"}), 200


def _fmt_ts(dt) -> str:
    """Formatea el timestamp igual que el CSV anterior, para no romper al front."""
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""


@app.route("/api/db/signals", methods=["GET"])
@app.route("/api/csv/signals", methods=["GET"])  # alias antiguo (compatibilidad)
def get_csv_signals():
    """Histórico de señales persistidas desde la DB (SQLite). La ruta
    /api/csv/signals se conserva como alias; ya no se lee ningún signals.csv.
    (No confundir con /api/signals, que devuelve la última señal viva por símbolo
    desde el estado en memoria.)"""
    from core.db import Signal, get_session
    limit = int(request.args.get("limit", 15))
    platform = request.args.get("platform", "mt4").upper()
    session = get_session()
    try:
        rows = (session.query(Signal)
                .filter(Signal.platform == platform)
                .order_by(Signal.timestamp.desc(), Signal.id.desc())
                .limit(limit).all())
    finally:
        session.close()
    rows.reverse()  # cronológico ascendente, como el CSV
    out = [{
        "timestamp": _fmt_ts(r.timestamp), "platform": r.platform, "agent": r.agent,
        "symbol": r.symbol, "action": r.action,
        "confidence": f"{r.confidence:.2f}" if r.confidence is not None else "",
        "trend": r.trend, "risk_level": r.risk_level,
        "entry": r.entry if r.entry is not None else "",
        "stop_loss": r.stop_loss if r.stop_loss is not None else "",
        "take_profit": r.take_profit if r.take_profit is not None else "",
        "reason": r.reason, "trade_id": r.trade_id,
        "executed": "true" if r.executed else "false",
    } for r in rows]
    return jsonify(out), 200


@app.route("/api/db/trades", methods=["GET"])
@app.route("/api/csv/trades", methods=["GET"])  # alias antiguo (compatibilidad)
def get_csv_trades():
    """Histórico de órdenes persistidas desde la DB (SQLite). La ruta
    /api/csv/trades se conserva como alias; ya no se lee ningún trades.csv."""
    from core.db import Trade, get_session
    limit = int(request.args.get("limit", 50))
    platform = request.args.get("platform", "mt4").upper()
    session = get_session()
    try:
        rows = (session.query(Trade)
                .filter(Trade.platform == platform)
                .order_by(Trade.timestamp.desc(), Trade.id.desc())
                .limit(limit).all())
    finally:
        session.close()
    rows.reverse()
    out = [{
        "timestamp": _fmt_ts(r.timestamp), "platform": r.platform, "symbol": r.symbol,
        "action": r.action,
        "volume": r.volume if r.volume is not None else "",
        "price": r.price if r.price is not None else "",
        "stop_loss": r.stop_loss if r.stop_loss is not None else "",
        "take_profit": r.take_profit if r.take_profit is not None else "",
        "retcode": r.retcode, "order_id": r.order_id, "comment": r.comment,
    } for r in rows]
    return jsonify(out), 200


@app.route("/api/db/closed-trades", methods=["GET"])
def get_closed_trades():
    """Histórico de trades CERRADOS persistidos (tabla ``closed_trades``), con
    filtros. A diferencia de ``state.closed_trades`` (solo la sesión en memoria),
    esto sobrevive a los reinicios.

    Parámetros (query string, todos opcionales):
      - ``platform`` (default mt4)
      - ``symbol``   filtra por símbolo exacto
      - ``action``   BUY / SELL
      - ``result``   ``win`` (pnl > 0) o ``loss`` (pnl <= 0)
      - ``from``/``to``  rango de fechas ``YYYY-MM-DD`` sobre el cierre (``to`` incl.)
      - ``limit``    máximo de filas devueltas (default 200, 0 = sin tope)

    Devuelve ``{trades, summary, symbols}``: ``summary`` (total/winning/pnl) se
    calcula sobre TODO el set filtrado, no solo sobre las filas devueltas;
    ``symbols`` es la lista de símbolos distintos (para el desplegable)."""
    from datetime import timedelta
    from sqlalchemy import case, func
    from core.db import ClosedTrade, get_session

    platform = request.args.get("platform", "mt4").upper()
    symbol = (request.args.get("symbol") or "").strip()
    action = (request.args.get("action") or "").strip().upper()
    result = (request.args.get("result") or "").strip().lower()
    limit = int(request.args.get("limit", 200) or 0)

    def _parse_day(name):
        raw = (request.args.get(name) or "").strip()
        if not raw:
            return None
        try:
            return datetime.strptime(raw, "%Y-%m-%d")
        except ValueError:
            return None

    date_from = _parse_day("from")
    date_to = _parse_day("to")

    session = get_session()
    try:
        def _apply_filters(q):
            q = q.filter(ClosedTrade.platform == platform)
            if symbol:
                q = q.filter(ClosedTrade.symbol == symbol)
            if action in ("BUY", "SELL"):
                q = q.filter(ClosedTrade.action == action)
            if result == "win":
                q = q.filter(ClosedTrade.pnl > 0)
            elif result == "loss":
                q = q.filter(ClosedTrade.pnl <= 0)
            if date_from is not None:
                q = q.filter(ClosedTrade.timestamp >= date_from)
            if date_to is not None:
                q = q.filter(ClosedTrade.timestamp < date_to + timedelta(days=1))
            return q

        rows_q = _apply_filters(session.query(ClosedTrade)).order_by(
            ClosedTrade.timestamp.desc(), ClosedTrade.id.desc())
        if limit and limit > 0:
            rows_q = rows_q.limit(limit)
        rows = rows_q.all()

        # Resumen sobre el set filtrado completo (independiente del límite).
        agg = _apply_filters(session.query(
            func.count(ClosedTrade.id),
            func.coalesce(func.sum(ClosedTrade.pnl), 0.0),
            func.coalesce(func.sum(
                case((ClosedTrade.pnl > 0, 1), else_=0)), 0),
        )).one()
        total, total_pnl, winning = int(agg[0]), float(agg[1]), int(agg[2])

        symbols = [s[0] for s in (session.query(ClosedTrade.symbol)
                   .filter(ClosedTrade.platform == platform)
                   .distinct().order_by(ClosedTrade.symbol.asc()).all()) if s[0]]
    finally:
        session.close()

    trades = []
    for r in rows:
        close_dt = r.timestamp
        open_dt = None
        if close_dt and r.duration_seconds:
            open_dt = close_dt - timedelta(seconds=int(r.duration_seconds))
        trades.append({
            "symbol": r.symbol, "action": r.action,
            "entry_price": r.entry_price, "exit_price": r.exit_price,
            "volume": r.volume, "pnl": r.pnl, "commission": r.commission,
            "open_time": _fmt_ts(open_dt), "close_time": _fmt_ts(close_dt),
            "duration_seconds": r.duration_seconds,
            "close_reason": r.close_reason, "trade_id": r.trade_id,
        })

    return jsonify({
        "trades": trades,
        "summary": {"total": total, "winning": winning, "pnl": round(total_pnl, 2)},
        "symbols": symbols,
    }), 200


@app.route("/api/equity", methods=["GET"])
def get_equity():
    """Serie temporal de la cartera (balance/equity) para el gráfico de evolución
    del dashboard. Submuestreada a `limit` puntos."""
    from core.logger import read_equity_series
    limit = int(request.args.get("limit", 500))
    platform = request.args.get("platform", "mt4").lower()
    since_seconds = int(request.args.get("since", 0) or 0)
    return jsonify(read_equity_series(platform, limit, since_seconds)), 200


@app.route("/api/news", methods=["GET"])
def get_news():
    """Titulares recientes (Yahoo Finance RSS) de los símbolos que opera el bot,
    para el slider de noticias del dashboard. Caché de 15 min en NewsProvider;
    fail-safe (lista vacía ante errores de red o con NEWS_ENABLED=false). Los
    símbolos salen de los agentes cargados; si el orquestador aún no está listo,
    caen a la lista SYMBOLS del entorno."""
    from core.news import news_provider
    symbols = []
    if _orchestrator is not None:
        symbols = [a.symbol for a in getattr(_orchestrator, "agents", [])]
    if not symbols:
        symbols = [s.strip().upper() for s in os.getenv("SYMBOLS", "").split(",") if s.strip()]
    # Dedup conservando el orden de aparición.
    seen = set()
    ordered = [s for s in symbols if not (s in seen or seen.add(s))]

    per_symbol = [(sym, items) for sym in ordered
                  if (items := news_provider.get_headlines(sym))]

    # Intercalado round-robin: que los slides consecutivos sean de símbolos
    # distintos en vez de agotar uno antes de pasar al siguiente.
    flat = []
    depth = max((len(items) for _, items in per_symbol), default=0)
    for i in range(depth):
        for sym, items in per_symbol:
            if i < len(items):
                flat.append({"symbol": sym, **items[i]})

    return jsonify({"enabled": news_provider.enabled, "headlines": flat}), 200


@app.route("/api/stats", methods=["GET"])
def get_stats():
    """Estadísticas agregadas de señales y memoria de resultados (consultas SQL)."""
    from sqlalchemy import func
    from core.db import Signal, SignalMemoryRecord, Trade, get_session
    from core.memory import WIN_OUTCOMES

    platform = request.args.get("platform", "mt4").upper()
    # "Hoy" en el día del BRÓKER, coherente con las marcas almacenadas.
    today_start = datetime.combine(broker_now().date(), datetime.min.time())

    stats = {
        "signals_total": 0,
        "signals_today": 0,
        "by_action": {"BUY": 0, "SELL": 0, "HOLD": 0},
        "avg_confidence": None,
        "trades_total": 0,
        "memory": {"evaluated": 0, "wins": 0, "win_rate": None},
    }

    session = get_session()
    try:
        base = session.query(Signal).filter(Signal.platform == platform)
        stats["signals_total"] = base.count()
        stats["signals_today"] = base.filter(Signal.timestamp >= today_start).count()
        for action, n in (session.query(Signal.action, func.count(Signal.id))
                          .filter(Signal.platform == platform)
                          .group_by(Signal.action).all()):
            if (action or "").upper() in stats["by_action"]:
                stats["by_action"][action.upper()] = n
        avg = (session.query(func.avg(Signal.confidence))
               .filter(Signal.platform == platform).scalar())
        if avg is not None:
            stats["avg_confidence"] = round(float(avg), 3)

        stats["trades_total"] = (session.query(Trade)
                                 .filter(Trade.platform == platform).count())

        # Win-rate agregado sobre toda la memoria de señales (todos los agentes).
        evaluated = (session.query(SignalMemoryRecord)
                     .filter(SignalMemoryRecord.outcome.isnot(None)).count())
        wins = (session.query(SignalMemoryRecord)
                .filter(SignalMemoryRecord.outcome.in_(WIN_OUTCOMES))
                .count())
        stats["memory"]["evaluated"] = evaluated
        stats["memory"]["wins"] = wins
        if evaluated:
            stats["memory"]["win_rate"] = round(wins / evaluated, 3)
    finally:
        session.close()

    return jsonify(stats), 200


@app.route("/api/agents", methods=["GET"])
def get_agents():
    """Resumen de agentes activos: config, stats de sesión, rendimiento y
    última optimización. Vacío si el sistema agéntico no está en marcha."""
    if _orchestrator is None:
        return jsonify({"agents": [], "last_optimization": None,
                        "last_optimization_at": None, "optimize_every_cycles": 0}), 200
    return jsonify(_orchestrator.agents_overview()), 200


@app.route("/api/coordinator", methods=["GET"])
def get_coordinator():
    """Estado del coordinador (mesa de dirección): snapshot de cartera, última
    coordinación, config y modelo LLM. {"enabled": false} si no está activo."""
    if _orchestrator is None or not hasattr(_orchestrator, "coordinator_overview"):
        return jsonify({"enabled": False}), 200
    return jsonify(_orchestrator.coordinator_overview()), 200


@app.route("/api/coordinator/decide", methods=["POST"])
@require_token
def coordinator_decide():
    """Fuerza una decisión de coordinación AHORA en modo dry-run (no ejecuta
    órdenes; usa las últimas señales conocidas). La ejecución real solo ocurre
    en el bucle del orquestador."""
    if _orchestrator is None or getattr(_orchestrator, "coordinator", None) is None:
        return jsonify({"error": "coordinator not running"}), 503
    result = _orchestrator.coordinate_now()
    return jsonify(result), 200


@app.route("/api/coordinator/model", methods=["POST"])
@require_token
def set_coordinator_model():
    """Cambia el provider/modelo LLM del director (mesa de dirección) EN CALIENTE
    y lo persiste en .env (COORDINATOR_PROVIDER/COORDINATOR_MODEL) para reusarlo en
    el próximo arranque. Body: {"provider": "gemini", "model": "gemini-3.5-flash"}."""
    if _orchestrator is None or getattr(_orchestrator, "coordinator", None) is None:
        return jsonify({"error": "coordinator not running"}), 503
    body = request.get_json(silent=True) or {}
    provider, model = body.get("provider"), body.get("model")
    # Validar contra lo realmente disponible (clave configurada + modelo listado).
    providers = available_providers()
    if provider not in providers or model not in providers.get(provider, []):
        return jsonify({"error": f"provider/modelo no disponible: {provider}/{model}"}), 400
    try:
        result = _orchestrator.set_coordinator_model(provider, model)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    # Persistir en .env (y os.environ) para que la elección sobreviva al reinicio.
    from core.settings_schema import write_env
    write_env({"COORDINATOR_PROVIDER": result["provider"],
               "COORDINATOR_MODEL": result["model"]})
    socketio.emit("coordinator_model_changed", result)
    return jsonify({"status": "ok", **result}), 200


def _apply_profile(table: dict, level: str, marker_key: str,
                   marker_value: str, event: str):
    """Lógica común de los dos selectores (riesgo / horizonte): escribe el set de
    claves .env del nivel + la marca del nivel activo, lo aplica en caliente vía
    reload_runtime_config (incluida la re-inyección de la directiva del prompt) y
    emite el evento WS. Devuelve (payload, http_status)."""
    from core.settings_schema import write_env
    if level not in table:
        return {"error": f"nivel desconocido: {level!r}", "valid": list(table.keys())}, 400
    serialized = dict(table[level])
    serialized[marker_key] = marker_value
    changed = write_env(serialized)
    if _orchestrator is not None and hasattr(_orchestrator, "reload_runtime_config"):
        try:
            _orchestrator.reload_runtime_config()
        except Exception as e:  # noqa: BLE001 — nunca tumbar la API por esto
            logger.warning("reload_runtime_config falló tras cambiar perfil: %s", e)
    overview = (_orchestrator.coordinator_overview()
                if _orchestrator is not None and hasattr(_orchestrator, "coordinator_overview")
                else {})
    payload = {"level": marker_value, "changed": changed, "overview": overview}
    socketio.emit(event, payload)
    return payload, 200


@app.route("/api/risk-profile", methods=["POST"])
@require_token
def set_risk_profile():
    """Aplica un perfil de RIESGO (conservative/moderate/aggressive/extreme): apetito,
    exposición y selectividad — y la directiva de apetito en los prompts. Lo persiste
    en .env y lo aplica en caliente. Body: {"profile": ...}. Protegido por API_TOKEN."""
    from core.profiles import RISK_PROFILES
    body = request.get_json(silent=True) or {}
    profile = str(body.get("profile", "")).strip().lower()
    payload, status = _apply_profile(
        RISK_PROFILES, profile, "RISK_PROFILE", profile, "risk_profile_changed")
    if status == 200:
        payload["profile"] = profile
    return jsonify(payload), status


@app.route("/api/horizon", methods=["POST"])
@require_token
def set_horizon():
    """Aplica un HORIZONTE (corto/medio/largo): duración de las operaciones — TP más
    cercano/lejano, periodo de gracia, trailing/parcial, cadencia — y la directiva de
    horizonte en los prompts. Lo persiste en .env y lo aplica en caliente.
    Body: {"horizon": ...}. Protegido por API_TOKEN."""
    from core.profiles import HORIZON_PROFILES
    body = request.get_json(silent=True) or {}
    horizon = str(body.get("horizon", "")).strip().lower()
    payload, status = _apply_profile(
        HORIZON_PROFILES, horizon, "HORIZON", horizon, "horizon_changed")
    if status == 200:
        payload["horizon"] = horizon
    return jsonify(payload), status


@app.route("/api/settings", methods=["GET"])
@require_token
def get_settings():
    """Devuelve el esquema de ajustes editables del .env con su valor actual.
    Los secretos no incluyen valor (solo `is_set`). Protegido por API_TOKEN."""
    from core.settings_schema import read_settings
    return jsonify({"settings": read_settings()}), 200


@app.route("/api/settings", methods=["POST"])
@require_token
def update_settings():
    """Escribe ajustes en el .env y los aplica EN CALIENTE donde se puede.
    Body: {"updates": {KEY: value, ...}}. Los secretos vacíos no se sobrescriben.
    Responde con las claves cambiadas y cuáles requieren reiniciar el bot."""
    from core.settings_schema import validate_and_serialize, write_env, restart_required
    body = request.get_json(silent=True) or {}
    updates = body.get("updates", body)  # tolera el dict plano
    try:
        serialized = validate_and_serialize(updates)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if not serialized:
        return jsonify({"changed": [], "restart_required": [], "applied_hot": []}), 200
    changed = write_env(serialized)
    needs_restart = restart_required(changed)
    # Aplica en caliente lo que se pueda sobre el orquestador vivo.
    if _orchestrator is not None and hasattr(_orchestrator, "reload_runtime_config"):
        try:
            _orchestrator.reload_runtime_config()
        except Exception as e:  # noqa: BLE001 — nunca tumbar la API por esto
            logger.warning("reload_runtime_config falló: %s", e)
    applied_hot = [k for k in changed if k not in needs_restart]
    return jsonify({
        "changed": changed,
        "restart_required": needs_restart,
        "applied_hot": applied_hot,
    }), 200


def _get_assistant():
    """Instancia perezosa del asistente "responsable de la organización".

    Se crea bajo demanda (la GEMINI_API_KEY y ASSISTANT_MODEL se leen en runtime,
    después de cargar el .env) y se reconfigura con el provider/modelo del entorno.
    El contexto en vivo se arma con bot_state + los overviews del orquestador."""
    global _assistant
    if _assistant is None:
        from core.assistant import OrgAssistant, build_live_context

        def context_builder() -> str:
            state = bot_state.get_state()
            coord = (_orchestrator.coordinator_overview()
                     if _orchestrator is not None and hasattr(_orchestrator, "coordinator_overview")
                     else {})
            agents = (_orchestrator.agents_overview()
                      if _orchestrator is not None and hasattr(_orchestrator, "agents_overview")
                      else {})
            return build_live_context(state, coord, agents)

        _assistant = OrgAssistant(
            provider=os.getenv("ASSISTANT_PROVIDER", "gemini"),
            model=os.getenv("ASSISTANT_MODEL", "gemini-3.5-flash"),
        )
        _assistant.set_context_builder(context_builder)
    else:
        # Reaplica provider/modelo por si cambiaron en Ajustes (en caliente).
        _assistant.configure(
            provider=os.getenv("ASSISTANT_PROVIDER", "gemini"),
            model=os.getenv("ASSISTANT_MODEL", "gemini-3.5-flash"),
        )
    return _assistant


@app.route("/api/assistant/info", methods=["GET"])
def assistant_info():
    """Estado del asistente: provider/modelo y si la clave LLM está configurada."""
    provider = os.getenv("ASSISTANT_PROVIDER", "gemini").lower()
    model = os.getenv("ASSISTANT_MODEL", "gemini-3.5-flash")
    key_map = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY"}
    if provider == "ollama":
        available = True
    else:
        available = bool(os.getenv(key_map.get(provider, ""), "").strip())
    return jsonify({"provider": provider, "model": model, "available": available}), 200


@app.route("/api/assistant/chat", methods=["POST"])
def assistant_chat():
    """Envía un mensaje al asistente y devuelve su respuesta.
    Body: {"message": str, "session_id": str}. La memoria se guarda por sesión."""
    body = request.get_json(silent=True) or {}
    message = str(body.get("message", "")).strip()
    session_id = str(body.get("session_id") or "default")
    if not message:
        return jsonify({"error": "message vacío"}), 400
    try:
        result = _get_assistant().chat(session_id, message)
    except Exception as e:  # noqa: BLE001 — nunca devolver 500 silencioso al chat
        logger.error(f"assistant chat error: {e}")
        return jsonify({"error": str(e)}), 500
    return jsonify({"reply": result.get("reply", ""),
                    "suggestions": result.get("suggestions", []),
                    "session_id": session_id}), 200


@app.route("/api/assistant/history", methods=["GET"])
def assistant_history():
    """Historial de una sesión del asistente. ?session_id=..."""
    session_id = str(request.args.get("session_id") or "default")
    return jsonify({"session_id": session_id,
                    "history": _get_assistant().history(session_id)}), 200


@app.route("/api/assistant/reset", methods=["POST"])
def assistant_reset():
    """Olvida la conversación de una sesión."""
    body = request.get_json(silent=True) or {}
    session_id = str(body.get("session_id") or "default")
    _get_assistant().reset(session_id)
    return jsonify({"status": "ok", "session_id": session_id}), 200


@app.route("/api/models", methods=["GET"])
def get_models():
    """Proveedores/modelos LLM disponibles según las claves del .env.
    Lo usa el dashboard para poblar el selector de modelo de cada agente."""
    return jsonify(available_providers()), 200


@app.route("/api/agents/<name>/model", methods=["POST"])
@require_token
def set_agent_model(name):
    """Cambia el provider/modelo LLM de un agente en caliente.
    Body: {"provider": "gemini", "model": "gemini-2.0-flash"}."""
    if _orchestrator is None:
        return jsonify({"error": "orchestrator not running"}), 503
    body = request.get_json(silent=True) or {}
    provider, model = body.get("provider"), body.get("model")
    # Validar contra lo realmente disponible (clave configurada + modelo listado).
    providers = available_providers()
    if provider not in providers or model not in providers[provider]:
        return jsonify({"error": f"provider/modelo no disponible: {provider}/{model}"}), 400
    try:
        result = _orchestrator.set_agent_model(name, provider, model)
    except KeyError:
        return jsonify({"error": f"agente '{name}' no encontrado"}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    socketio.emit("agent_model_changed", result)
    return jsonify({"status": "ok", **result}), 200


@app.route("/api/agents/<name>/params", methods=["POST"])
@require_token
def set_agent_params(name):
    """Ajusta a mano los umbrales de señal de un agente en caliente.
    Body: {"min_rr": 1.3, "min_confidence": 0.6, ...}. Solo se aceptan las
    claves editables (min_confidence, min_rr, atr_sl_mult, atr_tp_mult);
    cada valor se recorta a su rango de seguridad."""
    if _orchestrator is None:
        return jsonify({"error": "orchestrator not running"}), 503
    body = request.get_json(silent=True) or {}
    try:
        result = _orchestrator.set_agent_params(name, body)
    except KeyError:
        return jsonify({"error": f"agente '{name}' no encontrado"}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    socketio.emit("agent_params_changed", result)
    return jsonify({"status": "ok", **result}), 200


@app.route("/api/agents/<name>/enabled", methods=["POST"])
@require_token
def set_agent_enabled(name):
    """Activa/desactiva un agente para las siguientes rotaciones.
    Body: {"enabled": true|false}."""
    if _orchestrator is None:
        return jsonify({"error": "orchestrator not running"}), 503
    body = request.get_json(silent=True) or {}
    enabled = bool(body.get("enabled", True))
    try:
        result = _orchestrator.set_agent_enabled(name, enabled)
    except KeyError:
        return jsonify({"error": f"agente '{name}' no encontrado"}), 404
    socketio.emit("agent_enabled_changed", result)
    return jsonify({"status": "ok", **result}), 200


@app.route("/api/agents/<name>/activate", methods=["POST"])
@require_token
def activate_agent(name):
    """Carga en caliente un agente del catálogo no seleccionado al arrancar, para
    que analice desde la siguiente rotación. Body opcional:
    {"provider": "gemini", "model": "gemini-3.5-flash"} para sobreescribir el LLM."""
    if _orchestrator is None:
        return jsonify({"error": "orchestrator not running"}), 503
    body = request.get_json(silent=True) or {}
    provider, model = body.get("provider"), body.get("model")
    # Si se especifica LLM, validarlo contra lo disponible.
    if provider or model:
        providers = available_providers()
        if provider not in providers or model not in providers.get(provider, []):
            return jsonify({"error": f"provider/modelo no disponible: {provider}/{model}"}), 400
    try:
        result = _orchestrator.add_agent(name, provider=provider, model=model)
    except KeyError:
        return jsonify({"error": f"agente '{name}' no existe en el catálogo"}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 409
    socketio.emit("agent_added", result)
    return jsonify({"status": "ok", **result}), 200


@app.route("/api/agents/save", methods=["POST"])
@require_token
def save_agents_selection():
    """Persiste la selección actual de agentes (los cargados + su provider/modelo
    y estado enabled) en .env (clave ACTIVE_AGENTS, JSON) para reusarla en la
    próxima sesión del bot. No reinicia nada: solo guarda. Protegido por API_TOKEN."""
    import json
    from core.settings_schema import write_env
    if _orchestrator is None:
        return jsonify({"error": "orchestrator not running"}), 503
    selection = [{
        "name": a.name,
        "provider": a.params.provider,
        "model": a.params.model,
        "enabled": bool(getattr(a, "enabled", True)),
    } for a in _orchestrator.agents]
    write_env({"ACTIVE_AGENTS": json.dumps(selection, ensure_ascii=False)})
    payload = {"status": "ok", "saved": selection}
    socketio.emit("agents_selection_saved", payload)
    return jsonify(payload), 200


@app.route("/api/agents/optimize", methods=["POST"])
@require_token
def optimize_agents():
    """Lanza una optimización. Por defecto dry-run (no modifica parámetros);
    pasa {"apply": true} para aplicarla en caliente."""
    if _orchestrator is None:
        return jsonify({"error": "orchestrator not running"}), 503
    apply = bool((request.get_json(silent=True) or {}).get("apply", False))
    report = _orchestrator.optimize(apply=apply)
    return jsonify({"applied": apply, "report": report}), 200


@app.route("/api/positions/close-all", methods=["POST"])
@require_token
def close_all_positions():
    """Cierra TODAS las posiciones abiertas de la cuenta. Recorre las posiciones
    vivas en el broker y cierra cada una (varias del mismo símbolo incluidas).
    Devuelve el recuento de cerradas y los símbolos con error."""
    if _mt_client is None:
        return jsonify({"error": "MT client not connected"}), 503
    try:
        positions = _mt_client.get_positions() or []
    except Exception as e:
        logger.error(f"Error listing positions for close-all: {e}")
        return jsonify({"error": str(e)}), 500

    closed, errors = 0, []
    # Una llamada a close_position(symbol) cierra UNA posición del símbolo; con
    # varias del mismo símbolo se llama tantas veces como posiciones haya.
    from collections import Counter
    counts = Counter(str(p.get("symbol")) for p in positions if p.get("symbol"))
    for symbol, n in counts.items():
        _tag_manual_close(symbol)
        for _ in range(n):
            try:
                result = _mt_client.close_position(symbol)
            except Exception as e:  # noqa: BLE001
                errors.append({"symbol": symbol, "error": str(e)})
                break
            if result is None:  # ya no quedan posiciones del símbolo
                break
            if not result.get("success"):
                errors.append({"symbol": symbol,
                               "error": result.get("error") or result.get("comment") or "close failed"})
                break
            bot_state.remove_position(symbol)
            closed += 1
            socketio.emit("position_closed", {"symbol": symbol})
    return jsonify({"status": "ok", "closed": closed, "errors": errors}), 200


@app.route("/api/positions/<symbol>/close", methods=["POST"])
@require_token
def close_position(symbol):
    if _mt_client is None:
        return jsonify({"error": "MT client not connected"}), 503
    try:
        _tag_manual_close(symbol)
        result = _mt_client.close_position(symbol)
        if result is None:
            return jsonify({"error": "No open position for symbol"}), 404
        if not result.get("success"):
            return jsonify({"error": result.get("error") or result.get("comment") or "close failed"}), 502
        bot_state.remove_position(symbol)
        socketio.emit("position_closed", {"symbol": symbol})
        return jsonify({"status": "closed", "symbol": symbol, "result": result}), 200
    except Exception as e:
        logger.error(f"Error closing position {symbol}: {e}")
        return jsonify({"error": str(e)}), 500


@socketio.on("connect")
def handle_connect(auth=None):
    logger.info(f"Client connected: {request.sid}")
    connected_clients.add(request.sid)
    emit("initial_state", bot_state.get_state())


@socketio.on("disconnect")
def handle_disconnect():
    logger.info(f"Client disconnected: {request.sid}")
    connected_clients.discard(request.sid)


@socketio.on("request_state")
def handle_request_state():
    emit("state_update", bot_state.get_state())


def broadcast_signal_update(signal_dict):
    socketio.emit("signal_update", signal_dict)


def broadcast_position_update(symbol, position_dict):
    socketio.emit("position_update", {"symbol": symbol, "position": position_dict})


def broadcast_account_update(account_dict):
    socketio.emit("account_update", account_dict)


def broadcast_state_update():
    """Emite el estado completo (incluye las posiciones ya sincronizadas) para
    que el dashboard refleje en vivo los cierres por TP/SL y el P/L flotante."""
    socketio.emit("state_update", bot_state.get_state())


def broadcast_trade_closed(trade_dict):
    socketio.emit("trade_closed", trade_dict)


def broadcast_coordinator_decision(payload):
    """Emite la última decisión de la mesa de dirección (snapshot + decisiones)."""
    socketio.emit("coordinator_decision", payload)


if __name__ == "__main__":
    host = os.getenv("API_HOST", "127.0.0.1")
    socketio.run(app, host=host, port=5000, debug=False, allow_unsafe_werkzeug=True)
