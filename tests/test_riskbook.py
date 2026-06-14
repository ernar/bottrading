"""Tests del coordinador: topes duros del RiskBook (funciones puras) y el
parseo/fallback del CoordinatorAgent (sin llamadas reales al LLM)."""
from agents.coordinator import RiskBook, CoordinatorAgent


def _rb(can_close=True, max_total=0.5, max_symbol=0.4, max_net=0.6,
        reversal=0.015, symbol_loss=0.0) -> RiskBook:
    return RiskBook({"max_total_exposure_pct": max_total,
                     "max_symbol_allocation_pct": max_symbol,
                     "can_close": can_close,
                     "max_net_direction_pct": max_net,
                     "reversal_drawdown_pct": reversal,
                     "max_symbol_loss_pct": symbol_loss})


def _snapshot(total_exposure=0.0, in_cooldown=False, max_total=0.5,
              max_symbol=0.4, equity=0.0, hedging=False, symbols=None) -> dict:
    return {
        "total_exposure_pct": total_exposure,
        "max_total_exposure_pct": max_total,
        "max_symbol_allocation_pct": max_symbol,
        "equity": equity,
        "hedging": hedging,
        "in_cooldown": in_cooldown,
        "open_positions_total": 0,
        "symbols": symbols or {},
    }


def _sym(remaining_pct=0.4, net_direction="FLAT", net_exposure_pct=0.0,
         open_positions=0, floating_pnl=0.0, long_positions=0, short_positions=0) -> dict:
    return {
        "remaining_pct": remaining_pct,
        "net_direction": net_direction,
        "net_exposure_pct": net_exposure_pct,
        "open_positions": open_positions,
        "floating_pnl": floating_pnl,
        "long_positions": long_positions,
        "short_positions": short_positions,
    }


def _decision(symbol="BTCUSD", approve=True, alloc=0.2, action="hold"):
    return {"symbol": symbol, "approve": approve, "priority": 1,
            "allocation_pct": alloc, "position_action": action}


# ----- RiskBook.clamp: topes duros -----

def test_clamp_acota_asignacion_al_tope_del_simbolo():
    rb = _rb(max_symbol=0.4)
    snap = _snapshot(symbols={"BTCUSD": {"remaining_pct": 0.4}})
    out = rb.clamp([_decision(alloc=0.9)], snap)
    assert out[0]["allocation_pct"] == 0.4
    assert "tope símbolo" in out[0]["clamp"]
    assert out[0]["approve"] is True


def test_clamp_veta_entrada_en_cooldown():
    rb = _rb()
    snap = _snapshot(in_cooldown=True, symbols={"BTCUSD": {"remaining_pct": 0.4}})
    out = rb.clamp([_decision()], snap)
    assert out[0]["approve"] is False
    assert "cooldown" in out[0]["clamp"]


def test_clamp_veta_si_exposicion_total_en_tope():
    rb = _rb(max_total=0.5)
    snap = _snapshot(total_exposure=0.55, symbols={"BTCUSD": {"remaining_pct": 0.4}})
    out = rb.clamp([_decision(alloc=0.1)], snap)
    assert out[0]["approve"] is False
    assert "exposición total" in out[0]["clamp"]


def test_clamp_veta_si_simbolo_sin_presupuesto():
    rb = _rb()
    snap = _snapshot(symbols={"BTCUSD": {"remaining_pct": 0.0}})
    out = rb.clamp([_decision(alloc=0.1)], snap)
    assert out[0]["approve"] is False
    assert "tope de asignación" in out[0]["clamp"]


def test_clamp_anula_cierre_si_no_puede_cerrar():
    rb = _rb(can_close=False)
    snap = _snapshot(symbols={"BTCUSD": {"remaining_pct": 0.4}})
    out = rb.clamp([_decision(approve=False, alloc=0.0, action="close")], snap)
    assert out[0]["position_action"] == "hold"
    assert "cierre desactivado" in out[0]["clamp"]


def test_clamp_no_toca_decision_valida():
    rb = _rb()
    snap = _snapshot(symbols={"BTCUSD": {"remaining_pct": 0.4}})
    out = rb.clamp([_decision(alloc=0.2)], snap)
    assert out[0]["approve"] is True
    assert out[0]["allocation_pct"] == 0.2
    assert out[0]["clamp"] == ""


# ----- CoordinatorAgent: parseo y fallback -----

def _coord(max_symbol=0.4) -> CoordinatorAgent:
    return CoordinatorAgent("ollama", "qwen3:8b", _rb(max_symbol=max_symbol))


def test_parse_json_valido():
    c = _coord()
    raw = ('texto previo {"rationale": "ok", "decisions": [{"symbol": "BTCUSD", '
           '"approve": true, "priority": 1, "allocation_pct": 0.25, '
           '"position_action": "hold", "reason": "x"}]} basura final')
    rationale, decisions = c._parse(raw)
    assert rationale == "ok"
    assert decisions[0]["symbol"] == "BTCUSD"
    assert decisions[0]["approve"] is True
    assert decisions[0]["allocation_pct"] == 0.25


def test_parse_json_invalido_devuelve_none():
    c = _coord()
    assert c._parse("aquí no hay json") is None


def test_fallback_aprueba_solo_accionables_con_reparto():
    c = _coord(max_symbol=0.4)
    signals = {
        "BTCUSD": {"symbol": "BTCUSD", "action": "BUY"},
        "ETHUSD": {"symbol": "ETHUSD", "action": "SELL"},
        "XAUUSD": {"symbol": "XAUUSD", "action": "HOLD"},
    }
    decisions = c._fallback(signals)
    assert len(decisions) == 2  # HOLD excluida
    assert all(d["approve"] for d in decisions)
    # Reparto igual = min(0.4, 1/2) = 0.4.
    assert all(d["allocation_pct"] == 0.4 for d in decisions)


def test_decide_cae_a_fallback_si_llm_no_responde(monkeypatch):
    c = _coord()
    monkeypatch.setattr(c.engine, "chat_json", lambda *a, **k: None)
    snap = _snapshot(symbols={"BTCUSD": {"remaining_pct": 0.4}})
    result = c.decide(snap, {"BTCUSD": {"symbol": "BTCUSD", "action": "BUY"}},
                      {"agents": []})
    assert "fallback" in result["rationale"]
    assert result["decisions"][0]["symbol"] == "BTCUSD"
    assert result["decisions"][0]["approve"] is True


# ----- Control de concentración direccional -----

def test_clamp_veta_apilamiento_en_direccion_saturada():
    rb = _rb(max_net=0.6)
    snap = _snapshot(equity=10000, symbols={"BTCUSD": _sym(
        net_direction="LONG", net_exposure_pct=0.65, open_positions=3)})
    signals = {"BTCUSD": {"symbol": "BTCUSD", "action": "BUY", "trend": "bullish"}}
    out = rb.clamp([_decision(alloc=0.2)], snap, signals)
    assert out[0]["approve"] is False
    assert "no apilar" in out[0]["clamp"]


def test_clamp_permite_entrada_opuesta_que_reduce_neto():
    rb = _rb(max_net=0.6)
    # Neto LONG saturado, pero la señal es SELL (reduce el neto) y sin pérdida:
    # la guardia de reversión no dispara y la entrada opuesta se permite.
    snap = _snapshot(equity=10000, symbols={"BTCUSD": _sym(
        net_direction="LONG", net_exposure_pct=0.65, open_positions=3)})
    signals = {"BTCUSD": {"symbol": "BTCUSD", "action": "SELL", "trend": "bearish"}}
    out = rb.clamp([_decision(alloc=0.2)], snap, signals)
    assert out[0]["approve"] is True
    assert out[0]["clamp"] == ""


def test_clamp_reversion_fuerza_reduce():
    rb = _rb(reversal=0.015)
    # Pérdida 2% (>= 1.5% pero < 3%) con libro LONG y tendencia bearish -> reduce.
    snap = _snapshot(equity=10000, symbols={"BTCUSD": _sym(
        net_direction="LONG", net_exposure_pct=0.3, open_positions=3, floating_pnl=-200.0)})
    signals = {"BTCUSD": {"symbol": "BTCUSD", "action": "HOLD", "trend": "bearish"}}
    out = rb.clamp([_decision(approve=False, action="hold")], snap, signals)
    assert out[0]["position_action"] == "reduce"
    assert out[0]["manage_direction"] == "BUY"
    assert "reversión" in out[0]["clamp"]


def test_clamp_reversion_grande_fuerza_close():
    rb = _rb(reversal=0.015)
    # Pérdida 4% (>= 2x del umbral) con libro SHORT y tendencia bullish -> close.
    snap = _snapshot(equity=10000, symbols={"BTCUSD": _sym(
        net_direction="SHORT", net_exposure_pct=-0.3, open_positions=2, floating_pnl=-400.0)})
    signals = {"BTCUSD": {"symbol": "BTCUSD", "action": "HOLD", "trend": "bullish"}}
    out = rb.clamp([_decision(approve=False, action="hold")], snap, signals)
    assert out[0]["position_action"] == "close"
    assert out[0]["manage_direction"] == "SELL"


def test_clamp_sin_signals_no_evalua_reversion():
    rb = _rb(reversal=0.015)
    snap = _snapshot(equity=10000, symbols={"BTCUSD": _sym(
        net_direction="LONG", net_exposure_pct=0.3, open_positions=3, floating_pnl=-200.0)})
    out = rb.clamp([_decision(approve=False, action="hold")], snap)  # sin signals
    assert out[0]["position_action"] == "hold"


def test_clamp_hard_stop_por_simbolo():
    rb = _rb(reversal=0.0, symbol_loss=0.03)  # reversión off, hard-stop a 3%
    # Sin conflicto de tendencia (bullish + LONG), pero el hard-stop dispara igual.
    snap = _snapshot(equity=10000, symbols={"BTCUSD": _sym(
        net_direction="LONG", net_exposure_pct=0.3, open_positions=2, floating_pnl=-350.0)})
    signals = {"BTCUSD": {"symbol": "BTCUSD", "action": "HOLD", "trend": "bullish"}}
    out = rb.clamp([_decision(approve=False, action="hold")], snap, signals)
    assert out[0]["position_action"] == "close"
    assert out[0]["manage_direction"] == "BUY"
    assert "hard-stop" in out[0]["clamp"]


def test_clamp_hedge_degrada_a_reduce_sin_hedging():
    rb = _rb(reversal=0.0)
    snap = _snapshot(equity=10000, hedging=False, symbols={"BTCUSD": _sym(
        net_direction="LONG", net_exposure_pct=0.3, open_positions=2, floating_pnl=-50.0)})
    out = rb.clamp([_decision(approve=False, action="hedge")], snap)
    assert out[0]["position_action"] == "reduce"
    assert out[0]["manage_direction"] == "BUY"
    assert "sin hedging" in out[0]["clamp"]


def test_clamp_hedge_anula_a_hold_sin_can_close():
    rb = _rb(can_close=False)
    snap = _snapshot(equity=10000, hedging=True, symbols={"BTCUSD": _sym(
        net_direction="LONG", net_exposure_pct=0.3, open_positions=2, floating_pnl=-50.0)})
    out = rb.clamp([_decision(approve=False, action="hedge")], snap)
    assert out[0]["position_action"] == "hold"
    assert "cobertura desactivada" in out[0]["clamp"]


def test_clamp_hedge_valido_se_mantiene_en_cuenta_hedging():
    rb = _rb(reversal=0.0)
    snap = _snapshot(equity=10000, hedging=True, symbols={"BTCUSD": _sym(
        net_direction="SHORT", net_exposure_pct=-0.3, open_positions=2, floating_pnl=-50.0)})
    out = rb.clamp([_decision(approve=False, action="hedge")], snap)
    assert out[0]["position_action"] == "hedge"
    assert out[0]["manage_direction"] == "SELL"


# ----- RiskBook.snapshot: cálculo del sesgo neto (con cliente mock) -----

class _FakeInfo:
    trade_contract_size = 1.0


class _FakeClient:
    def __init__(self, account, positions):
        self._account = account
        self._positions = positions

    def get_account_info(self):
        return self._account

    def get_positions(self, symbol=None):
        if symbol is None:
            return self._positions
        return [p for p in self._positions if p.get("symbol") == symbol]

    def get_symbol_info(self, symbol):
        return _FakeInfo()


class _FakeAgent:
    def __init__(self, symbol):
        self.symbol = symbol


def _account(equity=100000, used_margin=10000, hedging=True):
    return {"equity": equity, "balance": equity, "free_margin": equity - used_margin,
            "used_margin": used_margin, "hedging": hedging}


def test_snapshot_calcula_direccion_neta_long():
    rb = _rb()
    positions = [
        {"symbol": "BTCUSD", "direction": "BUY", "volume": 0.2, "current_price": 50000, "profit": 10.0},
        {"symbol": "BTCUSD", "direction": "BUY", "volume": 0.1, "current_price": 50000, "profit": -5.0},
        {"symbol": "BTCUSD", "direction": "SELL", "volume": 0.1, "current_price": 50000, "profit": 2.0},
    ]
    client = _FakeClient(_account(hedging=True), positions)
    snap = rb.snapshot(client, [_FakeAgent("BTCUSD")])
    s = snap["symbols"]["BTCUSD"]
    assert s["long_positions"] == 2
    assert s["short_positions"] == 1
    assert s["net_direction"] == "LONG"
    assert round(s["net_volume"], 6) == 0.2  # (0.2 + 0.1) - 0.1
    assert snap["hedging"] is True


def test_snapshot_neto_flat_cuando_se_netea():
    rb = _rb()
    positions = [
        {"symbol": "BTCUSD", "direction": "BUY", "volume": 0.1, "current_price": 50000, "profit": 0.0},
        {"symbol": "BTCUSD", "direction": "SELL", "volume": 0.1, "current_price": 50000, "profit": 0.0},
    ]
    client = _FakeClient(_account(hedging=False), positions)
    snap = rb.snapshot(client, [_FakeAgent("BTCUSD")])
    s = snap["symbols"]["BTCUSD"]
    assert s["net_direction"] == "FLAT"
    assert s["net_volume"] == 0.0
    assert snap["hedging"] is False
