"""Tests del módulo de métricas honestas (core/performance.py).

Reproduce el bug real: el balance cae (P/L de trading negativo, con un depósito de
por medio) mientras ``closed_trades`` registra una pérdida mucho menor (flotante,
no realizado). El módulo debe sacar a la luz esa discrepancia.
"""
from datetime import datetime, timedelta

from core.db import ClosedTrade, EquityPoint, session_scope
from core import performance


def _seed_equity(balances, platform="MT4"):
    """Inserta puntos de equity con timestamps crecientes y distintos."""
    base = datetime(2026, 6, 15, 8, 0, 0)
    with session_scope() as s:
        for i, bal in enumerate(balances):
            s.add(EquityPoint(timestamp=base + timedelta(minutes=i), platform=platform,
                              balance=float(bal), equity=float(bal), free_margin=float(bal)))


def _seed_closed(trades, platform="MT4"):
    """trades = lista de dicts {symbol, action, pnl, commission?, duration?}."""
    base = datetime(2026, 6, 15, 9, 0, 0)
    with session_scope() as s:
        for i, t in enumerate(trades):
            s.add(ClosedTrade(
                timestamp=base + timedelta(minutes=i), platform=platform,
                symbol=t["symbol"], action=t["action"], volume=0.01,
                entry_price=100.0, exit_price=100.0, pnl=t["pnl"],
                commission=t.get("commission", 0.0),
                duration_seconds=t.get("duration", 60),
            ))


def test_ledger_subtracts_known_deposit():
    # 100 -> 150 (+50 depósito) -> 130 (-20) -> 138 (+8) -> 110 (-28). Neto +10.
    _seed_equity([100, 150, 130, 138, 110])
    s = performance.ledger_summary("mt4", cash_flows=[50.0])
    assert s["start_balance"] == 100.0
    assert s["end_balance"] == 110.0
    assert s["net_change"] == 10.0
    assert s["cash_flows_known"] is True
    assert s["trading_pnl"] == -40.0          # 10 neto − 50 depósito
    # El depósito (+50) se excluye de gross/PF; quedan +8 / (-20-28).
    assert s["gross_profit"] == 8.0
    assert s["gross_loss"] == -48.0
    assert s["profit_factor"] == round(8.0 / 48.0, 2)


def test_ledger_without_cash_flows_flags_unknown():
    _seed_equity([100, 150, 110])
    s = performance.ledger_summary("mt4", cash_flows=None)
    assert s["cash_flows_known"] is False
    assert s["trading_pnl"] == s["net_change"] == 10.0  # incluye el depósito sin restar


def test_balance_ledger_counts_steps():
    _seed_equity([100, 100, 105, 105, 95])  # cambios reales: +5, -10
    steps = performance.balance_ledger("mt4")
    assert [x["delta"] for x in steps] == [5.0, -10.0]


def test_closed_trades_metrics():
    _seed_closed([
        {"symbol": "BTCUSD", "action": "BUY", "pnl": 2.0},
        {"symbol": "BTCUSD", "action": "SELL", "pnl": -1.0},
        {"symbol": "ETHUSD", "action": "BUY", "pnl": -3.0},
        {"symbol": "ETHUSD", "action": "BUY", "pnl": 1.0},
    ])
    m = performance.closed_trades_summary("mt4")
    assert m["count"] == 4
    assert m["wins"] == 2 and m["losses"] == 2
    assert m["win_rate"] == 0.5
    assert m["total_pnl"] == -1.0
    assert m["profit_factor"] == round(3.0 / 4.0, 2)


def test_discrepancy_surfaces_underreporting():
    # Realidad: pierde 40 (con depósito de 50). Registro: solo -5.
    _seed_equity([100, 150, 130, 138, 110])
    _seed_closed([
        {"symbol": "BTCUSD", "action": "BUY", "pnl": 3.0},
        {"symbol": "BTCUSD", "action": "BUY", "pnl": -8.0},
    ])
    summ = performance.performance_summary("mt4", cash_flows=[50.0])
    rv = summ["recorded_vs_real"]
    assert rv["recorded_pnl"] == -5.0
    assert rv["real_trading_pnl"] == -40.0
    assert rv["discrepancy"] == -35.0
    assert rv["reliable"] is False


def test_data_quality_flags():
    _seed_closed([
        {"symbol": "BTCUSD", "action": "BUY", "pnl": 1.0, "commission": 0.0, "duration": 60},
        {"symbol": "BTCUSD", "action": "BUY", "pnl": -1.0, "commission": 0.0, "duration": -30},
    ])
    dq = performance.data_quality("mt4")
    assert dq["closed_count"] == 2
    assert dq["commission_all_zero"] is True
    assert dq["negative_durations"] == 1


def test_by_dimension_groups():
    _seed_closed([
        {"symbol": "BTCUSD", "action": "BUY", "pnl": 2.0},
        {"symbol": "ETHUSD", "action": "SELL", "pnl": -1.0},
    ])
    by_sym = performance.by_dimension("mt4", "symbol")
    assert set(by_sym.keys()) == {"BTCUSD", "ETHUSD"}
    by_dir = performance.by_dimension("mt4", "direction")
    assert set(by_dir.keys()) == {"BUY", "SELL"}


def test_format_lines_runs():
    _seed_equity([100, 150, 110])
    _seed_closed([{"symbol": "BTCUSD", "action": "BUY", "pnl": -2.0}])
    lines = performance.format_summary_lines(performance.performance_summary("mt4", cash_flows=[50.0]))
    assert any("RENDIMIENTO REAL" in ln for ln in lines)
