from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import functools
import logging
import csv
import json
import os
from datetime import date
from core.state import bot_state
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
logger = logging.getLogger(__name__)


def set_mt_client(client):
    global _mt_client
    _mt_client = client


def set_orchestrator(orchestrator):
    """Registra el orquestador de agentes para exponer su estado en /api/agents."""
    global _orchestrator
    _orchestrator = orchestrator


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


def _read_csv_rows(path: str, limit: int) -> list:
    """Lee un CSV de logs de forma resiliente.

    restkey='_extra' evita que campos sobrantes (CSV con esquema desalineado)
    acaben bajo una clave None, lo que rompía jsonify al ordenar claves.
    """
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, restkey="_extra")
        rows = [{k: v for k, v in row.items() if k is not None} for row in reader]
    return rows[-limit:]


@app.route("/api/csv/signals", methods=["GET"])
def get_csv_signals():
    limit = int(request.args.get("limit", 15))
    platform = request.args.get("platform", "mt4").lower()
    return jsonify(_read_csv_rows(f"logs/{platform}/signals.csv", limit)), 200


@app.route("/api/csv/trades", methods=["GET"])
def get_csv_trades():
    limit = int(request.args.get("limit", 50))
    platform = request.args.get("platform", "mt4").lower()
    return jsonify(_read_csv_rows(f"logs/{platform}/trades.csv", limit)), 200


@app.route("/api/equity", methods=["GET"])
def get_equity():
    """Serie temporal de la cartera (balance/equity) para el gráfico de evolución
    del dashboard. Submuestreada a `limit` puntos."""
    from core.logger import read_equity_series
    limit = int(request.args.get("limit", 500))
    platform = request.args.get("platform", "mt4").lower()
    since_seconds = int(request.args.get("since", 0) or 0)
    return jsonify(read_equity_series(platform, limit, since_seconds)), 200


@app.route("/api/stats", methods=["GET"])
def get_stats():
    """Estadísticas agregadas de señales (CSV) y memoria de resultados."""
    platform = request.args.get("platform", "mt4").lower()
    today = date.today().isoformat()

    stats = {
        "signals_total": 0,
        "signals_today": 0,
        "by_action": {"BUY": 0, "SELL": 0, "HOLD": 0},
        "avg_confidence": None,
        "trades_total": 0,
        "memory": {"evaluated": 0, "wins": 0, "win_rate": None},
    }

    path = f"logs/{platform}/signals.csv"
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        stats["signals_total"] = len(rows)
        confidences = []
        for r in rows:
            action = r.get("action", "").upper()
            if action in stats["by_action"]:
                stats["by_action"][action] += 1
            if r.get("timestamp", "").startswith(today):
                stats["signals_today"] += 1
            try:
                confidences.append(float(r.get("confidence", 0)))
            except ValueError:
                pass
        if confidences:
            stats["avg_confidence"] = round(sum(confidences) / len(confidences), 3)

    trades_path = f"logs/{platform}/trades.csv"
    if os.path.exists(trades_path):
        with open(trades_path, newline="", encoding="utf-8") as f:
            stats["trades_total"] = sum(1 for _ in csv.DictReader(f))

    memory_path = "logs/memory.json"
    if os.path.exists(memory_path):
        try:
            with open(memory_path, encoding="utf-8") as f:
                memory = json.load(f)
            evaluated = [r for recs in memory.values() for r in recs if r.get("outcome")]
            wins = sum(1 for r in evaluated if r["outcome"] in ("favorable", "TP alcanzado"))
            stats["memory"]["evaluated"] = len(evaluated)
            stats["memory"]["wins"] = wins
            if evaluated:
                stats["memory"]["win_rate"] = round(wins / len(evaluated), 3)
        except (json.JSONDecodeError, OSError):
            pass

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


@app.route("/api/positions/<symbol>/close", methods=["POST"])
@require_token
def close_position(symbol):
    if _mt_client is None:
        return jsonify({"error": "MT client not connected"}), 503
    try:
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


def broadcast_trade_closed(trade_dict):
    socketio.emit("trade_closed", trade_dict)


def broadcast_coordinator_decision(payload):
    """Emite la última decisión de la mesa de dirección (snapshot + decisiones)."""
    socketio.emit("coordinator_decision", payload)


if __name__ == "__main__":
    host = os.getenv("API_HOST", "127.0.0.1")
    socketio.run(app, host=host, port=5000, debug=False, allow_unsafe_werkzeug=True)
