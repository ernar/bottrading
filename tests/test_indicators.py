"""Tests de los indicadores técnicos (Python puro, sin dependencias externas)."""
from core import indicators as ta


def test_ema_constante():
    # La EMA de una serie constante es ese mismo valor.
    assert ta.ema([5.0] * 30, 20) == 5.0


def test_ema_insuficientes_devuelve_none():
    assert ta.ema([1, 2, 3], 20) is None


def test_rsi_tendencia_alcista_pura():
    # Solo subidas -> sin pérdidas -> RSI 100.
    closes = [float(i) for i in range(1, 40)]
    assert ta.rsi(closes) == 100.0


def test_rsi_rango_valido():
    closes = [10, 11, 10.5, 11.2, 10.8, 11.5, 11.1, 12, 11.7, 12.3,
              12.0, 12.6, 12.2, 13, 12.7, 13.3]
    val = ta.rsi(closes)
    assert val is not None and 0 <= val <= 100


def test_bollinger_centrado_en_media():
    closes = [10.0] * 25
    bb = ta.bollinger(closes)
    assert bb["middle"] == 10.0
    # Sin dispersión, las bandas colapsan en la media.
    assert bb["upper"] == 10.0 and bb["lower"] == 10.0


def test_atr_positivo():
    highs = [float(i) + 1 for i in range(20)]
    lows = [float(i) for i in range(20)]
    closes = [float(i) + 0.5 for i in range(20)]
    atr = ta.atr(highs, lows, closes)
    assert atr is not None and atr > 0


def test_pct_change():
    assert ta.pct_change([100, 110], 1) == 10.0
    assert ta.pct_change([100], 5) is None


def test_support_resistance_separa_niveles():
    highs = [1, 3, 1, 5, 1, 4, 1, 2]
    lows = [0, -2, 0, -3, 0, -1, 0, 0]
    sr = ta.support_resistance(highs, lows, wing=1)
    # Resistencias por encima del precio de referencia, soportes por debajo.
    ref = (highs[-1] + lows[-1]) / 2
    assert all(r > ref for r in sr["resistances"])
    assert all(s < ref for s in sr["supports"])
