"""Tests del detector determinista de momento/estructura (funciones puras):
`ema_slope`, `break_of_structure` y `trend_state`."""
from core import indicators as ta


def _zigzag(start: float, impulse: float, pullback: float, legs: int = 6,
            up: int = 3, down: int = 2):
    """Genera una serie en zigzag (impulsos + retrocesos) para tener swings
    reales (higher-lows / lower-highs), como una tendencia de verdad."""
    closes = [start]
    for _ in range(legs):
        for _ in range(up):
            closes.append(round(closes[-1] + impulse, 2))
        for _ in range(down):
            closes.append(round(closes[-1] - pullback, 2))
    return closes


def _hl(closes, wick: float = 1.0):
    return [c + wick for c in closes], [c - wick for c in closes]


# ----- trend_state: dirección de momento -----

def test_trend_state_alcista():
    closes = _zigzag(100.0, 5, 2)            # tendencia alcista clara
    highs, lows = _hl(closes)
    ts = ta.trend_state(closes, highs, lows)
    assert ts["direction"] == "bullish"
    assert ts["score"] > 0
    assert ts["slope"] > 0


def test_trend_state_bajista():
    closes = _zigzag(200.0, -5, -2)          # tendencia bajista clara
    highs, lows = _hl(closes)
    ts = ta.trend_state(closes, highs, lows)
    assert ts["direction"] == "bearish"
    assert ts["score"] < 0
    assert ts["slope"] < 0


def test_trend_state_datos_insuficientes():
    assert ta.trend_state([1, 2, 3], [1, 2, 3], [1, 2, 3]) is None


# ----- break_of_structure / reversal: el giro que se nos escapaba -----

def test_giro_bajista_confirmado():
    # Alcista en zigzag y luego desplome: rompe el último swing low -> giro bajista.
    closes = _zigzag(100.0, 5, 2)
    for _ in range(6):
        closes.append(round(closes[-1] - 6, 2))
    highs, lows = _hl(closes)
    assert ta.break_of_structure(highs, lows, closes) == "down"
    ts = ta.trend_state(closes, highs, lows)
    assert ts["reversal"] == "bearish"
    assert ts["direction"] == "bearish"


def test_giro_alcista_confirmado():
    # Bajista en zigzag y luego rebote: rompe el último swing high -> giro alcista.
    closes = _zigzag(200.0, -5, -2)
    for _ in range(6):
        closes.append(round(closes[-1] + 6, 2))
    highs, lows = _hl(closes)
    assert ta.break_of_structure(highs, lows, closes) == "up"
    ts = ta.trend_state(closes, highs, lows)
    assert ts["reversal"] == "bullish"


def test_sin_ruptura_en_tendencia_limpia():
    # Una tendencia alcista sin desplome no marca giro (reversal None).
    closes = _zigzag(100.0, 5, 2)
    highs, lows = _hl(closes)
    ts = ta.trend_state(closes, highs, lows)
    assert ts["reversal"] is None


# ----- ema_slope -----

def test_ema_slope_signo():
    subiendo = [float(i) for i in range(40)]
    bajando = [float(40 - i) for i in range(40)]
    assert ta.ema_slope(subiendo, 20) > 0
    assert ta.ema_slope(bajando, 20) < 0
    assert ta.ema_slope([1, 2], 20) is None
