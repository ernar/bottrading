"""Tests de la memoria de señales: evaluación provisional vs terminal."""
from datetime import datetime, timedelta

from sqlalchemy import select

from core.db import SignalMemoryRecord, get_session, session_scope
from core.memory import SignalMemory


def _mem():
    return SignalMemory(scope="test")


def _record(mem, action="BUY", price=100.0, sl=95.0, tp=110.0, conf=0.7):
    mem.record_signal("BTCUSD", {"action": action, "confidence": conf,
                                 "stop_loss": sl, "take_profit": tp}, price=price)


def _last():
    """Último registro de memoria persistido (para aserciones)."""
    session = get_session()
    try:
        return session.scalars(
            select(SignalMemoryRecord).order_by(SignalMemoryRecord.id.desc())
        ).first()
    finally:
        session.close()


def _backdate(seconds):
    """Antedata el último registro para simular el paso del tiempo."""
    with session_scope() as session:
        rec = session.scalars(
            select(SignalMemoryRecord).order_by(SignalMemoryRecord.id.desc())
        ).first()
        rec.timestamp = datetime.now() - timedelta(seconds=seconds)


def test_no_evalua_antes_de_la_edad_minima():
    mem = _mem()
    _record(mem)
    _backdate(60)  # 1 min < 30 min
    mem.evaluate_pending("BTCUSD", 111)
    rec = _last()
    assert rec.outcome is None and rec.final is False


def test_tp_es_terminal_y_cuenta_como_win():
    mem = _mem()
    _record(mem, sl=95, tp=110)
    _backdate(3600)
    mem.evaluate_pending("BTCUSD", 111)  # supera el TP
    rec = _last()
    assert rec.outcome == "TP alcanzado" and rec.final is True
    perf = mem.get_performance("BTCUSD")
    assert perf["samples"] == 1 and perf["win_rate"] == 1.0 and perf["tp_hit_rate"] == 1.0


def test_sl_es_terminal():
    mem = _mem()
    _record(mem, sl=95, tp=110)
    _backdate(3600)
    mem.evaluate_pending("BTCUSD", 94)  # cae por debajo del SL
    rec = _last()
    assert rec.outcome == "SL tocado" and rec.final is True
    perf = mem.get_performance("BTCUSD")
    assert perf["sl_hit_rate"] == 1.0 and perf["win_rate"] == 0.0


def test_provisional_no_cuenta_y_se_actualiza():
    mem = _mem()
    _record(mem, sl=95, tp=110)
    _backdate(3600)
    # Precio entre SL y TP, dentro de la ventana: outcome provisional, no terminal.
    mem.evaluate_pending("BTCUSD", 105)
    rec = _last()
    assert rec.outcome == "favorable" and rec.final is False
    assert mem.get_performance("BTCUSD")["samples"] == 0

    # Más tarde toca el SL: ahora sí es terminal (no se quedó congelado).
    mem.evaluate_pending("BTCUSD", 94)
    rec = _last()
    assert rec.final is True and rec.outcome == "SL tocado"
    assert mem.get_performance("BTCUSD")["samples"] == 1


def test_provisional_se_finaliza_por_antiguedad():
    mem = _mem()
    _record(mem, sl=95, tp=110)
    _backdate(25 * 3600)  # > 24h
    mem.evaluate_pending("BTCUSD", 105)  # sin tocar SL/TP, pero ya es viejo
    rec = _last()
    assert rec.final is True and rec.outcome == "favorable"
    assert mem.get_performance("BTCUSD")["samples"] == 1


def test_poda_a_max_registros_por_simbolo():
    mem = _mem()
    for i in range(35):
        _record(mem, price=100.0 + i)
    session = get_session()
    try:
        n = session.scalars(select(SignalMemoryRecord)).all()
    finally:
        session.close()
    assert len(n) == 30  # MAX_RECORDS_PER_SYMBOL
