"""Señal DETERMINISTA de seguimiento de tendencia (sin LLM).

Única fuente de verdad del edge validado en backtest (BTC/ETH en D1): vota
``core.indicators.trend_state`` (pendiente EMA + precio vs EMA + MACD + momentum +
ruptura de estructura) y, si la mayoría es suficiente, entra a favor con SL/TP por
múltiplos de ATR. La usan TANTO el backtest (``core/backtest.make_baseline_signal_fn``)
COMO el agente en vivo (``SymbolAgent.analyze`` en modo ``signal_mode="deterministic"``),
para que lo que se opera en real sea EXACTAMENTE lo medido.

No llama al LLM (coste $0). ``confidence`` alta (0.99) y R:R amplio a propósito: la
base no tiene "confianza" calibrable, así que debe superar los umbrales
(min_confidence/min_rr) del especialista; si no, ``validate_trade`` la rechazaría.
"""
from typing import Optional

from core import indicators as ta


def deterministic_signal(client, symbol: str, *, timeframe: str = "H1",
                         atr_sl_mult: float = 1.5, atr_tp_mult: float = 3.0,
                         min_score: int = 2, require_break: bool = False,
                         confidence: float = 0.99, lot: float = 0.01,
                         bars: int = 150) -> Optional[dict]:
    """Devuelve el dict de señal (BUY/SELL con entry/SL/TP) o None si no hay setup.

    Selectividad de régimen: ``min_score`` exige una mayoría más amplia del voto de
    ``trend_state`` (2 = base; 4-5 = solo tendencias fuertes); ``require_break`` exige
    además ruptura de estructura confirmada en el sentido de la entrada."""
    rates = client.get_ohlcv(symbol, timeframe, bars)
    if not rates or len(rates) < 40:
        return None
    closes = [r["close"] for r in rates]
    highs = [r["high"] for r in rates]
    lows = [r["low"] for r in rates]
    ts = ta.trend_state(closes, highs, lows)
    if not ts:
        return None
    score = ts.get("score", 0)
    if abs(score) < min_score:
        return None
    action = "BUY" if score > 0 else "SELL"
    if require_break:
        broke = ts.get("broke_structure")
        if (action == "BUY" and broke != "up") or (action == "SELL" and broke != "down"):
            return None

    tick = client.get_tick(symbol)
    atr = client.get_atr(symbol, timeframe=timeframe)
    if not tick or not atr or atr <= 0:
        return None
    sym_info = client.get_symbol_info(symbol)
    digits = getattr(sym_info, "digits", 2) if sym_info else 2
    entry = tick.ask if action == "BUY" else tick.bid
    sign = 1 if action == "BUY" else -1
    return {
        "action": action,
        "entry": entry,
        "stop_loss": round(entry - sign * atr_sl_mult * atr, digits),
        "take_profit": round(entry + sign * atr_tp_mult * atr, digits),
        "confidence": confidence,
        "trend": "bullish" if action == "BUY" else "bearish",
        "risk_level": "medio",
        "volume": lot,
        "reason": f"determinista trend_state (score {score}) {timeframe}",
    }
