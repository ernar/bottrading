"""Tests de los perfiles de trading (riesgo + horizonte): que el nivel activo se
lee bien y que las directivas de prompt salen pobladas (es lo que hace que el
perfil cambie de verdad el comportamiento del LLM), y que el horizonte mueve los
parámetros de gestión de posición vía overrides _DEFAULT."""
import os

from core.profiles import (
    RISK_PROFILES, HORIZON_PROFILES, get_active_risk, get_active_horizon,
    build_agent_directive, build_coordinator_directive,
)
from core.config import get_agent_param_overrides


def test_niveles_activos_default(monkeypatch):
    monkeypatch.delenv("RISK_PROFILE", raising=False)
    monkeypatch.delenv("HORIZON", raising=False)
    assert get_active_risk() == "moderate"
    assert get_active_horizon() == "medio"


def test_niveles_activos_desde_env(monkeypatch):
    monkeypatch.setenv("RISK_PROFILE", "AGGRESSIVE")  # tolera mayúsculas
    monkeypatch.setenv("HORIZON", "corto")
    assert get_active_risk() == "aggressive"
    assert get_active_horizon() == "corto"


def test_nivel_invalido_cae_a_default(monkeypatch):
    monkeypatch.setenv("RISK_PROFILE", "loquesea")
    monkeypatch.setenv("HORIZON", "xxx")
    assert get_active_risk() == "moderate"
    assert get_active_horizon() == "medio"


def test_directivas_pobladas_y_con_tono():
    # Agresivo + corto debe producir directivas no vacías y con su tono.
    ag = build_agent_directive("aggressive", "corto")
    co = build_coordinator_directive("aggressive", "corto")
    assert ag and co
    assert "AGRESIVO" in ag and "CORTO" in ag
    assert "AGRESIVO" in co and "CORTO" in co
    # Conservador + largo: tono opuesto.
    assert "CONSERVADOR" in build_agent_directive("conservative", "largo")
    assert "LARGO" in build_agent_directive("conservative", "largo")


def test_tablas_tienen_los_cuatro_y_tres_niveles():
    assert set(RISK_PROFILES) == {"conservative", "moderate", "aggressive", "extreme"}
    assert set(HORIZON_PROFILES) == {"corto", "medio", "largo"}


def test_horizonte_define_multiplicador_de_posiciones():
    # Cada horizonte fija el multiplicador del nº máximo de posiciones; corto abre
    # más concurrentes que largo.
    mult = {h: float(HORIZON_PROFILES[h]["MAX_POSITIONS_HORIZON_MULT"])
            for h in ("corto", "medio", "largo")}
    assert mult["corto"] > mult["medio"] > mult["largo"]


def test_max_posiciones_combina_riesgo_y_horizonte(monkeypatch):
    # El nº máximo de posiciones por símbolo que gobierna la mesa = base del perfil
    # de RIESGO (MAX_OPEN_POSITIONS_DEFAULT) × multiplicador del HORIZONTE.
    from core.config import get_coordinator_config
    monkeypatch.setenv("MAX_OPEN_POSITIONS_DEFAULT", "4")
    monkeypatch.setenv("MAX_POSITIONS_HORIZON_MULT", "1.5")
    assert get_coordinator_config()["max_open_positions"] == 6   # round(4 × 1.5)
    monkeypatch.setenv("MAX_POSITIONS_HORIZON_MULT", "0.6")
    assert get_coordinator_config()["max_open_positions"] == 2   # round(4 × 0.6 = 2.4)
    # Suelo en 1 aunque el producto redondee por debajo.
    monkeypatch.setenv("MAX_OPEN_POSITIONS_DEFAULT", "1")
    assert get_coordinator_config()["max_open_positions"] == 1


def test_horizonte_mueve_params_de_gestion(monkeypatch):
    # El horizonte "corto" escribe estas claves _DEFAULT; get_agent_param_overrides
    # debe devolverlas para que apply_params las propague a los agentes.
    for k, v in HORIZON_PROFILES["corto"].items():
        monkeypatch.setenv(k, v)
    ov = get_agent_param_overrides("BTCUSD", "gemini-2.0-flash")
    assert ov["atr_tp_mult"] == 1.2
    assert ov["trailing_breakeven_atr_mult"] == 0.6
    assert ov["partial_profit_trigger_pct"] == 0.3
    for k in HORIZON_PROFILES["corto"]:
        os.environ.pop(k, None)
