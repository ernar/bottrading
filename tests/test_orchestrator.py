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
    # La mesa es obligatoria (todo el flujo es coordinado): placeholders simples
    # bastan, el __init__ solo asigna atributos al risk_book y lee coordinator.engine.
    orch = AgentOrchestrator([], client=None, platform="mt4",
                             coordinator=SimpleNamespace(),
                             risk_book=SimpleNamespace())
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


# ----- TP gobernado por la mesa (_apply_tp_rr) -----

class _TPClient:
    def get_symbol_info(self, symbol):
        return SimpleNamespace(digits=2)


def _orch_with_client(client):
    return AgentOrchestrator([], client=client, platform="mt4",
                             coordinator=SimpleNamespace(), risk_book=SimpleNamespace())


def test_apply_tp_rr_recalcula_buy():
    orch = _orch_with_client(_TPClient())
    agent = SimpleNamespace(symbol="X")
    sig = {"action": "BUY", "entry": 100.0, "stop_loss": 98.0, "take_profit": 110.0}
    assert orch._apply_tp_rr(agent, sig, 1.5) is True
    # riesgo = 2; TP = 100 + 1.5*2 = 103
    assert sig["take_profit"] == 103.0


def test_apply_tp_rr_recalcula_sell():
    orch = _orch_with_client(_TPClient())
    agent = SimpleNamespace(symbol="X")
    sig = {"action": "SELL", "entry": 100.0, "stop_loss": 102.0, "take_profit": 90.0}
    assert orch._apply_tp_rr(agent, sig, 1.5) is True
    # riesgo = 2; TP = 100 - 1.5*2 = 97
    assert sig["take_profit"] == 97.0


def test_apply_tp_rr_no_actua_sin_niveles_o_rr_cero():
    orch = _orch_with_client(_TPClient())
    agent = SimpleNamespace(symbol="X")
    sig = {"action": "BUY", "entry": 100.0, "stop_loss": 98.0, "take_profit": 110.0}
    assert orch._apply_tp_rr(agent, sig, 0.0) is False
    assert sig["take_profit"] == 110.0  # sin tocar
    sig2 = {"action": "BUY", "entry": 0, "stop_loss": 0, "take_profit": 0}
    assert orch._apply_tp_rr(agent, sig2, 1.5) is False


# ----- Trailing stop: el SL nunca afloja (_improves_sl) -----

def test_improves_sl_buy_solo_sube():
    assert AgentOrchestrator._improves_sl("BUY", 101.0, 100.0) is True
    assert AgentOrchestrator._improves_sl("BUY", 99.0, 100.0) is False
    assert AgentOrchestrator._improves_sl("BUY", 99.0, 0.0) is True  # sin SL previo


def test_improves_sl_sell_solo_baja():
    assert AgentOrchestrator._improves_sl("SELL", 99.0, 100.0) is True
    assert AgentOrchestrator._improves_sl("SELL", 101.0, 100.0) is False
    assert AgentOrchestrator._improves_sl("SELL", 0.0, 100.0) is False  # nivel inválido


# ----- Gestión de ciclo de vida: trailing + parcial -----

class _LifecycleClient:
    """Cliente falso que registra modify_position / close_position."""
    def __init__(self, positions):
        self._positions = positions
        self.closed = []
        self.modified = []

    def get_positions(self, symbol=None):
        return list(self._positions)

    def get_tick(self, symbol):
        return SimpleNamespace(ask=105.01, bid=105.0)

    def get_atr(self, symbol):
        return 2.0

    def get_symbol_info(self, symbol):
        return SimpleNamespace(digits=2, volume_min=0.01, volume_step=0.01)

    def close_position(self, symbol, direction=None, volume=None, ticket=None):
        self.closed.append((symbol, volume, ticket))
        return {"success": True}

    def modify_position(self, symbol, ticket, stop_loss=None, take_profit=None):
        self.modified.append((symbol, ticket, stop_loss, take_profit))
        return {"success": True}


def _lifecycle_agent():
    params = AgentParams(use_trailing_stop=True, trailing_breakeven_atr_mult=1.0,
                         trailing_step_atr_mult=0.5, partial_profit_trigger_pct=0.5,
                         partial_profit_pct=0.5)
    return SimpleNamespace(name="btc", symbol="BTCUSD", params=params)


def test_lifecycle_parcial_una_vez_y_trailing():
    pos = {"ticket": 7, "type": "0", "open_price": 100.0, "sl": 98.0,
           "tp": 110.0, "volume": 0.10}  # BUY, precio 105 = mitad del camino al TP
    client = _LifecycleClient([pos])
    orch = AgentOrchestrator([_lifecycle_agent()], client=client, platform="mt4",
                             coordinator=SimpleNamespace(), risk_book=SimpleNamespace())
    orch._manage_position_lifecycle()
    # Parcial: cierra la mitad del volumen (0.05) del ticket 7.
    assert client.closed == [("BTCUSD", 0.05, 7)]

    # Segunda pasada con la MISMA posición (mismo linaje): no re-parcializa, pero
    # el trailing sí mueve el SL a 104 (105 - 0.5*2), por encima del SL 98.
    orch._manage_position_lifecycle()
    assert client.closed == [("BTCUSD", 0.05, 7)]  # sigue una sola vez
    assert client.modified and client.modified[-1][2] == 104.0  # nuevo SL


def test_lifecycle_sin_agentes_activos_no_hace_nada():
    params = AgentParams(use_trailing_stop=False, partial_profit_trigger_pct=0.0)
    agent = SimpleNamespace(name="btc", symbol="BTCUSD", params=params)
    client = _LifecycleClient([{"ticket": 1, "type": "0", "open_price": 100.0,
                                "sl": 98.0, "tp": 110.0, "volume": 0.10}])
    orch = AgentOrchestrator([agent], client=client, platform="mt4",
                             coordinator=SimpleNamespace(), risk_book=SimpleNamespace())
    orch._manage_position_lifecycle()
    assert client.closed == [] and client.modified == []
