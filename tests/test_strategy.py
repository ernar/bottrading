"""Tests de validación de señales y dimensionado de lote (sin llamar al LLM)."""
from types import SimpleNamespace

from core.models import BotConfig
from core.strategy import StrategyEngine


def _engine(**config_kwargs):
    cfg = BotConfig(debug_mode=False, **config_kwargs)
    # provider="openai" no instancia cliente Ollama en __init__.
    return StrategyEngine(cfg, provider="openai", min_confidence=0.6, min_rr=1.5)


def _buy_signal(confidence=0.7, entry=100.0, sl=99.0, tp=102.0):
    return {
        "action": "BUY", "confidence": confidence,
        "entry": entry, "stop_loss": sl, "take_profit": tp,
        "risk_level": "medium",
    }


def _tick(ask=100.0, bid=99.99):
    return SimpleNamespace(ask=ask, bid=bid)


def test_acepta_buy_valido():
    eng = _engine()
    assert eng.validate_trade(_buy_signal(), positions=[], tick=_tick()) is True


def test_rechaza_confianza_baja():
    eng = _engine()
    assert eng.validate_trade(_buy_signal(confidence=0.50), positions=[], tick=_tick()) is False


def test_rechaza_rr_insuficiente():
    eng = _engine()
    # R:R = 0.5 (riesgo 1, beneficio 0.5) < min_rr 1.5
    sig = _buy_signal(entry=100, sl=99, tp=100.5)
    assert eng.validate_trade(sig, positions=[], tick=_tick()) is False


def test_rechaza_niveles_incoherentes_buy():
    eng = _engine()
    sig = _buy_signal(entry=100, sl=101, tp=102)  # SL por encima del entry en un BUY
    assert eng.validate_trade(sig, positions=[], tick=_tick()) is False


def test_rechaza_entry_alejado_del_mercado():
    eng = _engine()
    # entry 105 frente a ask 100 -> 5% > MAX_ENTRY_DEVIATION_PCT
    sig = _buy_signal(entry=105, sl=104, tp=107)
    assert eng.validate_trade(sig, positions=[], tick=_tick(ask=100)) is False


def test_rechaza_spread_alto():
    eng = _engine(max_spread_filter=2.0)
    assert eng.validate_trade(_buy_signal(), positions=[], tick=_tick(),
                              spread_points=5.0) is False


def test_rechaza_max_posiciones_globales():
    eng = _engine(max_open_positions=3)
    assert eng.validate_trade(_buy_signal(), positions=[], tick=_tick(),
                              total_open_positions=3) is False


def test_override_confianza_salta_max_posiciones():
    eng = _engine(max_open_positions=3, max_pos_override_confidence=0.90)
    sig = _buy_signal(confidence=0.95)
    assert eng.validate_trade(sig, positions=[], tick=_tick(),
                              total_open_positions=3) is True


def test_permite_apilar_misma_direccion_bajo_el_limite():
    # El proyecto permite varias posiciones en la misma dirección (reforzar);
    # el control es max_open_positions, no un veto a duplicar.
    eng = _engine(max_open_positions=5)
    pos = SimpleNamespace(direction="BUY")
    assert eng.validate_trade(_buy_signal(), positions=[pos], tick=_tick(),
                              total_open_positions=1) is True


# ----- calculate_lot_size -----

def test_lot_size_riesgo_basico():
    eng = _engine(risk_per_trade=0.02)
    # riesgo 200; pérdida/lote = (1/0.01)*1 = 100 -> 2.0 lotes
    lot = eng.calculate_lot_size(account_balance=10000, entry_price=100,
                                 stop_loss_price=99, point=0.01, tick_value=1.0)
    assert lot == 2.0


def test_lot_size_redondea_hacia_abajo_al_step():
    eng = _engine(risk_per_trade=0.02)
    # raw = 200 / ((0.7/0.01)*1) = 2.857 -> 2.85 con step 0.01
    lot = eng.calculate_lot_size(account_balance=10000, entry_price=100,
                                 stop_loss_price=99.3, point=0.01, tick_value=1.0)
    assert lot == 2.85


def test_lot_size_respeta_minimo():
    eng = _engine(risk_per_trade=0.02)
    # raw muy por debajo del mínimo -> devuelve volume_min
    lot = eng.calculate_lot_size(account_balance=10, entry_price=100,
                                 stop_loss_price=99, point=0.01, tick_value=1.0,
                                 volume_min=0.01)
    assert lot == 0.01


def test_lot_size_datos_invalidos():
    eng = _engine()
    assert eng.calculate_lot_size(account_balance=10000, entry_price=100,
                                  stop_loss_price=100, point=0.01,
                                  tick_value=1.0) == 0.01
