"""Tests de la persistencia añadida: contadores por agente (sobreviven al
reinicio) y parseo de la selección de agentes guardada (ACTIVE_AGENTS)."""
import os

from core.db import load_agent_stats, save_agent_stats
from core.config import get_active_agents


# ----- Contadores por agente (tabla agent_stats) -----

def test_agent_stats_vacio_por_defecto():
    assert load_agent_stats() == {}


def test_agent_stats_round_trip():
    stats = {
        "btc-agent": {"signals": 5, "trades": 2, "holds": 3},
        "eth-agent": {"signals": 1, "trades": 0, "holds": 1},
    }
    save_agent_stats(stats)
    assert load_agent_stats() == stats


def test_agent_stats_upsert_actualiza():
    save_agent_stats({"btc-agent": {"signals": 1, "trades": 0, "holds": 1}})
    save_agent_stats({"btc-agent": {"signals": 9, "trades": 4, "holds": 5}})
    out = load_agent_stats()
    assert out == {"btc-agent": {"signals": 9, "trades": 4, "holds": 5}}


# ----- Selección de agentes guardada (ACTIVE_AGENTS) -----

def test_active_agents_vacio_sin_env(monkeypatch):
    monkeypatch.delenv("ACTIVE_AGENTS", raising=False)
    assert get_active_agents() == []


def test_active_agents_json_invalido_devuelve_vacio(monkeypatch):
    monkeypatch.setenv("ACTIVE_AGENTS", "{ no es json valido")
    assert get_active_agents() == []


def test_active_agents_parsea_y_normaliza(monkeypatch):
    monkeypatch.setenv(
        "ACTIVE_AGENTS",
        '[{"name":"btc-agent","provider":"GEMINI","model":"gemini-2.0-flash","enabled":false},'
        '{"name":"eth-agent"}]',
    )
    out = get_active_agents()
    assert out == [
        {"name": "btc-agent", "provider": "gemini", "model": "gemini-2.0-flash", "enabled": False,
         "thinking": None, "reasoning_effort": None},
        {"name": "eth-agent", "provider": None, "model": None, "enabled": True,
         "thinking": None, "reasoning_effort": None},
    ]


def test_active_agents_ignora_items_sin_nombre(monkeypatch):
    monkeypatch.setenv("ACTIVE_AGENTS", '[{"provider":"gemini"}, {"name":"btc-agent"}]')
    out = get_active_agents()
    assert [a["name"] for a in out] == ["btc-agent"]
    os.environ.pop("ACTIVE_AGENTS", None)
