import csv
import os
from datetime import datetime

SIGNALS_HEADERS = [
    "timestamp", "platform", "agent", "symbol", "action", "confidence", "trend",
    "risk_level", "entry", "stop_loss", "take_profit", "reason", "trade_id",
    "executed",
]

TRADES_HEADERS = [
    "timestamp", "platform", "symbol", "action", "volume", "price",
    "stop_loss", "take_profit", "retcode", "order_id", "comment",
]

CLOSED_TRADES_HEADERS = [
    "timestamp", "platform", "symbol", "action", "volume", "entry_price",
    "exit_price", "pnl", "commission", "duration_seconds", "trade_id",
    "close_reason",
]

EQUITY_HEADERS = ["timestamp", "platform", "balance", "equity", "free_margin"]


def _signals_path(platform: str) -> str:
    return f"logs/{platform}/signals.csv"


def _trades_path(platform: str) -> str:
    return f"logs/{platform}/trades.csv"


def _closed_trades_path(platform: str) -> str:
    return f"logs/{platform}/closed_trades.csv"


def _equity_path(platform: str) -> str:
    return f"logs/{platform}/equity.csv"


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


def log_signal(signal: dict, platform: str = "mt4"):
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
        signal.get("trade_id", ""),
        "true" if signal.get("executed") else "false",
    ]
    with open(path, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)


def log_trade(symbol: str, action: str, volume: float, price: float,
              stop_loss: float, take_profit: float, result: dict,
              platform: str = "mt4"):
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


def log_equity(balance: float, equity: float, free_margin: float = 0.0,
               platform: str = "mt4"):
    """Registra una instantánea de la cartera (balance/equity) para dibujar su
    evolución en el dashboard. El llamante decide la cadencia (throttle)."""
    path = _equity_path(platform)
    _ensure_file(path, EQUITY_HEADERS)
    row = [
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        platform.upper(),
        f"{balance:.2f}", f"{equity:.2f}", f"{free_margin:.2f}",
    ]
    with open(path, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)


def read_equity_series(platform: str = "mt4", limit: int = 500) -> list:
    """Devuelve la serie de equity como lista de puntos
    ``{"t", "balance", "equity"}`` ordenada por tiempo. Si hay más filas que
    `limit`, submuestrea de forma uniforme conservando el primer y último punto.
    """
    path = _equity_path(platform)
    if not os.path.exists(path):
        return []
    points = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                points.append({
                    "t": r.get("timestamp", ""),
                    "balance": float(r.get("balance", 0) or 0),
                    "equity": float(r.get("equity", 0) or 0),
                })
            except ValueError:
                continue  # fila corrupta: se ignora
    if limit and len(points) > limit:
        # Submuestreo uniforme: paso = n/limit, manteniendo el último punto.
        step = len(points) / limit
        sampled = [points[int(i * step)] for i in range(limit)]
        sampled[-1] = points[-1]
        points = sampled
    return points


def log_closed_trade(symbol: str, action: str, volume: float, entry_price: float,
                     exit_price: float, pnl: float, commission: float = 0.0,
                     duration_seconds: int = None, trade_id: str = "",
                     close_reason: str = "", platform: str = "mt4"):
    """Escribe un trade cerrado en CSV persistente para que el rendimiento
    sobreviva al reinicio del bot."""
    path = _closed_trades_path(platform)
    _ensure_file(path, CLOSED_TRADES_HEADERS)
    row = [
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        platform.upper(),
        symbol, action, volume, entry_price,
        exit_price, f"{pnl:.2f}", f"{commission:.2f}",
        duration_seconds or "", trade_id, close_reason,
    ]
    with open(path, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)
