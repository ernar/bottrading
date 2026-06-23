"""Tests del motor de señal DETERMINISTA (core/signals.py) y del modo determinista
del agente (sin LLM)."""
from clients.replay_client import ReplayClient
from core.signals import deterministic_signal
from agents.base_agent import AgentParams, SymbolAgent


def _client(candles):
    return ReplayClient("SYM", candles, point=1.0, digits=2, contract_size=1.0)


def _uptrend(n=60):
    return [{"time": i, "open": 100 + i, "high": 100 + i + 1, "low": 100 + i - 1,
             "close": 100 + i, "volume": 1} for i in range(n)]


def _flat(n=60):
    return [{"time": i, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1}
            for i in range(n)]


def test_deterministic_signal_buys_uptrend():
    c = _client(_uptrend()); c.set_cursor(59)
    sig = deterministic_signal(c, "SYM", timeframe="H1", atr_sl_mult=1.5, atr_tp_mult=4.0)
    assert sig and sig["action"] == "BUY"
    assert sig["stop_loss"] < sig["entry"] < sig["take_profit"]
    assert sig["confidence"] == 0.99
    # R:R ≈ 4/1.5
    risk = sig["entry"] - sig["stop_loss"]; reward = sig["take_profit"] - sig["entry"]
    assert round(reward / risk, 1) == round(4.0 / 1.5, 1)


def test_deterministic_signal_none_when_flat():
    c = _client(_flat()); c.set_cursor(59)
    assert deterministic_signal(c, "SYM") is None      # score ~0 < min_score 2


def test_deterministic_min_score_filters_weak_trends():
    c = _client(_uptrend()); c.set_cursor(59)
    assert deterministic_signal(c, "SYM", min_score=6) is None   # exige voto imposible


def test_agent_deterministic_mode_no_llm(monkeypatch):
    """En modo determinista, analyze() produce señal SIN llamar al LLM."""
    import core.strategy as strat

    def _boom(*a, **k):
        raise AssertionError("no debe llamarse al LLM en modo determinista")
    monkeypatch.setattr(strat.StrategyEngine, "generate_signal", _boom, raising=True)

    params = AgentParams(signal_mode="deterministic", timeframe="H1",
                         atr_sl_mult=1.5, atr_tp_mult=4.0, min_confidence=0.5, min_rr=1.0)
    agent = SymbolAgent("t-agent", "SYM", params)
    client = _client(_uptrend()); client.set_cursor(59)
    sig = agent.analyze(client, platform="mt4")
    assert sig and sig["action"] == "BUY"
    assert sig["agent"] == "t-agent"


def test_deterministic_coordinator_importable():
    # Debe vivir en agents.coordinator y re-exportarse desde core.backtest.
    from agents.coordinator import DeterministicCoordinator as A
    from core.backtest import DeterministicCoordinator as B
    assert A is B
