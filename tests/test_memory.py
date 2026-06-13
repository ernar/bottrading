"""Tests de la memoria de señales: evaluación provisional vs terminal."""
from datetime import datetime, timedelta

from core.memory import SignalMemory


def _mem(tmp_path):
    return SignalMemory(path=str(tmp_path / "mem.json"))


def _record(mem, action="BUY", price=100.0, sl=95.0, tp=110.0, conf=0.7):
    mem.record_signal("BTCUSD", {"action": action, "confidence": conf,
                                 "stop_loss": sl, "take_profit": tp}, price=price)


def _backdate(mem, seconds):
    rec = mem._data["BTCUSD"][-1]
    rec["timestamp"] = (datetime.now() - timedelta(seconds=seconds)).isoformat(timespec="seconds")


def test_no_evalua_antes_de_la_edad_minima(tmp_path):
    mem = _mem(tmp_path)
    _record(mem)
    _backdate(mem, 60)  # 1 min < 30 min
    mem.evaluate_pending("BTCUSD", 111)
    rec = mem._data["BTCUSD"][-1]
    assert rec["outcome"] is None and rec["final"] is False


def test_tp_es_terminal_y_cuenta_como_win(tmp_path):
    mem = _mem(tmp_path)
    _record(mem, sl=95, tp=110)
    _backdate(mem, 3600)
    mem.evaluate_pending("BTCUSD", 111)  # supera el TP
    rec = mem._data["BTCUSD"][-1]
    assert rec["outcome"] == "TP alcanzado" and rec["final"] is True
    perf = mem.get_performance("BTCUSD")
    assert perf["samples"] == 1 and perf["win_rate"] == 1.0 and perf["tp_hit_rate"] == 1.0


def test_sl_es_terminal(tmp_path):
    mem = _mem(tmp_path)
    _record(mem, sl=95, tp=110)
    _backdate(mem, 3600)
    mem.evaluate_pending("BTCUSD", 94)  # cae por debajo del SL
    rec = mem._data["BTCUSD"][-1]
    assert rec["outcome"] == "SL tocado" and rec["final"] is True
    perf = mem.get_performance("BTCUSD")
    assert perf["sl_hit_rate"] == 1.0 and perf["win_rate"] == 0.0


def test_provisional_no_cuenta_y_se_actualiza(tmp_path):
    mem = _mem(tmp_path)
    _record(mem, sl=95, tp=110)
    _backdate(mem, 3600)
    # Precio entre SL y TP, dentro de la ventana: outcome provisional, no terminal.
    mem.evaluate_pending("BTCUSD", 105)
    rec = mem._data["BTCUSD"][-1]
    assert rec["outcome"] == "favorable" and rec["final"] is False
    assert mem.get_performance("BTCUSD")["samples"] == 0

    # Más tarde toca el SL: ahora sí es terminal (no se quedó congelado).
    mem.evaluate_pending("BTCUSD", 94)
    rec = mem._data["BTCUSD"][-1]
    assert rec["final"] is True and rec["outcome"] == "SL tocado"
    assert mem.get_performance("BTCUSD")["samples"] == 1


def test_provisional_se_finaliza_por_antiguedad(tmp_path):
    mem = _mem(tmp_path)
    _record(mem, sl=95, tp=110)
    _backdate(mem, 25 * 3600)  # > 24h
    mem.evaluate_pending("BTCUSD", 105)  # sin tocar SL/TP, pero ya es viejo
    rec = mem._data["BTCUSD"][-1]
    assert rec["final"] is True and rec["outcome"] == "favorable"
    assert mem.get_performance("BTCUSD")["samples"] == 1
