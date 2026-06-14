"""Tests del coordinador: topes duros del RiskBook (funciones puras) y el
parseo/fallback del CoordinatorAgent (sin llamadas reales al LLM)."""
from agents.coordinator import RiskBook, CoordinatorAgent


def _rb(can_close=True, max_total=0.5, max_symbol=0.4, max_net=0.6,
        reversal=0.015, symbol_loss=0.0, min_hold=0.0, llm_can_close=True) -> RiskBook:
    # min_hold por defecto 0 (gracia off) y llm_can_close=True (gestión
    # discrecional permitida) para no alterar los tests existentes; el default
    # real de producción es llm_can_close=False (solo fuerza mayor).
    return RiskBook({"max_total_exposure_pct": max_total,
                     "max_symbol_allocation_pct": max_symbol,
                     "can_close": can_close,
                     "max_net_direction_pct": max_net,
                     "reversal_drawdown_pct": reversal,
                     "max_symbol_loss_pct": symbol_loss,
                     "min_hold_seconds": min_hold,
                     "llm_can_close": llm_can_close})


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
         open_positions=0, floating_pnl=0.0, long_positions=0, short_positions=0,
         newest_position_age=None) -> dict:
    return {
        "remaining_pct": remaining_pct,
        "net_direction": net_direction,
        "net_exposure_pct": net_exposure_pct,
        "open_positions": open_positions,
        "floating_pnl": floating_pnl,
        "long_positions": long_positions,
        "short_positions": short_positions,
        # None = edad desconocida (la gracia no aplica, como en los tests previos).
        "newest_position_age": newest_position_age,
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
    return CoordinatorAgent("gemini", "gemini-2.0-flash", _rb(max_symbol=max_symbol))


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


# ----- Fuerza mayor: la mesa no ejecuta gestión discrecional del LLM -----

def test_clamp_ignora_reduce_discrecional_del_llm():
    # Caso del usuario: el LLM propone reduce por "exposición excesiva". Con
    # llm_can_close=False (default real) se ignora: la posición tiene su S/L.
    rb = _rb(reversal=0.0, llm_can_close=False)
    snap = _snapshot(equity=10000, symbols={"BTCUSD": _sym(
        net_direction="LONG", net_exposure_pct=0.3, open_positions=2, floating_pnl=-50.0)})
    out = rb.clamp([_decision(approve=False, action="reduce")], snap)
    assert out[0]["position_action"] == "hold"
    assert out[0].get("manage_direction") is None
    assert "fuerza mayor" in out[0]["clamp"]


def test_clamp_ignora_hedge_discrecional_del_llm():
    rb = _rb(reversal=0.0, llm_can_close=False)
    snap = _snapshot(equity=10000, hedging=True, symbols={"BTCUSD": _sym(
        net_direction="LONG", net_exposure_pct=0.3, open_positions=2, floating_pnl=-50.0)})
    out = rb.clamp([_decision(approve=False, action="hedge")], snap)
    assert out[0]["position_action"] == "hold"


def test_clamp_hard_stop_actua_aunque_llm_no_pueda_cerrar():
    # Fuerza mayor: el hard-stop determinista cierra aunque la gestión
    # discrecional del LLM esté desactivada.
    rb = _rb(reversal=0.0, symbol_loss=0.03, llm_can_close=False)
    snap = _snapshot(equity=10000, symbols={"BTCUSD": _sym(
        net_direction="LONG", net_exposure_pct=0.3, open_positions=2, floating_pnl=-350.0)})
    signals = {"BTCUSD": {"symbol": "BTCUSD", "action": "HOLD", "trend": "bullish"}}
    out = rb.clamp([_decision(approve=False, action="hold")], snap, signals)
    assert out[0]["position_action"] == "close"
    assert "hard-stop" in out[0]["clamp"]


def test_clamp_reversion_actua_aunque_llm_no_pueda_cerrar():
    rb = _rb(reversal=0.015, llm_can_close=False)
    snap = _snapshot(equity=10000, symbols={"BTCUSD": _sym(
        net_direction="LONG", net_exposure_pct=0.3, open_positions=3, floating_pnl=-200.0)})
    signals = {"BTCUSD": {"symbol": "BTCUSD", "action": "HOLD", "trend": "bearish"}}
    out = rb.clamp([_decision(approve=False, action="hold")], snap, signals)
    assert out[0]["position_action"] == "reduce"
    assert "reversión" in out[0]["clamp"]


def test_clamp_reduce_discrecional_se_permite_si_se_habilita():
    # Con llm_can_close=True el reduce discrecional del LLM se respeta.
    rb = _rb(reversal=0.0, llm_can_close=True)
    snap = _snapshot(equity=10000, symbols={"BTCUSD": _sym(
        net_direction="LONG", net_exposure_pct=0.3, open_positions=2, floating_pnl=-50.0)})
    out = rb.clamp([_decision(approve=False, action="reduce")], snap)
    assert out[0]["position_action"] == "reduce"


# ----- Período de gracia para posiciones recién abiertas -----

def test_clamp_gracia_pausa_reversion():
    # Mismo escenario que test_clamp_reversion_fuerza_reduce pero la posición es
    # recién abierta (30s < 300s de gracia): la reversión NO se fuerza.
    rb = _rb(reversal=0.015, min_hold=300)
    snap = _snapshot(equity=10000, symbols={"BTCUSD": _sym(
        net_direction="LONG", net_exposure_pct=0.3, open_positions=3,
        floating_pnl=-200.0, newest_position_age=30)})
    signals = {"BTCUSD": {"symbol": "BTCUSD", "action": "HOLD", "trend": "bearish"}}
    out = rb.clamp([_decision(approve=False, action="hold")], snap, signals)
    assert out[0]["position_action"] == "hold"
    assert "gracia" in out[0]["clamp"]


def test_clamp_gracia_aplaza_close_del_llm():
    # El LLM propone close sobre una posición recién abierta: se aplaza a hold.
    rb = _rb(reversal=0.0, min_hold=300)
    snap = _snapshot(equity=10000, symbols={"BTCUSD": _sym(
        net_direction="LONG", net_exposure_pct=0.3, open_positions=1,
        floating_pnl=-10.0, newest_position_age=45)})
    out = rb.clamp([_decision(approve=False, action="close")], snap)
    assert out[0]["position_action"] == "hold"
    assert out[0].get("manage_direction") is None
    assert "aplazado" in out[0]["clamp"]


def test_clamp_hard_stop_rompe_la_gracia():
    # El hard-stop catastrófico se impone incluso dentro del período de gracia.
    rb = _rb(reversal=0.0, symbol_loss=0.03, min_hold=300)
    snap = _snapshot(equity=10000, symbols={"BTCUSD": _sym(
        net_direction="LONG", net_exposure_pct=0.3, open_positions=2,
        floating_pnl=-350.0, newest_position_age=10)})
    signals = {"BTCUSD": {"symbol": "BTCUSD", "action": "HOLD", "trend": "bullish"}}
    out = rb.clamp([_decision(approve=False, action="hold")], snap, signals)
    assert out[0]["position_action"] == "close"
    assert "hard-stop" in out[0]["clamp"]


def test_clamp_reversion_actua_pasada_la_gracia():
    # Posición ya madura (600s > 300s): la reversión vuelve a actuar.
    rb = _rb(reversal=0.015, min_hold=300)
    snap = _snapshot(equity=10000, symbols={"BTCUSD": _sym(
        net_direction="LONG", net_exposure_pct=0.3, open_positions=3,
        floating_pnl=-200.0, newest_position_age=600)})
    signals = {"BTCUSD": {"symbol": "BTCUSD", "action": "HOLD", "trend": "bearish"}}
    out = rb.clamp([_decision(approve=False, action="hold")], snap, signals)
    assert out[0]["position_action"] == "reduce"
    assert out[0]["manage_direction"] == "BUY"


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


def test_snapshot_registra_edad_de_posiciones():
    # La mesa registra cuándo vio cada ticket por primera vez; en el primer
    # snapshot la edad es ~0 (recién vista) y poda los tickets que desaparecen.
    rb = _rb(min_hold=300)
    positions = [
        {"symbol": "BTCUSD", "ticket": 111, "direction": "BUY", "volume": 0.1,
         "current_price": 50000, "profit": 0.0},
    ]
    client = _FakeClient(_account(), positions)
    snap = rb.snapshot(client, [_FakeAgent("BTCUSD")])
    age = snap["symbols"]["BTCUSD"]["newest_position_age"]
    assert age is not None and age >= 0
    assert "111" in rb._first_seen
    # Si la posición desaparece, su edad se olvida.
    client2 = _FakeClient(_account(), [])
    rb.snapshot(client2, [_FakeAgent("BTCUSD")])
    assert "111" not in rb._first_seen


def test_snapshot_sin_posiciones_edad_none():
    rb = _rb()
    client = _FakeClient(_account(), [])
    snap = rb.snapshot(client, [_FakeAgent("BTCUSD")])
    assert snap["symbols"]["BTCUSD"]["newest_position_age"] is None
