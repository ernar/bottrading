"""Test del alineado por timestamp del volcado de histórico."""
from scripts.dump_history import align_by_time


def _c(t, price=100):
    return {"time": t, "open": price, "high": price + 1, "low": price - 1,
            "close": price, "volume": 1}


def test_align_keeps_common_timestamps_sorted():
    series = {
        "BTCUSD": [_c(3), _c(1), _c(2), _c(5)],   # tiene 1,2,3,5 (desordenado)
        "ETHUSD": [_c(2), _c(3), _c(4)],          # tiene 2,3,4
    }
    out = align_by_time(series)
    assert [c["time"] for c in out["BTCUSD"]] == [2, 3]   # intersección {2,3}, ordenado
    assert [c["time"] for c in out["ETHUSD"]] == [2, 3]


def test_align_single_symbol_just_sorts():
    out = align_by_time({"BTCUSD": [_c(3), _c(1), _c(2)]})
    assert [c["time"] for c in out["BTCUSD"]] == [1, 2, 3]


def test_align_empty():
    assert align_by_time({}) == {}
