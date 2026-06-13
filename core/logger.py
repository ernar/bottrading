import csv
import os
from datetime import datetime

SIGNALS_HEADERS = [
    "timestamp", "platform", "agent", "symbol", "action", "confidence", "trend",
    "risk_level", "entry", "stop_loss", "take_profit", "reason",
]

TRADES_HEADERS = [
    "timestamp", "platform", "symbol", "action", "volume", "price",
    "stop_loss", "take_profit", "retcode", "order_id", "comment",
]


def _signals_path(platform: str) -> str:
    return f"logs/{platform}/signals.csv"


def _trades_path(platform: str) -> str:
    return f"logs/{platform}/trades.csv"


def _ensure_file(path: str, headers: list):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        # Si la cabecera existente no coincide con la actual (p.ej. se añadió
        # la columna 'agent'), archiva el CSV viejo y crea uno nuevo: así no se
        # mezclan esquemas y la API no peta al leer filas desalineadas.
        with open(path, newline="", encoding="utf-8") as f:
            existing = next(csv.reader(f), None)
        if existing == headers:
            return
        os.replace(path, path + ".old")
    with open(path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(headers)


def log_signal(signal: dict, platform: str = "mt5"):
    path = _signals_path(platform)
    _ensure_file(path, SIGNALS_HEADERS)
    row = [
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        platform.upper(),
        signal.get("agent", ""),
        signal.get("symbol", ""),
        signal.get("action", ""),
        f"{signal.get('confidence', 0):.2f}",
        signal.get("trend", ""),
        signal.get("risk_level", ""),
        signal.get("entry", ""),
        signal.get("stop_loss", ""),
        signal.get("take_profit", ""),
        signal.get("reason", "").replace("\n", " "),
    ]
    with open(path, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)


def log_trade(symbol: str, action: str, volume: float, price: float,
              stop_loss: float, take_profit: float, result: dict,
              platform: str = "mt5"):
    path = _trades_path(platform)
    _ensure_file(path, TRADES_HEADERS)
    row = [
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        platform.upper(),
        symbol, action, volume, price, stop_loss, take_profit,
        result.get("retcode", ""),
        result.get("order", ""),
        result.get("comment", "").replace("\n", " "),
    ]
    with open(path, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)
