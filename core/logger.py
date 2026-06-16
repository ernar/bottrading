"""Registro de señales, órdenes, cierres y equity en la base de datos.

Antes esto se volcaba a CSV en ``logs/{platform}/``; ahora va a SQLite vía
``core/db.py``. Las firmas públicas (``log_signal``, ``log_trade``,
``log_equity``, ``log_closed_trade``, ``read_equity_series``) se mantienen
estables para no propagar cambios a los llamantes (orquestador/coordinador).
"""
from datetime import timedelta
from typing import Optional

from core.clock import broker_now
from core.db import ClosedTrade, EquityPoint, Signal, Trade, session_scope


def _to_float(value) -> Optional[float]:
    """Convierte a float tolerando cadenas vacías/ilegibles (-> None)."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def log_signal(signal: dict, platform: str = "mt4"):
    with session_scope() as s:
        s.add(Signal(
            timestamp=broker_now(),
            platform=platform.upper(),
            agent=signal.get("agent", ""),
            symbol=signal.get("symbol", ""),
            action=signal.get("action", ""),
            confidence=_to_float(signal.get("confidence", 0)),
            trend=signal.get("trend", ""),
            risk_level=signal.get("risk_level", ""),
            entry=_to_float(signal.get("entry")),
            stop_loss=_to_float(signal.get("stop_loss")),
            take_profit=_to_float(signal.get("take_profit")),
            reason=(signal.get("reason", "") or "").replace("\n", " "),
            trade_id=signal.get("trade_id", "") or "",
            executed=bool(signal.get("executed")),
        ))


def log_trade(symbol: str, action: str, volume: float, price: float,
              stop_loss: float, take_profit: float, result: dict,
              platform: str = "mt4"):
    with session_scope() as s:
        s.add(Trade(
            timestamp=broker_now(),
            platform=platform.upper(),
            symbol=symbol,
            action=action,
            volume=_to_float(volume),
            price=_to_float(price),
            stop_loss=_to_float(stop_loss),
            take_profit=_to_float(take_profit),
            retcode=str(result.get("retcode", "")),
            order_id=str(result.get("order", "")),
            comment=(result.get("comment", "") or "").replace("\n", " "),
        ))


def log_equity(balance: float, equity: float, free_margin: float = 0.0,
               platform: str = "mt4"):
    """Registra una instantánea de la cartera (balance/equity) para dibujar su
    evolución en el dashboard. El llamante decide la cadencia (throttle)."""
    with session_scope() as s:
        s.add(EquityPoint(
            timestamp=broker_now(),
            platform=platform.upper(),
            balance=_to_float(balance),
            equity=_to_float(equity),
            free_margin=_to_float(free_margin),
        ))


def read_equity_series(platform: str = "mt4", limit: int = 500,
                       since_seconds: int = 0) -> list:
    """Devuelve la serie de equity como lista de puntos
    ``{"t", "balance", "equity"}`` ordenada por tiempo. Si hay más filas que
    `limit`, submuestrea de forma uniforme conservando el primer y último punto.

    `since_seconds` > 0 filtra a las filas cuyo timestamp esté dentro de esa
    ventana (p. ej. 3600 = última hora). El filtro temporal se aplica ANTES del
    submuestreo para conservar la resolución dentro del rango.
    """
    from core.db import get_session

    session = get_session()
    try:
        q = session.query(EquityPoint).filter(
            EquityPoint.platform == platform.upper()
        )
        if since_seconds and since_seconds > 0:
            cutoff = broker_now() - timedelta(seconds=since_seconds)
            q = q.filter(EquityPoint.timestamp >= cutoff)
        rows = q.order_by(EquityPoint.timestamp.asc()).all()
    finally:
        session.close()

    points = [{
        "t": r.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        "balance": float(r.balance or 0),
        "equity": float(r.equity or 0),
    } for r in rows]

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
    """Escribe un trade cerrado para que el rendimiento sobreviva al reinicio."""
    with session_scope() as s:
        s.add(ClosedTrade(
            timestamp=broker_now(),
            platform=platform.upper(),
            symbol=symbol,
            action=action,
            volume=_to_float(volume),
            entry_price=_to_float(entry_price),
            exit_price=_to_float(exit_price),
            pnl=_to_float(pnl),
            commission=_to_float(commission),
            duration_seconds=int(duration_seconds) if duration_seconds else None,
            trade_id=trade_id or "",
            close_reason=close_reason or "",
        ))
