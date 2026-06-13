"""Tests de la optimización por reglas (tune_params) y helpers de posiciones."""
from types import SimpleNamespace

from agents.base_agent import AgentParams
from agents.orchestrator import tune_params, _pos_direction, _clamp


def _perf(samples=10, win_rate=0.5, sl_hit_rate=0.2, tp_hit_rate=0.3, avg_move_pct=0.1):
    return {"samples": samples, "win_rate": win_rate, "sl_hit_rate": sl_hit_rate,
            "tp_hit_rate": tp_hit_rate, "avg_move_pct": avg_move_pct}


def test_datos_insuficientes_no_cambia():
    params = AgentParams()
    new, reasons = tune_params(params, _perf(samples=3), hold_rate=0.0)
    assert new == params
    assert "insuficientes" in reasons[0]


def test_win_rate_bajo_sube_selectividad():
    params = AgentParams(min_confidence=0.60, min_rr=1.0)
    new, _ = tune_params(params, _perf(win_rate=0.30), hold_rate=0.2)
    assert new.min_confidence == 0.65
    assert new.min_rr == 1.10


def test_muchos_sl_amplia_stops():
    params = AgentParams(atr_sl_mult=1.5, atr_tp_mult=2.0)
    new, _ = tune_params(params, _perf(sl_hit_rate=0.50), hold_rate=0.2)
    assert new.atr_sl_mult == 1.8
    assert new.atr_tp_mult == 2.3


def test_clamp_respeta_limites():
    # min_confidence está acotado a [0.50, 0.85]
    assert _clamp(0.95, "min_confidence") == 0.85
    assert _clamp(0.10, "min_confidence") == 0.50


def test_pos_direction_normaliza_mt4():
    # MT5 da texto; MT4 da el type como entero.
    assert _pos_direction(SimpleNamespace(direction="BUY")) == "BUY"
    assert _pos_direction({"type": "0"}) == "BUY"
    assert _pos_direction({"type": "1"}) == "SELL"
