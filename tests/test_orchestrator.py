"""Tests de la optimización por reglas (tune_params) y helpers de posiciones."""
import time
from types import SimpleNamespace

from agents.base_agent import AgentParams
from agents.orchestrator import AgentOrchestrator, tune_params, _pos_direction, _clamp
from core.state import bot_state


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
    # MT4 da el type como entero.
    assert _pos_direction(SimpleNamespace(direction="BUY")) == "BUY"
    assert _pos_direction({"type": "0"}) == "BUY"
    assert _pos_direction({"type": "1"}) == "SELL"


# ----- Cooldown por pérdida (ventana móvil) -----

def _orch():
    # El __init__ no usa el cliente; basta con un placeholder para estos tests.
    orch = AgentOrchestrator([], client=None, platform="mt4")
    orch.max_daily_loss_pct = 0.05
    orch.risk_loss_window_seconds = 6 * 3600
    # Ventana ya abierta con equity base de referencia (no expirada).
    orch._risk_window_start = time.monotonic()
    orch._window_start_equity = 1000.0
    return orch


def test_cooldown_se_activa_y_no_detiene_el_bot():
    orch = _orch()
    bot_state.set_bot_running(True)
    orch._check_daily_loss_guard({"equity": 940.0})  # -6% > 5%
    assert orch._risk_cooldown_active() is True
    # Clave del requisito: el bot NO se detiene, sigue corriendo.
    assert bot_state.bot_running is True


def test_sin_cooldown_si_perdida_bajo_limite():
    orch = _orch()
    orch._check_daily_loss_guard({"equity": 970.0})  # -3% < 5%
    assert orch._risk_cooldown_active() is False


def test_cooldown_se_rearma_al_expirar_la_ventana():
    orch = _orch()
    # Activa el cooldown dentro de la ventana actual.
    orch._check_daily_loss_guard({"equity": 940.0})  # -6% > 5%
    assert orch._risk_cooldown_active() is True
    # Simula que la ventana expiró (inicio muy en el pasado).
    orch._risk_window_start = time.monotonic() - (orch.risk_loss_window_seconds + 1)
    # Ya expirada: el cooldown se considera rearmado aunque no haya corrido el guard.
    assert orch._risk_cooldown_active() is False
    # Al correr el guard, fija una nueva base con el equity actual y limpia cooldown.
    orch._check_daily_loss_guard({"equity": 940.0})
    assert orch._risk_cooldown_active() is False
    assert orch._window_start_equity == 940.0


def test_throttled_espacia_analisis():
    orch = _orch()
    orch._last_analysis_at["BTCUSD"] = time.time()
    assert orch._throttled("BTCUSD", 900) is True       # recién analizado
    orch._last_analysis_at["BTCUSD"] = time.time() - 1000
    assert orch._throttled("BTCUSD", 900) is False      # ya pasó el intervalo
