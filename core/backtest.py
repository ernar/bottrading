"""Harness de backtest: replaya velas históricas a través del MISMO path de señal
de producción (vía ``ReplayClient``) y mide el rendimiento — para validar una
estrategia ANTES de arriesgar dinero, y para comparar motores (LLM vs base
determinista) sobre el mismo periodo.

Dos motores de señal listos:
- ``make_baseline_signal_fn``: DETERMINISTA, solo ``core.indicators.trend_state``
  (sin LLM). Es la vara de medir: si el LLM no lo supera neto de costes, el LLM no
  aporta ventaja.
- ``make_llm_signal_fn(agent)``: usa el path real (``build_market_context`` +
  ``StrategyEngine.generate_signal`` + ``_fill_sl_tp`` del agente). Sin red de
  noticias para que el backtest sea reproducible.

Uso CLI:
    python -m core.backtest --symbol BTCUSD --bars 1000 --balance 1000 \
        --spread 50 --commission 7 --engines baseline
    python -m core.backtest --candles datos.json --engines baseline,btc-agent

Las velas se cargan del bróker vivo (MT4) o de un JSON ({symbol, candles:[...]}).
"""
from typing import Callable, List, Optional

from clients.replay_client import ReplayClient
from core import indicators as ta
from core.performance import _metrics_from_pnls


SignalFn = Callable[[object, str], Optional[dict]]


# ----- Motores de señal -----

def make_baseline_signal_fn(atr_sl_mult: float = 1.5, atr_tp_mult: float = 3.0,
                            lot: float = 0.01, confidence: float = 0.99,
                            min_score: int = 2, require_break: bool = False) -> SignalFn:
    """Señal DETERMINISTA desde ``trend_state``: BUY si alcista, SELL si bajista,
    nada en lateral. SL/TP por múltiplos de ATR. Es la base de comparación.

    ``confidence`` alta (0.99) y R:R ~2 (sl 1.5×ATR / tp 3×ATR) a propósito: la base
    no tiene "confianza" calibrable como el LLM, así que debe SUPERAR los umbrales
    (min_confidence/min_rr) que el optimizador haya subido en los agentes; si no, el
    ``validate_trade`` del especialista la rechazaría y mediríamos 0 operaciones.

    Selectividad de régimen (para NO operar el chop, que es donde el seguidor de
    tendencia se deja barrer a stops): ``min_score`` exige una mayoría más amplia del
    voto de ``trend_state`` (|score| >= min_score; 2 = comportamiento original, 4-5 =
    solo tendencias fuertes); ``require_break`` exige además ruptura de estructura
    confirmada en el sentido de la entrada.

    Reusa ``core.signals.deterministic_signal`` (la MISMA lógica que opera el agente en
    vivo en modo determinista), para que backtest y real midan/operen lo mismo."""
    from core.signals import deterministic_signal

    def fn(client, symbol):
        return deterministic_signal(
            client, symbol, timeframe="H1", atr_sl_mult=atr_sl_mult,
            atr_tp_mult=atr_tp_mult, min_score=min_score, require_break=require_break,
            confidence=confidence, lot=lot)
    return fn


def make_llm_signal_fn(agent, lot: float = None) -> SignalFn:
    """Señal del path REAL del especialista (sin noticias, para reproducibilidad).
    Reusa ``build_market_context`` + ``agent.strategy.generate_signal`` +
    ``agent._fill_sl_tp``. El volumen sale del agente salvo override ``lot``."""
    from core.market_context import build_market_context

    def fn(client, symbol):
        tick = client.get_tick(symbol)
        if not tick:
            return None
        positions = client.get_positions(symbol)
        md = build_market_context(client, symbol, positions=positions,
                                  memory_summary="", news_context="")
        sig = agent.strategy.generate_signal(symbol, market_data=md)
        if not sig:
            return None
        agent._fill_sl_tp(client, sig, tick)
        if lot is not None:
            sig["volume"] = lot
        return sig
    return fn


# ----- Motor del backtest -----

def run_backtest(client: ReplayClient, symbol: str, signal_fn: SignalFn, *,
                 warmup: int = 120, max_positions: int = 1) -> dict:
    """Recorre las velas: en cada barra pide una señal (con info HASTA esa barra),
    abre si procede, avanza y liquida SL/TP en la barra siguiente. Devuelve un
    resumen con métricas, curva de equity y drawdown."""
    if not client._candles:
        return _summarize(client, [client.equity()], 0)
    client.set_cursor(min(warmup, len(client._candles) - 1))
    equity_curve = [client.equity()]

    bars = 0
    while not client.at_end:
        if len(client.get_positions(symbol)) < max_positions:
            sig = signal_fn(client, symbol)
            if sig and sig.get("action") in ("BUY", "SELL") and sig.get("stop_loss"):
                client.place_order(symbol, sig.get("volume") or 0.01, sig["action"],
                                   stop_loss=sig.get("stop_loss"),
                                   take_profit=sig.get("take_profit"))
        client.step()
        equity_curve.append(client.equity())
        bars += 1

    # Cierra lo que quede abierto al final del periodo (a precio de la última vela).
    for p in list(client.get_positions(symbol)):
        client.close_position(symbol, ticket=p["ticket"])
    equity_curve[-1] = client.equity()
    return _summarize(client, equity_curve, bars)


def _max_drawdown(curve: List[float]) -> float:
    peak, mdd = float("-inf"), 0.0
    for v in curve:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak)
    return round(mdd, 4)


def _summarize(client: ReplayClient, equity_curve: List[float], bars: int) -> dict:
    closed = client.closed_trades
    pnls = [t["pnl"] for t in closed]
    comms = [t["commission"] for t in closed]
    metrics = _metrics_from_pnls(pnls, comms)
    reasons: dict = {}
    for t in closed:
        reasons[t["close_reason"]] = reasons.get(t["close_reason"], 0) + 1
    start = client.starting_balance
    end = client.balance
    return {
        "metrics": metrics,
        "starting_balance": round(start, 2),
        "ending_balance": round(end, 2),
        "ending_equity": client.equity(),
        "return_pct": round((end - start) / start, 4) if start else None,
        "max_drawdown": _max_drawdown(equity_curve),
        "bars": bars,
        "trades": len(closed),
        "by_reason": reasons,
    }


# ----- Modo COORDINADO (la mesa en el bucle) -----

# La mesa determinista (sin LLM) vive en agents/coordinator.py para reusarla TAMBIÉN
# en vivo (main.py). Se re-exporta aquí por compatibilidad con el backtest y sus tests.
from agents.coordinator import DeterministicCoordinator  # noqa: E402


def run_coordinated_backtest(client, agents, coordinator, risk_book, signal_fns, *,
                             warmup: int = 120, manage_lifecycle: bool = True,
                             quiet: bool = True) -> dict:
    """Backtest del PIPELINE COORDINADO real: por cada barra recolecta las señales
    de los especialistas (+ momento determinista, como en producción), las pasa por
    la mesa (``RiskBook.snapshot`` → ``coordinator.decide`` → ``clamp``) y ejecuta
    sus decisiones REUTILIZANDO el ejecutor real del orquestador
    (``_execute_decisions`` → ``_open_from_signal``/``_manage_open_positions``) contra
    el simulador. Así mide el bot tal y como opera (con validate, tp_rr, size_mult,
    notional, guardias…), no solo la señal en bruto.

    ``signal_fns``: dict {symbol: fn} o un único ``fn(client, symbol)``. ``coordinator``
    puede ser el real (LLM, lento/caro: una llamada por barra) o ``DeterministicCoordinator``
    (gratis). Requiere un cliente MULTI-símbolo (``MultiSymbolReplayClient``) para que
    la mesa vea la cartera entera."""
    import contextlib
    from agents.orchestrator import AgentOrchestrator
    from core.market_context import momentum_snapshot

    if callable(signal_fns):
        _single = signal_fns
        signal_fns = {a.symbol: _single for a in agents}

    orch = AgentOrchestrator(agents, client, platform="replay",
                             coordinator=coordinator, risk_book=risk_book)

    n = getattr(client, "_len", None)
    if n is None:
        n = len(getattr(client, "_candles", []))
    if not n:
        return _summarize(client, [client.equity()], 0)
    client.set_cursor(min(warmup, n - 1))
    equity_curve = [client.equity()]
    bars = 0
    diag_signals = 0      # señales accionables generadas por los especialistas
    diag_approved = 0     # entradas accionables aprobadas por la mesa

    class _Null:
        def write(self, *_a):
            pass

        def flush(self):
            pass

    @contextlib.contextmanager
    def _quiet_stdout():
        if not quiet:
            yield
            return
        with contextlib.redirect_stdout(_Null()):
            yield

    with _quiet_stdout():
        while not client.at_end:
            signals = {}
            for agent in agents:
                if not getattr(agent, "enabled", True):
                    continue
                fn = signal_fns.get(agent.symbol)
                if not fn:
                    continue
                sig = fn(client, agent.symbol)
                if not sig:
                    continue
                ts = momentum_snapshot(client, agent.symbol)
                if ts:
                    sig["momentum"] = ts.get("direction")
                    if ts.get("reversal"):
                        sig["reversal"] = ts["reversal"]
                sig.setdefault("symbol", agent.symbol)
                signals[agent.symbol] = sig
                if sig.get("action") in ("BUY", "SELL"):
                    diag_signals += 1

            if manage_lifecycle:
                orch._manage_position_lifecycle()

            snapshot = risk_book.snapshot(client, agents, in_cooldown=False)
            has_positions = snapshot.get("open_positions_total", 0) > 0
            if signals or (has_positions and risk_book.can_close):
                result = coordinator.decide(snapshot, signals, {"agents": []}, news_context="")
                for d in result.get("decisions", []):
                    sg = signals.get(d.get("symbol")) or {}
                    if d.get("approve") and sg.get("action") in ("BUY", "SELL"):
                        diag_approved += 1
                orch._execute_decisions(result, signals)

            client.step()
            equity_curve.append(client.equity())
            bars += 1

        for agent in agents:
            for p in list(client.get_positions(agent.symbol)):
                client.close_position(agent.symbol, ticket=p["ticket"])
    equity_curve[-1] = client.equity()
    res = _summarize(client, equity_curve, bars)
    res["diagnostics"] = {
        "candles": n, "bars": bars,
        "actionable_signals": diag_signals,
        "approved_by_mesa": diag_approved,
        "orders_placed": client._next_ticket - 1,
    }
    return res


# ----- Carga de histórico + un experimento (para el runner de barridos) -----

def load_history(path: str):
    """Carga un JSON de histórico y devuelve ``(series, infos)``.

    Acepta el formato multi (``{"series": {...}, "infos": {...}}``, de
    ``scripts/dump_history.py``) o el single (``{"symbol", "candles"}``)."""
    import json
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if "series" in data:
        return data["series"], (data.get("infos") or {})
    sym = data.get("symbol", "SYM")
    return {sym: data["candles"]}, {}


def _apply_overrides(agent, overrides: dict):
    """Sobreescribe params del agente (min_confidence/min_rr/atr_*) y los aplica en
    caliente (apply_params re-sincroniza el StrategyEngine). Override determinista,
    independiente del tuning persistido en .env, para que el barrido sea limpio."""
    upd = {k: v for k, v in (overrides or {}).items() if v is not None}
    if not upd:
        return
    try:
        new = agent.params.model_copy(update=upd)   # pydantic v2
    except AttributeError:
        new = agent.params.copy(update=upd)          # pydantic v1
    agent.apply_params(new)


def run_one(series: dict, infos: dict, engines: list, *, signal: str = "baseline",
            coord: str = "deterministic", overrides: dict = None,
            warmup: int = 120, balance: float = 1000.0) -> dict:
    """Corre UN backtest coordinado para una configuración concreta (conjunto de
    agentes + motor de señal + mesa + overrides de params) y devuelve su resumen.
    Requiere que la DB esté inicializada (el ejecutor real escribe en ella)."""
    from agents.registry import build_agent
    from agents.coordinator import RiskBook, CoordinatorAgent
    from core.config import get_coordinator_config
    from clients.replay_client import MultiSymbolReplayClient

    agents = [build_agent(n) for n in engines]
    for a in agents:
        _apply_overrides(a, overrides)

    syms = [a.symbol for a in agents]
    missing = [s for s in syms if s not in series]
    sub_series = {s: series[s] for s in syms if s in series}
    if missing or not sub_series:
        return {"ok": False, "error": f"faltan series para {missing or syms}"}
    sub_infos = {s: infos.get(s, {}) for s in sub_series}
    client = MultiSymbolReplayClient(sub_series, sub_infos, starting_balance=balance)

    risk_book = RiskBook(get_coordinator_config())
    if coord == "llm":
        cfg = get_coordinator_config()
        coordinator = CoordinatorAgent(provider=cfg.get("provider") or "gemini",
                                       model=cfg.get("model") or "gemini-2.0-flash",
                                       risk_book=risk_book, temperature=cfg["temperature"])
    else:
        coordinator = DeterministicCoordinator(risk_book)

    if signal == "llm":
        signal_fns = {a.symbol: make_llm_signal_fn(a) for a in agents}
    else:
        ov = overrides or {}
        signal_fns = {a.symbol: make_baseline_signal_fn(
            atr_sl_mult=ov.get("atr_sl_mult", 1.5) or 1.5,
            atr_tp_mult=ov.get("atr_tp_mult", 3.0) or 3.0) for a in agents}

    res = run_coordinated_backtest(client, agents, coordinator, risk_book, signal_fns,
                                   warmup=warmup, quiet=True)
    res["ok"] = True
    return res


# ----- Carga de datos + CLI (runner A/B) -----

def _load_candles_from_client(client, symbol: str, bars: int) -> List[dict]:
    return client.get_ohlcv(symbol, "H1", bars)


def _replay_from(symbol: str, candles: List[dict], sym_info, *, balance: float,
                 spread: float, commission: float) -> ReplayClient:
    """Construye un ReplayClient con la info de símbolo del bróker (o defaults)."""
    g = lambda a, d: getattr(sym_info, a, d) if sym_info else d
    return ReplayClient(
        symbol, candles,
        point=g("point", 0.01), digits=int(g("digits", 2)),
        contract_size=float(g("trade_contract_size", g("contract_size", 1.0)) or 1.0),
        spread_points=spread, commission_per_lot=commission,
        tick_value=float(g("trade_tick_value", 1.0) or 1.0),
        volume_min=float(g("volume_min", 0.01) or 0.01),
        volume_step=float(g("volume_step", 0.01) or 0.01),
        starting_balance=balance)


def print_comparison(results: dict):
    """Tabla comparativa motor↔métricas (neto de costes)."""
    print(f"\n{'motor':<16}{'trades':>7}{'win':>7}{'PF':>7}{'expect.':>9}"
          f"{'P/L':>10}{'ret%':>8}{'maxDD':>8}")
    print("-" * 72)
    for name, r in results.items():
        m = r["metrics"]
        def f(v, p=""):
            return (f"{v:{p}}" if v is not None else "n/a")
        win = f"{m['win_rate']*100:.0f}%" if m["win_rate"] is not None else "n/a"
        ret = f"{r['return_pct']*100:.1f}%" if r["return_pct"] is not None else "n/a"
        dd = f"{r['max_drawdown']*100:.1f}%"
        print(f"{name:<16}{r['trades']:>7}{win:>7}{f(m['profit_factor']):>7}"
              f"{f(m['expectancy']):>9}{f(m['total_pnl']):>10}{ret:>8}{dd:>8}")


def _info_dict(sym_info, spread, commission) -> dict:
    g = lambda a, d: getattr(sym_info, a, d) if sym_info else d
    return {
        "point": g("point", 0.01), "digits": int(g("digits", 2)),
        "contract_size": float(g("trade_contract_size", g("contract_size", 1.0)) or 1.0),
        "spread_points": spread, "commission_per_lot": commission,
        "tick_value": float(g("trade_tick_value", 1.0) or 1.0),
        "volume_min": float(g("volume_min", 0.01) or 0.01),
        "volume_step": float(g("volume_step", 0.01) or 0.01),
    }


def main(argv=None):
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Backtest de señal o del pipeline coordinado.")
    ap.add_argument("--mode", choices=["signal", "coordinated"], default="signal",
                    help="signal: motor de señal aislado · coordinated: la mesa en el bucle")
    ap.add_argument("--symbol", default="BTCUSD")
    ap.add_argument("--symbols", default="", help="coordinated: símbolos coma (si difieren de los agentes)")
    ap.add_argument("--bars", type=int, default=1000)
    ap.add_argument("--balance", type=float, default=1000.0)
    ap.add_argument("--spread", type=float, default=0.0, help="spread en puntos")
    ap.add_argument("--commission", type=float, default=0.0, help="comisión por lote (ida+vuelta)")
    ap.add_argument("--warmup", type=int, default=120)
    ap.add_argument("--engines", default="baseline",
                    help="signal: 'baseline' y/o nombres de agentes · coordinated: agentes de la mesa")
    ap.add_argument("--signal", choices=["baseline", "llm"], default="baseline",
                    help="coordinated: fuente de señal de cada especialista")
    ap.add_argument("--coord", choices=["deterministic", "llm"], default="deterministic",
                    help="coordinated: mesa determinista (gratis) o LLM real")
    ap.add_argument("--candles", default="",
                    help="JSON: {symbol,candles} o {series:{sym:[...]}} en vez del bróker")
    args = ap.parse_args(argv)

    if args.mode == "coordinated":
        return _main_coordinated(args)

    # --- Modo señal (motor aislado) ---
    sym_info = None
    if args.candles:
        with open(args.candles, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        symbol = data.get("symbol", args.symbol)
        candles = data["candles"]
    else:
        from clients.mt4_client import MT4Client
        mt = MT4Client()
        if not mt.connect():
            print("No se pudo conectar al bridge MT4 (¿EA adjunto?).")
            return 1
        symbol = args.symbol
        candles = _load_candles_from_client(mt, symbol, args.bars)
        sym_info = mt.get_symbol_info(symbol)
    if not candles:
        print(f"Sin velas para {symbol}.")
        return 1

    results = {}
    for engine in [e.strip() for e in args.engines.split(",") if e.strip()]:
        client = _replay_from(symbol, candles, sym_info, balance=args.balance,
                              spread=args.spread, commission=args.commission)
        if engine == "baseline":
            fn = make_baseline_signal_fn()
        else:
            from agents.registry import build_agent
            agent = build_agent(engine)
            fn = make_llm_signal_fn(agent)
        results[engine] = run_backtest(client, symbol, fn, warmup=args.warmup)

    print_comparison(results)
    return 0


def _main_coordinated(args):
    """Backtest del pipeline coordinado (mesa en el bucle) sobre los símbolos de los
    agentes indicados. Escribe en una DB temporal para no tocar la de producción."""
    import json
    import os
    import tempfile

    from agents.registry import build_agent
    from agents.coordinator import RiskBook, CoordinatorAgent
    from core.config import get_coordinator_config
    from core import db

    agent_names = [e.strip() for e in args.engines.split(",") if e.strip()]
    if not agent_names:
        print("Indica al menos un agente con --engines.")
        return 1
    agents = [build_agent(name) for name in agent_names]

    # Series + info por símbolo (de un JSON multi o del bróker vivo).
    series, infos = {}, {}
    if args.candles:
        with open(args.candles, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if "series" in data:
            series = data["series"]
            infos = {s: _info_dict(None, args.spread, args.commission) for s in series}
            infos.update(data.get("infos", {}))
        else:  # formato single
            series = {data.get("symbol", args.symbol): data["candles"]}
            infos = {data.get("symbol", args.symbol): _info_dict(None, args.spread, args.commission)}
    else:
        from clients.mt4_client import MT4Client
        mt = MT4Client()
        if not mt.connect():
            print("No se pudo conectar al bridge MT4 (¿EA adjunto?).")
            return 1
        for ag in agents:
            series[ag.symbol] = _load_candles_from_client(mt, ag.symbol, args.bars)
            infos[ag.symbol] = _info_dict(mt.get_symbol_info(ag.symbol), args.spread, args.commission)
    if not all(series.get(ag.symbol) for ag in agents):
        print("Faltan velas para algún símbolo de los agentes.")
        return 1

    from clients.replay_client import MultiSymbolReplayClient
    client = MultiSymbolReplayClient(series, infos, starting_balance=args.balance)

    # DB temporal: el ejecutor real escribe señales/trades/stats; no tocar producción.
    tmp_db = os.path.join(tempfile.gettempdir(), "backtest_coord.db")
    db.init_db(f"sqlite:///{tmp_db}")

    risk_book = RiskBook(get_coordinator_config())
    if args.coord == "llm":
        cfg = get_coordinator_config()
        coordinator = CoordinatorAgent(provider=cfg.get("provider") or "gemini",
                                       model=cfg.get("model") or "gemini-2.0-flash",
                                       risk_book=risk_book, temperature=cfg["temperature"])
    else:
        coordinator = DeterministicCoordinator(risk_book)

    if args.signal == "llm":
        signal_fns = {ag.symbol: make_llm_signal_fn(ag) for ag in agents}
    else:
        signal_fns = {ag.symbol: make_baseline_signal_fn() for ag in agents}

    res = run_coordinated_backtest(client, agents, coordinator, risk_book, signal_fns,
                                   warmup=args.warmup, quiet=True)
    label = f"mesa[{args.coord}]·señal[{args.signal}]"
    print_comparison({label: res})
    # Desglose por símbolo (P/L realizado del simulador).
    by_sym: dict = {}
    for t in client.closed_trades:
        b = by_sym.setdefault(t["symbol"], [0.0, 0])
        b[0] += t["pnl"]
        b[1] += 1
    print("\nPor símbolo (P/L realizado · nº cierres):")
    for sym, (pnl, n) in sorted(by_sym.items()):
        print(f"  {sym:<12} {pnl:>+8.2f}  ({n})")

    # Diagnóstico: explica un resultado de 0 operaciones (lo más confuso).
    d = res.get("diagnostics", {})
    print(f"\nDiagnóstico: {d.get('candles')} velas comunes · {d.get('bars')} barras recorridas · "
          f"{d.get('actionable_signals')} señales accionables · "
          f"{d.get('approved_by_mesa')} aprobadas por la mesa · "
          f"{d.get('orders_placed')} órdenes colocadas.")
    if res.get("trades", 0) == 0 and d.get("orders_placed", 0) == 0:
        if (d.get("candles") or 0) <= args.warmup:
            print(f"  → Pocas velas comunes ({d.get('candles')}) para el warmup ({args.warmup}): "
                  "baja --warmup o vuelca símbolos de la MISMA clase (la intersección por "
                  "timestamp entre cripto y forex/índices deja muy pocas velas).")
        elif (d.get("actionable_signals") or 0) == 0:
            print("  → 0 señales accionables: la tendencia quedó 'lateral' o faltan datos "
                  "(prueba --signal llm, o revisa que las velas tengan recorrido).")
        elif (d.get("approved_by_mesa") or 0) == 0:
            print("  → La MESA vetó todo (exposición/correlación/cooldown/nocional). Revisa los topes.")
        else:
            print("  → La mesa aprobó pero el ESPECIALISTA rechazó en validación "
                  "(min_confidence/min_rr/max_spread del agente demasiado estrictos).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
