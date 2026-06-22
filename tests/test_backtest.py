"""Tests del simulador de fills (ReplayClient) y del motor de backtest.

Lo crítico a verificar es la correcta liquidación de SL/TP y el P/L resultante,
con casos sintéticos controlados.
"""
from clients.replay_client import ReplayClient
from core.backtest import run_backtest, make_baseline_signal_fn


def _client(candles, **kw):
    kw.setdefault("point", 1.0)
    kw.setdefault("digits", 2)
    kw.setdefault("contract_size", 1.0)
    kw.setdefault("spread_points", 0.0)
    kw.setdefault("commission_per_lot", 0.0)
    kw.setdefault("starting_balance", 1000.0)
    return ReplayClient("SYM", candles, **kw)


def _bar(t, o, h, l, c):
    return {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": 1}


def _one_shot(action, sl, tp, lot=1.0):
    state = {"done": False}

    def fn(client, symbol):
        if state["done"]:
            return None
        state["done"] = True
        tick = client.get_tick(symbol)
        entry = tick.ask if action == "BUY" else tick.bid
        return {"action": action, "entry": entry, "stop_loss": sl,
                "take_profit": tp, "volume": lot}
    return fn


def test_take_profit_fill_buy():
    candles = [_bar(0, 100, 100, 100, 100), _bar(1, 100, 100, 100, 100),
               _bar(2, 100, 111, 99, 105), _bar(3, 105, 106, 104, 105)]
    client = _client(candles)
    res = run_backtest(client, "SYM", _one_shot("BUY", 95, 110), warmup=1)
    assert res["trades"] == 1
    assert res["metrics"]["total_pnl"] == 10.0   # (110-100)*1*1
    assert res["ending_balance"] == 1010.0
    assert res["by_reason"] == {"Take Profit": 1}


def test_stop_loss_fill_buy():
    candles = [_bar(0, 100, 100, 100, 100), _bar(1, 100, 100, 100, 100),
               _bar(2, 100, 101, 94, 96), _bar(3, 96, 97, 95, 96)]
    client = _client(candles)
    res = run_backtest(client, "SYM", _one_shot("BUY", 95, 200), warmup=1)
    assert res["trades"] == 1
    assert res["metrics"]["total_pnl"] == -5.0   # (95-100)
    assert res["by_reason"] == {"Stop Loss": 1}


def test_sl_before_tp_when_both_in_bar():
    # La vela toca SL (94<=95) y TP (111>=110) a la vez: se asume SL primero (peor caso).
    candles = [_bar(0, 100, 100, 100, 100), _bar(1, 100, 100, 100, 100),
               _bar(2, 100, 111, 94, 100), _bar(3, 100, 101, 99, 100)]
    client = _client(candles)
    res = run_backtest(client, "SYM", _one_shot("BUY", 95, 110), warmup=1)
    assert res["metrics"]["total_pnl"] == -5.0
    assert res["by_reason"] == {"Stop Loss": 1}


def test_sell_take_profit_and_commission():
    # SELL entra a bid=100; TP=90 alcanzado (low<=90). P/L bruto = (100-90)=10;
    # comisión 2/lote -> neto 8.
    candles = [_bar(0, 100, 100, 100, 100), _bar(1, 100, 100, 100, 100),
               _bar(2, 100, 101, 89, 92), _bar(3, 92, 93, 91, 92)]
    client = _client(candles, commission_per_lot=2.0)
    res = run_backtest(client, "SYM", _one_shot("SELL", 110, 90), warmup=1)
    assert res["trades"] == 1
    assert res["metrics"]["total_pnl"] == 8.0
    assert res["metrics"]["commission"] == -2.0


def test_open_position_closed_at_end():
    # Sin SL/TP alcanzado: la posición se cierra a la última vela (105) -> +5.
    candles = [_bar(0, 100, 100, 100, 100), _bar(1, 100, 100, 100, 100),
               _bar(2, 102, 103, 101, 102), _bar(3, 104, 106, 103, 105)]
    client = _client(candles)
    res = run_backtest(client, "SYM", _one_shot("BUY", 1, 9999), warmup=1)
    assert res["trades"] == 1
    assert res["metrics"]["total_pnl"] == 5.0    # (105-100)


def test_spread_is_paid_on_entry():
    # spread 2 puntos (point=1): BUY entra a ask=102; TP=110 -> P/L = 8 (no 10).
    candles = [_bar(0, 100, 100, 100, 100), _bar(1, 100, 100, 100, 100),
               _bar(2, 100, 111, 99, 105)]
    client = _client(candles, spread_points=2.0)
    res = run_backtest(client, "SYM", _one_shot("BUY", 90, 110), warmup=1)
    assert res["metrics"]["total_pnl"] == 8.0


def test_baseline_runs_and_summarizes():
    # Tendencia alcista monótona: el motor debe correr y devolver un resumen válido.
    candles = [_bar(i, 100 + i, 100 + i + 1, 100 + i - 1, 100 + i) for i in range(80)]
    client = _client(candles, digits=2)
    res = run_backtest(client, "SYM", make_baseline_signal_fn(), warmup=30)
    assert set(res.keys()) >= {"metrics", "return_pct", "max_drawdown", "trades"}
    assert res["bars"] > 0


# ----- Modo COORDINADO (la mesa en el bucle) -----

def test_coordinated_backtest_runs_full_pipeline():
    """El backtest coordinado corre el pipeline real (snapshot -> decide -> clamp ->
    ejecutar) sobre VARIOS símbolos y deja operar a ambos cuando coinciden en
    dirección (consenso del grupo correlacionado BTC/ETH)."""
    from agents.registry import build_agent
    from agents.coordinator import RiskBook
    from core.config import get_coordinator_config
    from core.backtest import run_coordinated_backtest, DeterministicCoordinator
    from clients.replay_client import MultiSymbolReplayClient

    agents = [build_agent("btc-agent"), build_agent("eth-agent")]
    btc = [_bar(i, 100 + i, 101 + i, 99 + i, 100 + i) for i in range(60)]
    eth = [_bar(i, 50 + i * 0.5, 51 + i * 0.5, 49 + i * 0.5, 50 + i * 0.5) for i in range(60)]
    infos = {s: {"point": 1.0, "digits": 2, "contract_size": 1.0,
                 "spread_points": 0, "commission_per_lot": 0}
             for s in ("BTCUSD", "ETHUSD")}
    client = MultiSymbolReplayClient({"BTCUSD": btc, "ETHUSD": eth}, infos,
                                     starting_balance=1000.0)

    def mk():
        done = set()

        def fn(c, sym):
            if sym in done:
                return None
            done.add(sym)
            e = c.get_tick(sym).ask
            # Confianza por encima del techo de PARAM_BOUNDS (0.85) -> valida siempre,
            # independiente del tuning del .env.
            return {"action": "BUY", "entry": e, "stop_loss": e - 5, "take_profit": e + 10,
                    "confidence": 0.95, "trend": "bullish", "risk_level": "medio",
                    "reason": "test", "volume": 0.01}
        return fn

    risk_book = RiskBook(get_coordinator_config())
    coord = DeterministicCoordinator(risk_book)
    res = run_coordinated_backtest(client, agents, coord, risk_book, mk(),
                                   warmup=30, quiet=True)

    assert res["trades"] >= 2
    syms = {t["symbol"] for t in client.closed_trades}
    assert syms == {"BTCUSD", "ETHUSD"}      # ambas patas operaron (consenso BUY)
    assert res["metrics"]["total_pnl"] > 0   # ambas alcanzaron TP en la tendencia


def test_deterministic_coordinator_vetoes_opposite_correlated_pair():
    """La mesa determinista veta abrir patas OPUESTAS del grupo correlacionado
    (BTC largo / ETH corto): no se monta un pairs trade involuntario."""
    from agents.registry import build_agent
    from agents.coordinator import RiskBook
    from core.config import get_coordinator_config
    from core.backtest import DeterministicCoordinator
    from clients.replay_client import MultiSymbolReplayClient

    agents = [build_agent("btc-agent"), build_agent("eth-agent")]
    series = {s: [_bar(i, 100, 101, 99, 100) for i in range(40)] for s in ("BTCUSD", "ETHUSD")}
    infos = {s: {"point": 1.0, "digits": 2, "contract_size": 1.0} for s in series}
    client = MultiSymbolReplayClient(series, infos, starting_balance=1000.0)
    client.set_cursor(30)

    risk_book = RiskBook(get_coordinator_config())
    coord = DeterministicCoordinator(risk_book)
    signals = {
        "BTCUSD": {"action": "BUY", "confidence": 0.9, "symbol": "BTCUSD"},
        "ETHUSD": {"action": "SELL", "confidence": 0.7, "symbol": "ETHUSD"},
    }
    snap = risk_book.snapshot(client, agents, in_cooldown=False)
    decisions = {d["symbol"]: d for d in coord.decide(snap, signals, {"agents": []})["decisions"]}
    # Grupo plano: gana la entrada de mayor confianza (BTC BUY); la opuesta (ETH SELL) se veta.
    assert decisions["BTCUSD"]["approve"] is True
    assert decisions["ETHUSD"]["approve"] is False
    assert "correlacionado" in decisions["ETHUSD"]["clamp"]


def test_ohlcv_respects_cursor():
    candles = [_bar(i, 100, 101, 99, 100) for i in range(10)]
    client = _client(candles)
    client.set_cursor(4)
    assert len(client.get_ohlcv("SYM", "H1", 100)) == 5   # velas 0..4
    assert client.get_ohlcv("SYM", "H1", 2)[-1]["time"] == 4
