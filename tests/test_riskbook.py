"""Tests del coordinador: topes duros del RiskBook (funciones puras) y el
parseo/fallback del CoordinatorAgent (sin llamadas reales al LLM)."""
from agents.coordinator import RiskBook, CoordinatorAgent


def _rb(can_close=True, max_total=0.5, max_symbol=0.4, max_net=0.6,
        max_pyramid=None, reversal=0.015, symbol_loss=0.0, min_hold=0.0,
        llm_can_close=True, max_open_positions=0) -> RiskBook:
    # min_hold por defecto 0 (gracia off) y llm_can_close=True (gestión
    # discrecional permitida) para no alterar los tests existentes; el default
    # real de producción es llm_can_close=False (solo fuerza mayor).
    # max_pyramid None => igual a max_net (sin piramidación extra), como el default.
    # max_open_positions 0 => sin tope de recuento (no altera los tests previos).
    cfg = {"max_total_exposure_pct": max_total,
           "max_symbol_allocation_pct": max_symbol,
           "can_close": can_close,
           "max_net_direction_pct": max_net,
           "reversal_drawdown_pct": reversal,
           "max_symbol_loss_pct": symbol_loss,
           "min_hold_seconds": min_hold,
           "llm_can_close": llm_can_close,
           "max_open_positions": max_open_positions}
    if max_pyramid is not None:
        cfg["max_pyramid_direction_pct"] = max_pyramid
    return RiskBook(cfg)


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


def _decision(symbol="BTCUSD", approve=True, alloc=0.2, action="hold", tp_rr=0.0,
              size_mult=0.0, max_spread=0.0):
    return {"symbol": symbol, "approve": approve, "priority": 1,
            "allocation_pct": alloc, "position_action": action, "tp_rr": tp_rr,
            "size_mult": size_mult, "max_spread": max_spread}


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


# ----- Tope de nº de posiciones por símbolo (gobernado por la mesa) -----

def test_clamp_veta_entrada_si_simbolo_en_su_maximo():
    # max_open_positions=3 y el símbolo ya tiene 3 abiertas: la entrada se veta.
    rb = _rb(max_open_positions=3)
    snap = _snapshot(equity=10000, symbols={"BTCUSD": _sym(
        remaining_pct=0.4, open_positions=3)})
    out = rb.clamp([_decision(alloc=0.2)], snap)
    assert out[0]["approve"] is False
    assert "máximo de posiciones" in out[0]["clamp"]


def test_clamp_permite_entrada_si_queda_hueco():
    # max_open_positions=3 con 2 abiertas: aún cabe una más.
    rb = _rb(max_open_positions=3)
    snap = _snapshot(equity=10000, symbols={"BTCUSD": _sym(
        remaining_pct=0.4, open_positions=2)})
    out = rb.clamp([_decision(alloc=0.2)], snap)
    assert out[0]["approve"] is True
    assert "máximo de posiciones" not in out[0]["clamp"]


def test_clamp_max_posiciones_off_no_veta():
    # max_open_positions=0 (sin tope): no veta por recuento aunque haya muchas.
    rb = _rb(max_open_positions=0)
    snap = _snapshot(equity=10000, symbols={"BTCUSD": _sym(
        remaining_pct=0.4, open_positions=9)})
    out = rb.clamp([_decision(alloc=0.2)], snap)
    assert out[0]["approve"] is True


def test_snapshot_expone_max_open_positions():
    rb = _rb(max_open_positions=5)
    client = _FakeClient(_account(), [])
    snap = rb.snapshot(client, [_FakeAgent("BTCUSD")])
    assert snap["max_open_positions"] == 5


# ----- R:R objetivo (tp_rr) gobernado por la mesa -----

def test_clamp_tp_rr_dentro_de_rango_no_se_toca():
    rb = _rb()  # tp_rr_min=1.0, tp_rr_max=4.0 por defecto
    snap = _snapshot(symbols={"BTCUSD": {"remaining_pct": 0.4}})
    out = rb.clamp([_decision(tp_rr=1.5)], snap)
    assert out[0]["tp_rr"] == 1.5
    assert "tp_rr" not in out[0]["clamp"]


def test_clamp_tp_rr_acota_por_arriba_y_por_abajo():
    rb = _rb()
    snap = _snapshot(symbols={"BTCUSD": {"remaining_pct": 0.4}})
    alto = rb.clamp([_decision(tp_rr=9.0)], snap)
    assert alto[0]["tp_rr"] == 4.0
    assert "tp_rr" in alto[0]["clamp"]
    bajo = rb.clamp([_decision(tp_rr=0.3)], snap)
    assert bajo[0]["tp_rr"] == 1.0
    assert "tp_rr" in bajo[0]["clamp"]


def test_clamp_tp_rr_ausente_queda_en_cero():
    rb = _rb()
    snap = _snapshot(symbols={"BTCUSD": {"remaining_pct": 0.4}})
    out = rb.clamp([_decision()], snap)  # tp_rr=0 (no ajustar)
    assert out[0]["tp_rr"] == 0.0


# ----- Multiplicador de lote (size_mult) gobernado por la mesa -----

def test_clamp_size_mult_dentro_de_rango_no_se_toca():
    rb = _rb()  # size_mult_min=0.5, size_mult_max=2.0 por defecto
    snap = _snapshot(symbols={"BTCUSD": {"remaining_pct": 0.4}})
    out = rb.clamp([_decision(size_mult=1.5)], snap)
    assert out[0]["size_mult"] == 1.5
    assert "size_mult" not in out[0]["clamp"]


def test_clamp_size_mult_acota_por_arriba_y_por_abajo():
    rb = _rb()
    snap = _snapshot(symbols={"BTCUSD": {"remaining_pct": 0.4}})
    alto = rb.clamp([_decision(size_mult=5.0)], snap)
    assert alto[0]["size_mult"] == 2.0
    assert "size_mult" in alto[0]["clamp"]
    bajo = rb.clamp([_decision(size_mult=0.1)], snap)
    assert bajo[0]["size_mult"] == 0.5
    assert "size_mult" in bajo[0]["clamp"]


def test_clamp_size_mult_ausente_queda_en_cero():
    rb = _rb()
    snap = _snapshot(symbols={"BTCUSD": {"remaining_pct": 0.4}})
    out = rb.clamp([_decision()], snap)  # size_mult=0 (lote base ×1)
    assert out[0]["size_mult"] == 0.0


# ----- Filtro de spread (max_spread) gobernado por la mesa -----

def test_clamp_max_spread_se_propaga_y_anota():
    # La mesa afloja el filtro (baseline 50 -> 80): se propaga y deja nota.
    rb = _rb()
    snap = _snapshot(symbols={"BTCUSD": {"remaining_pct": 0.4, "max_spread_filter": 50.0}})
    out = rb.clamp([_decision(max_spread=80.0)], snap)
    assert out[0]["max_spread"] == 80.0
    assert "max_spread afloja" in out[0]["clamp"]


def test_clamp_max_spread_aprieta_anota():
    rb = _rb()
    snap = _snapshot(symbols={"BTCUSD": {"remaining_pct": 0.4, "max_spread_filter": 50.0}})
    out = rb.clamp([_decision(max_spread=20.0)], snap)
    assert out[0]["max_spread"] == 20.0
    assert "max_spread aprieta" in out[0]["clamp"]


def test_clamp_max_spread_negativo_se_recorta_a_cero():
    rb = _rb()
    snap = _snapshot(symbols={"BTCUSD": {"remaining_pct": 0.4}})
    out = rb.clamp([_decision(max_spread=-5.0)], snap)
    assert out[0]["max_spread"] == 0.0


def test_clamp_max_spread_ausente_queda_en_cero():
    rb = _rb()
    snap = _snapshot(symbols={"BTCUSD": {"remaining_pct": 0.4}})
    out = rb.clamp([_decision()], snap)  # max_spread=0 (baseline)
    assert out[0]["max_spread"] == 0.0


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


def test_set_model_cambia_provider_y_modelo_en_caliente():
    c = _coord()
    engine_antes = c.engine
    temp_antes = c.engine.temperature
    result = c.set_model("openai", "gpt-4o-mini")
    assert result == {"provider": "openai", "model": "gpt-4o-mini"}
    assert c.provider == "openai"
    assert c.model == "gpt-4o-mini"
    # Reconstruye el motor (provider en el engine, modelo en su BotConfig) y
    # preserva la temperatura.
    assert c.engine is not engine_antes
    assert c.engine.provider == "openai"
    assert c.engine.config.model == "gpt-4o-mini"
    assert c.engine.temperature == temp_antes


def test_set_model_normaliza_y_exige_datos():
    c = _coord()
    # Normaliza mayúsculas/espacios.
    c.set_model("  OpenAI ", "  gpt-4o ")
    assert c.provider == "openai"
    assert c.model == "gpt-4o"
    # provider/model vacíos => error.
    import pytest
    with pytest.raises(ValueError):
        c.set_model("", "gpt-4o")
    with pytest.raises(ValueError):
        c.set_model("openai", "  ")


def test_parse_extrae_size_mult():
    c = _coord()
    raw = ('{"rationale": "ok", "decisions": [{"symbol": "BTCUSD", "approve": true, '
           '"allocation_pct": 0.2, "size_mult": 1.5}]}')
    _, decisions = c._parse(raw)
    assert decisions[0]["size_mult"] == 1.5


def test_parse_size_mult_ausente_es_cero():
    c = _coord()
    raw = ('{"rationale": "ok", "decisions": [{"symbol": "BTCUSD", "approve": true, '
           '"allocation_pct": 0.2}]}')
    _, decisions = c._parse(raw)
    assert decisions[0]["size_mult"] == 0.0


def test_parse_extrae_max_spread():
    c = _coord()
    raw = ('{"rationale": "ok", "decisions": [{"symbol": "BTCUSD", "approve": true, '
           '"allocation_pct": 0.2, "max_spread": 65}]}')
    _, decisions = c._parse(raw)
    assert decisions[0]["max_spread"] == 65.0


def test_parse_max_spread_ausente_es_cero():
    c = _coord()
    raw = ('{"rationale": "ok", "decisions": [{"symbol": "BTCUSD", "approve": true, '
           '"allocation_pct": 0.2}]}')
    _, decisions = c._parse(raw)
    assert decisions[0]["max_spread"] == 0.0


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


def test_clamp_permite_piramidar_ganador_con_tendencia_a_favor():
    # Neto LONG saturado (0.65 >= max_net 0.6) PERO en ganancia y la tendencia del
    # especialista confirma LONG: se tolera apilar hasta max_pyramid (1.2).
    rb = _rb(max_net=0.6, max_pyramid=1.2)
    snap = _snapshot(equity=10000, symbols={"BTCUSD": _sym(
        net_direction="LONG", net_exposure_pct=0.65, open_positions=2,
        floating_pnl=300.0)})
    signals = {"BTCUSD": {"symbol": "BTCUSD", "action": "BUY", "trend": "bullish"}}
    out = rb.clamp([_decision(alloc=0.2)], snap, signals)
    assert out[0]["approve"] is True
    assert "piramidando ganador" in out[0]["clamp"]


def test_clamp_no_piramida_perdedor_aunque_tendencia_a_favor():
    # Mismo escenario pero en PÉRDIDA flotante: no se piramida, sigue vetado.
    rb = _rb(max_net=0.6, max_pyramid=1.2)
    snap = _snapshot(equity=10000, symbols={"BTCUSD": _sym(
        net_direction="LONG", net_exposure_pct=0.65, open_positions=2,
        floating_pnl=-100.0)})
    signals = {"BTCUSD": {"symbol": "BTCUSD", "action": "BUY", "trend": "bullish"}}
    out = rb.clamp([_decision(alloc=0.2)], snap, signals)
    assert out[0]["approve"] is False
    assert "no apilar" in out[0]["clamp"]


def test_clamp_no_piramida_por_encima_del_tope_pyramid():
    # En ganancia y a favor, pero el neto ya supera max_pyramid: vetado.
    rb = _rb(max_net=0.6, max_pyramid=0.8)
    snap = _snapshot(equity=10000, symbols={"BTCUSD": _sym(
        net_direction="LONG", net_exposure_pct=0.85, open_positions=3,
        floating_pnl=300.0)})
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


# ----- Coherencia entre símbolos correlacionados (BTC/ETH) -----

def _sig(action, confidence=0.6, trend=None):
    return {"action": action, "confidence": confidence,
            "trend": trend or ("bullish" if action == "BUY" else "bearish")}


def test_clamp_veta_pata_opuesta_en_par_correlacionado():
    # Grupo plano (sin posiciones): BTC BUY conf 0.8 vs ETH SELL conf 0.6.
    # Gana la de mayor confianza (BTC BUY); la ETH SELL opuesta se veta.
    rb = _rb(max_net=0.6)
    snap = _snapshot(equity=10000, symbols={
        "BTCUSD": _sym(remaining_pct=0.4), "ETHUSD": _sym(remaining_pct=0.4)})
    signals = {"BTCUSD": _sig("BUY", 0.8), "ETHUSD": _sig("SELL", 0.6)}
    out = rb.clamp([_decision("BTCUSD"), _decision("ETHUSD")], snap, signals)
    by = {d["symbol"]: d for d in out}
    assert by["BTCUSD"]["approve"] is True
    assert by["ETHUSD"]["approve"] is False
    assert "grupo correlacionado" in by["ETHUSD"]["clamp"]


def test_clamp_permite_par_correlacionado_misma_direccion():
    # Ambos en la misma dirección (BUY): no hay conflicto, se aprueban los dos.
    rb = _rb(max_net=0.6)
    snap = _snapshot(equity=10000, symbols={
        "BTCUSD": _sym(remaining_pct=0.4), "ETHUSD": _sym(remaining_pct=0.4)})
    signals = {"BTCUSD": _sig("BUY", 0.7), "ETHUSD": _sig("BUY", 0.6)}
    out = rb.clamp([_decision("BTCUSD"), _decision("ETHUSD")], snap, signals)
    assert all(d["approve"] for d in out)
    assert all("grupo correlacionado" not in d["clamp"] for d in out)


def test_clamp_par_correlacionado_respeta_libro_abierto():
    # BTC ya tiene neto LONG abierto; ETH propone una entrada SELL. La dirección
    # dominante del grupo la fija el libro abierto (LONG), así que la SELL se veta
    # aunque su confianza sea alta.
    rb = _rb(max_net=0.6)
    snap = _snapshot(equity=10000, symbols={
        "BTCUSD": _sym(net_direction="LONG", net_exposure_pct=0.3, open_positions=2),
        "ETHUSD": _sym(remaining_pct=0.4)})
    signals = {"BTCUSD": _sig("HOLD"), "ETHUSD": _sig("SELL", 0.9)}
    out = rb.clamp([_decision("BTCUSD", approve=False),
                    _decision("ETHUSD")], snap, signals)
    by = {d["symbol"]: d for d in out}
    assert by["ETHUSD"]["approve"] is False
    assert "grupo correlacionado" in by["ETHUSD"]["clamp"]


def test_clamp_par_correlacionado_consenso_de_reversion():
    # CONSENSO: BTC tiene neto LONG abierto, pero AMBOS especialistas giran a SELL
    # a la vez. El grupo flipa de forma coherente -> se aprueban las dos entradas
    # SELL (no es pairs trade: ambas patas nuevas van al mismo lado). Antes la
    # exposición abierta LONG vetaba los dos cortos (oportunidad perdida).
    rb = _rb(max_net=0.6)
    snap = _snapshot(equity=10000, symbols={
        "BTCUSD": _sym(net_direction="LONG", net_exposure_pct=0.3, open_positions=2),
        "ETHUSD": _sym(remaining_pct=0.4)})
    signals = {"BTCUSD": _sig("SELL", 0.7), "ETHUSD": _sig("SELL", 0.6)}
    out = rb.clamp([_decision("BTCUSD"), _decision("ETHUSD")], snap, signals)
    assert all(d["approve"] for d in out)
    assert all("grupo correlacionado" not in d["clamp"] for d in out)


def test_clamp_par_correlacionado_no_afecta_a_simbolo_solo():
    # Con un único símbolo del grupo presente, la guardia no hace nada.
    rb = _rb(max_net=0.6)
    snap = _snapshot(equity=10000, symbols={"BTCUSD": _sym(remaining_pct=0.4)})
    signals = {"BTCUSD": _sig("BUY", 0.8)}
    out = rb.clamp([_decision("BTCUSD")], snap, signals)
    assert out[0]["approve"] is True
    assert "grupo correlacionado" not in out[0]["clamp"]


# ----- RiskBook.snapshot: cálculo del sesgo neto (con cliente mock) -----

class _FakeInfo:
    trade_contract_size = 1.0
    point = 1.0


class _FakeTick:
    def __init__(self, ask, bid):
        self.ask = ask
        self.bid = bid


class _FakeClient:
    def __init__(self, account, positions, tick=None):
        self._account = account
        self._positions = positions
        self._tick = tick

    def get_account_info(self):
        return self._account

    def get_positions(self, symbol=None):
        if symbol is None:
            return self._positions
        return [p for p in self._positions if p.get("symbol") == symbol]

    def get_symbol_info(self, symbol):
        return _FakeInfo()

    def get_tick(self, symbol):
        return self._tick


class _FakeConfig:
    def __init__(self, max_spread_filter=0.0):
        self.max_spread_filter = max_spread_filter


class _FakeAgent:
    def __init__(self, symbol, max_spread_filter=0.0):
        self.symbol = symbol
        self.config = _FakeConfig(max_spread_filter)


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


def test_snapshot_desglosa_pnl_por_lado():
    # Libro cubierto: los largos pierden y el corto gana. El P/L por lado revela lo
    # que el neto (suma) oculta.
    rb = _rb()
    positions = [
        {"symbol": "BTCUSD", "direction": "BUY", "volume": 0.2, "current_price": 50000, "profit": -40.0},
        {"symbol": "BTCUSD", "direction": "BUY", "volume": 0.1, "current_price": 50000, "profit": -10.0},
        {"symbol": "BTCUSD", "direction": "SELL", "volume": 0.1, "current_price": 50000, "profit": 52.0},
    ]
    client = _FakeClient(_account(hedging=True), positions)
    snap = rb.snapshot(client, [_FakeAgent("BTCUSD")])
    s = snap["symbols"]["BTCUSD"]
    assert s["long_pnl"] == -50.0
    assert s["short_pnl"] == 52.0
    assert s["floating_pnl"] == 2.0


def test_snapshot_expone_rango_size_mult():
    rb = RiskBook({"size_mult_min": 0.5, "size_mult_max": 2.0})
    client = _FakeClient(_account(), [])
    snap = rb.snapshot(client, [_FakeAgent("BTCUSD")])
    assert snap["size_mult_min"] == 0.5
    assert snap["size_mult_max"] == 2.0


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
    # snapshot la edad es ~0 (recién vista) y poda los tickets que ya no aparecen
    # en una lectura FIABLE (no vacía) posterior.
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
    # La posición 111 se cierra y abre otra (222): una lectura NO vacía que ya no
    # incluye 111 sí poda su edad (el cierre es real, no un fallo de lectura).
    client2 = _FakeClient(_account(), [
        {"symbol": "BTCUSD", "ticket": 222, "direction": "BUY", "volume": 0.1,
         "current_price": 50000, "profit": 0.0}])
    rb.snapshot(client2, [_FakeAgent("BTCUSD")])
    assert "111" not in rb._first_seen
    assert "222" in rb._first_seen


def test_lectura_vacia_espuria_no_borra_la_edad():
    # Regresión: get_positions() devuelve [] tanto si NO hay posiciones como si la
    # lectura FALLA (bridge no listo al arrancar). Una lista vacía NO debe arrasar
    # el registro de antigüedad, o el período de gracia se reiniciaría en cada
    # arranque (snapshot de _startup_review antes de que el bridge esté caliente).
    rb = _rb(min_hold=300)
    positions = [
        {"symbol": "BTCUSD", "ticket": 111, "direction": "BUY", "volume": 0.1,
         "current_price": 50000, "profit": 0.0},
    ]
    rb.snapshot(_FakeClient(_account(), positions), [_FakeAgent("BTCUSD")])
    rb._first_seen["111"] -= 400  # antedata: ya pasó la gracia
    # Lectura vacía espuria (bridge no listo / error): no se poda 111.
    rb.snapshot(_FakeClient(_account(), []), [_FakeAgent("BTCUSD")])
    assert "111" in rb._first_seen
    # Al reaparecer la posición, conserva su edad (gracia ya cumplida, no reinicia).
    snap = rb.snapshot(_FakeClient(_account(), positions), [_FakeAgent("BTCUSD")])
    assert snap["symbols"]["BTCUSD"]["newest_position_age"] >= 400


def test_snapshot_sin_posiciones_edad_none():
    rb = _rb()
    client = _FakeClient(_account(), [])
    snap = rb.snapshot(client, [_FakeAgent("BTCUSD")])
    assert snap["symbols"]["BTCUSD"]["newest_position_age"] is None


def test_snapshot_expone_spread_baseline_y_actual():
    # El snapshot lleva el filtro de spread del especialista (baseline) y el spread
    # actual (ask-bid)/point, para que el director razone un override max_spread.
    rb = _rb()
    client = _FakeClient(_account(), [], tick=_FakeTick(ask=50005.0, bid=50000.0))
    snap = rb.snapshot(client, [_FakeAgent("BTCUSD", max_spread_filter=50.0)])
    s = snap["symbols"]["BTCUSD"]
    assert s["max_spread_filter"] == 50.0
    assert s["current_spread"] == 5.0  # (50005 - 50000) / point(1.0)


def test_snapshot_spread_actual_none_sin_tick():
    # Sin tick disponible, current_spread es None (fail-safe), sin romper el snapshot.
    rb = _rb()
    client = _FakeClient(_account(), [], tick=None)
    snap = rb.snapshot(client, [_FakeAgent("BTCUSD", max_spread_filter=8.0)])
    s = snap["symbols"]["BTCUSD"]
    assert s["max_spread_filter"] == 8.0
    assert s["current_spread"] is None


# ----- Persistencia del período de gracia (sobrevive a reinicios) -----

def test_first_seen_persiste_y_sobrevive_a_reinicio():
    # persist_first_seen=True: el registro se guarda en la DB (tabla
    # risk_first_seen) y se recarga al "reiniciar" (nueva instancia).
    cfg = {"min_hold_seconds": 300, "persist_first_seen": True}
    positions = [
        {"symbol": "BTCUSD", "ticket": 111, "direction": "BUY", "volume": 0.1,
         "current_price": 50000, "profit": 0.0},
    ]
    client = _FakeClient(_account(), positions)

    # Primer arranque: registra el ticket y lo vuelca a la DB.
    rb1 = RiskBook(cfg)
    rb1.snapshot(client, [_FakeAgent("BTCUSD")])
    assert "111" in rb1._first_seen
    # Antedata el avistamiento 100s para simular tiempo transcurrido.
    rb1._first_seen["111"] -= 100
    rb1._save_first_seen()

    # "Reinicio": una instancia nueva recarga el registro de la DB.
    rb2 = RiskBook(cfg)
    assert "111" in rb2._first_seen  # NO se reinicia a cero
    snap = rb2.snapshot(client, [_FakeAgent("BTCUSD")])
    age = snap["symbols"]["BTCUSD"]["newest_position_age"]
    assert age >= 100  # la edad se conserva tras el reinicio


def test_first_seen_sin_persistencia_solo_en_memoria():
    # Sin persist_first_seen el registro es solo en memoria (no toca la DB).
    rb = RiskBook({"min_hold_seconds": 300})
    client = _FakeClient(_account(), [
        {"symbol": "BTCUSD", "ticket": 222, "direction": "BUY", "volume": 0.1,
         "current_price": 50000, "profit": 0.0}])
    rb.snapshot(client, [_FakeAgent("BTCUSD")])
    assert "222" in rb._first_seen
    # Una instancia nueva sin persistencia arranca vacía.
    assert RiskBook({"min_hold_seconds": 300})._first_seen == {}
