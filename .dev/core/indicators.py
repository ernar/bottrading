"""Indicadores técnicos en Python puro (sin numpy/pandas).

Todas las funciones reciben listas de floats (cierres, máximos, mínimos)
ordenadas de la vela más antigua a la más reciente.
"""
from typing import List, Optional


def ema(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    result = sum(values[:period]) / period
    for v in values[period:]:
        result = v * k + result * (1 - k)
    return result


def ema_series(values: List[float], period: int) -> List[float]:
    if len(values) < period:
        return []
    k = 2 / (period + 1)
    series = [sum(values[:period]) / period]
    for v in values[period:]:
        series.append(v * k + series[-1] * (1 - k))
    return series


def rsi(closes: List[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def macd(closes: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Optional[dict]:
    if len(closes) < slow + signal:
        return None
    fast_series = ema_series(closes, fast)
    slow_series = ema_series(closes, slow)
    # Alinear: slow_series empieza (slow - fast) velas después
    offset = slow - fast
    macd_line = [f - s for f, s in zip(fast_series[offset:], slow_series)]
    if len(macd_line) < signal:
        return None
    signal_series = ema_series(macd_line, signal)
    macd_val = macd_line[-1]
    signal_val = signal_series[-1]
    return {
        "macd": macd_val,
        "signal": signal_val,
        "histogram": macd_val - signal_val,
    }


def bollinger(closes: List[float], period: int = 20, num_std: float = 2.0) -> Optional[dict]:
    if len(closes) < period:
        return None
    window = closes[-period:]
    mid = sum(window) / period
    variance = sum((c - mid) ** 2 for c in window) / period
    std = variance ** 0.5
    return {
        "upper": mid + num_std * std,
        "middle": mid,
        "lower": mid - num_std * std,
    }


def atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    # Wilder smoothing
    value = sum(trs[:period]) / period
    for tr in trs[period:]:
        value = (value * (period - 1) + tr) / period
    return value


def support_resistance(highs: List[float], lows: List[float], lookback: int = 50,
                       wing: int = 2, max_levels: int = 3) -> dict:
    """Detecta swing highs/lows como niveles de resistencia/soporte."""
    highs = highs[-lookback:]
    lows = lows[-lookback:]
    resistances, supports = [], []
    for i in range(wing, len(highs) - wing):
        if highs[i] == max(highs[i - wing:i + wing + 1]):
            resistances.append(highs[i])
        if lows[i] == min(lows[i - wing:i + wing + 1]):
            supports.append(lows[i])
    last_close_ref = (highs[-1] + lows[-1]) / 2
    resistances = sorted({r for r in resistances if r > last_close_ref})[:max_levels]
    supports = sorted({s for s in supports if s < last_close_ref}, reverse=True)[:max_levels]
    return {"supports": supports, "resistances": resistances}


def pct_change(closes: List[float], bars: int) -> Optional[float]:
    if len(closes) <= bars or closes[-bars - 1] == 0:
        return None
    return (closes[-1] - closes[-bars - 1]) / closes[-bars - 1] * 100
