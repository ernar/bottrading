"""Cálculo de métricas de una operación (profit/pérdida potencial, R:R, comisión).

Extraído de main.py para que tanto el bucle principal como el orquestador de
agentes lo reutilicen sin duplicar lógica.
"""
from clients.base_client import BaseMTClient


def calc_trade_metrics(client: BaseMTClient, symbol: str, action: str,
                       entry: float, stop_loss: float, take_profit: float,
                       volume: float, commission_per_lot: float = 7.0) -> dict:
    sym = client.get_symbol_info(symbol)
    if not sym or not entry or not stop_loss or not take_profit:
        return {}

    point = sym.point
    tick_value = getattr(sym, "trade_tick_value", 1.0)

    direction = 1 if action == "BUY" else -1
    pips_tp = direction * (take_profit - entry) / point
    pips_sl = direction * (entry - stop_loss) / point

    potential_profit = pips_tp * tick_value * volume
    potential_loss = pips_sl * tick_value * volume
    commission = commission_per_lot * volume

    rr = round(pips_tp / pips_sl, 2) if pips_sl else 0

    return {
        "potential_profit": round(potential_profit, 2),
        "potential_loss": round(potential_loss, 2),
        "commission": round(commission, 2),
        "net_profit": round(potential_profit - commission, 2),
        "net_loss": round(potential_loss + commission, 2),
        "rr": rr,
        "pips_tp": round(pips_tp, 1),
        "pips_sl": round(pips_sl, 1),
    }
