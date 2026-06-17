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


def ema_slope(values: List[float], period: int, lookback: int = 3) -> Optional[float]:
    """Pendiente reciente de la EMA(period): diferencia entre la EMA actual y la
    de hace `lookback` velas. >0 sube, <0 baja, None si faltan datos. Gira ANTES
    que el cruce EMA20/50 (que necesita que una media cruce a la otra), así que
    avisa de un cambio de momento con menos retardo."""
    series = ema_series(values, period)
    if len(series) <= lookback:
        return None
    return series[-1] - series[-1 - lookback]


def break_of_structure(highs: List[float], lows: List[float], closes: List[float],
                       wing: int = 2, lookback: int = 20) -> Optional[str]:
    """Ruptura de estructura: el último cierre supera el swing high confirmado más
    reciente ("up") o pierde el swing low confirmado más reciente ("down").

    Un swing necesita `wing` velas a cada lado para confirmarse, así que las
    últimas `wing` velas (aún sin confirmar) no cuentan como pivote. Detecta el
    cambio de carácter del precio (un giro rompe el último mínimo/máximo relevante)
    sin el retardo de un cruce de medias. Devuelve "up" | "down" | None."""
    if len(closes) < 2 * wing + 2:
        return None
    last_close = closes[-1]
    hi = highs[-lookback:]
    lo = lows[-lookback:]
    swing_high = swing_low = None
    # De la vela confirmada más reciente hacia atrás: el primer pivote en cada
    # sentido es el swing relevante más cercano.
    for i in range(len(hi) - wing - 1, wing - 1, -1):
        if swing_high is None and hi[i] == max(hi[i - wing:i + wing + 1]):
            swing_high = hi[i]
        if swing_low is None and lo[i] == min(lo[i - wing:i + wing + 1]):
            swing_low = lo[i]
        if swing_high is not None and swing_low is not None:
            break
    if swing_high is not None and last_close > swing_high:
        return "up"
    if swing_low is not None and last_close < swing_low:
        return "down"
    return None


def trend_state(closes: List[float], highs: List[float], lows: List[float],
                ema_period: int = 20) -> Optional[dict]:
    """Lectura DETERMINISTA de tendencia/momento, menos rezagada que el cruce
    EMA20/50 que mira el LLM. Vota cinco sub-señales —pendiente de la EMA,
    posición precio vs EMA, histograma MACD, momentum de 3 velas y ruptura de
    estructura— y las resume:

      direction: "bullish" | "bearish" | "sideways"  (sesgo de momento actual).
      reversal:  "bullish" | "bearish" | None  — GIRO confirmado: ruptura de
                 estructura con el momentum/MACD acompañando (cambio de carácter,
                 no mero ruido). Es el discriminador que justifica reaccionar
                 pronto a un giro (incluso recortando el período de gracia).
      score:     entero con signo (cuántas sub-señales apuntan a cada lado).

    Devuelve None si no hay velas suficientes."""
    if len(closes) < ema_period + 5:
        return None
    price = closes[-1]
    e = ema(closes, ema_period)
    slope = ema_slope(closes, ema_period)
    m = macd(closes)
    macd_hist = m["histogram"] if m else None
    chg = pct_change(closes, 3)
    broke = break_of_structure(highs, lows, closes)

    score = 0
    if slope is not None:
        score += 1 if slope > 0 else -1 if slope < 0 else 0
    if e is not None:
        score += 1 if price > e else -1
    if macd_hist is not None:
        score += 1 if macd_hist > 0 else -1 if macd_hist < 0 else 0
    if chg is not None:
        score += 1 if chg > 0 else -1 if chg < 0 else 0
    if broke == "up":
        score += 1
    elif broke == "down":
        score -= 1

    direction = "bullish" if score >= 2 else "bearish" if score <= -2 else "sideways"

    # Giro CONFIRMADO: la estructura se rompe en un sentido y el momentum/MACD no
    # lo contradicen. Más exigente que el simple `direction` para no dar falsas
    # alarmas en lateral.
    reversal = None
    if broke == "down" and (macd_hist is None or macd_hist < 0) and (chg is None or chg <= 0):
        reversal = "bearish"
    elif broke == "up" and (macd_hist is None or macd_hist > 0) and (chg is None or chg >= 0):
        reversal = "bullish"

    return {
        "direction": direction,
        "reversal": reversal,
        "score": score,
        "slope": slope,
        "macd_hist": macd_hist,
        "broke_structure": broke,
    }
