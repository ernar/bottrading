from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_socketio import SocketIO, emit
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
def start_bot():
    bot_state.set_bot_running(True)
    socketio.emit("bot_status", {"running": True})
    return jsonify({"status": "started"}), 200


@app.route("/api/bot/stop", methods=["POST"])
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
    platform = request.args.get("platform", "mt5").lower()
    return jsonify(_read_csv_rows(f"logs/{platform}/signals.csv", limit)), 200


@app.route("/api/csv/trades", methods=["GET"])
def get_csv_trades():
    limit = int(request.args.get("limit", 50))
    platform = request.args.get("platform", "mt5").lower()
    return jsonify(_read_csv_rows(f"logs/{platform}/trades.csv", limit)), 200


@app.route("/api/stats", methods=["GET"])
def get_stats():
    """Estadísticas agregadas de señales (CSV) y memoria de resultados."""
    platform = request.args.get("platform", "mt5").lower()
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


@app.route("/api/models", methods=["GET"])
def get_models():
    """Proveedores/modelos LLM disponibles según las claves del .env.
    Lo usa el dashboard para poblar el selector de modelo de cada agente."""
    return jsonify(available_providers()), 200


@app.route("/api/agents/<name>/model", methods=["POST"])
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
def optimize_agents():
    """Lanza una optimización. Por defecto dry-run (no modifica parámetros);
    pasa {"apply": true} para aplicarla en caliente."""
    if _orchestrator is None:
        return jsonify({"error": "orchestrator not running"}), 503
    apply = bool((request.get_json(silent=True) or {}).get("apply", False))
    report = _orchestrator.optimize(apply=apply)
    return jsonify({"applied": apply, "report": report}), 200


@app.route("/api/positions/<symbol>/close", methods=["POST"])
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


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True)
