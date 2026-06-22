"""Métricas de rendimiento HONESTAS reconciliando contra el libro de balance.

El problema que resuelve: la tabla ``closed_trades`` registra como P/L el último
*flotante* observado antes de que la posición desapareciera (no el realizado), y
la comisión siempre como 0. Resultado: el P/L registrado puede diferir VARIAS
veces del realizado de verdad (en producción se vio −$14.69 registrado frente a
≈−$76 reales). La única fuente fiable que ya tenemos sin tocar el EA es el
``balance`` de la cuenta (tabla ``equity``): el balance SOLO cambia cuando un
cierre se realiza, o cuando hay un depósito/retiro. Reconciliando los saltos de
balance obtenemos el P/L de trading realizado AGREGADO (correcto) y lo
contrastamos con lo registrado para hacer visible la subestimación.

Flujos de caja (depósitos/retiros): no se pueden distinguir de un cierre solo por
el balance. Se pasan explícitos (lista de importes con signo: depósito +, retiro
−) o por env ``KNOWN_CASH_FLOWS`` (coma, p. ej. ``50,-20``). Sin ellos, el P/L de
trading coincide con el cambio neto de balance y se marca ``cash_flows_known=False``.

Todo es de SOLO LECTURA sobre la DB. Las funciones son (casi) puras para poder
testearlas: reciben ``platform`` y opcionalmente ``cash_flows``.
"""
import os
from typing import List, Optional

from core.db import ClosedTrade, EquityPoint, get_session


def _get_cash_flows(explicit: Optional[List[float]]) -> Optional[List[float]]:
    """Lista de flujos de caja con signo. Si ``explicit`` es None, lee el env
    ``KNOWN_CASH_FLOWS`` (coma). Devuelve None si no hay información (para poder
    distinguir "sin flujos conocidos" de "cero flujos")."""
    if explicit is not None:
        return explicit
    raw = (os.getenv("KNOWN_CASH_FLOWS", "") or "").strip()
    if not raw:
        return None
    out = []
    for tok in raw.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            out.append(float(tok))
        except ValueError:
            continue
    return out or None


def balance_ledger(platform: str = "mt4", eps: float = 0.005) -> List[dict]:
    """Saltos del ``balance`` realizado entre puntos consecutivos de ``equity``.

    Devuelve ``[{"t", "delta", "balance"}]`` solo para los cambios cuyo módulo
    supere ``eps`` (filtra ruido de redondeo). Cada salto corresponde a un cierre
    realizado (o a un depósito/retiro)."""
    session = get_session()
    try:
        rows = (session.query(EquityPoint.timestamp, EquityPoint.balance)
                .filter(EquityPoint.platform == platform.upper())
                .order_by(EquityPoint.timestamp.asc()).all())
    finally:
        session.close()

    steps, prev = [], None
    for ts, bal in rows:
        if bal is None:
            continue
        bal = float(bal)
        if prev is not None and abs(bal - prev) > eps:
            steps.append({
                "t": ts.strftime("%Y-%m-%d %H:%M:%S") if ts else "",
                "delta": round(bal - prev, 2),
                "balance": round(bal, 2),
            })
        prev = bal
    return steps


def ledger_summary(platform: str = "mt4",
                   cash_flows: Optional[List[float]] = None,
                   eps: float = 0.005) -> dict:
    """P/L de trading realizado reconciliado desde el libro de balance.

    - ``net_change``: balance final − inicial (incluye depósitos/retiros).
    - ``cash_flow_total``: suma de flujos de caja conocidos (None si se desconocen).
    - ``trading_pnl``: ``net_change − cash_flow_total`` (P/L puro de trading).
    - ``gross_profit``/``gross_loss``/``profit_factor``: de los saltos, intentando
      excluir los que casan con un flujo de caja conocido (aprox. si no casan)."""
    cash_flows = _get_cash_flows(cash_flows)
    steps = balance_ledger(platform, eps=eps)

    session = get_session()
    try:
        first = (session.query(EquityPoint.balance)
                 .filter(EquityPoint.platform == platform.upper(),
                         EquityPoint.balance.isnot(None))
                 .order_by(EquityPoint.timestamp.asc()).first())
        last = (session.query(EquityPoint.balance)
                .filter(EquityPoint.platform == platform.upper(),
                        EquityPoint.balance.isnot(None))
                .order_by(EquityPoint.timestamp.desc()).first())
    finally:
        session.close()

    start_balance = round(float(first[0]), 2) if first else None
    end_balance = round(float(last[0]), 2) if last else None
    net_change = (round(end_balance - start_balance, 2)
                  if start_balance is not None and end_balance is not None else None)

    cash_flow_total = round(sum(cash_flows), 2) if cash_flows is not None else None
    trading_pnl = (round(net_change - (cash_flow_total or 0.0), 2)
                   if net_change is not None else None)

    # Excluye de los saltos los que casen (importe ≈) con un flujo de caja conocido,
    # para que gross/PF reflejen solo operativa. Best-effort: si no casa, queda dentro.
    deltas = [s["delta"] for s in steps]
    if cash_flows:
        remaining = list(deltas)
        for cf in cash_flows:
            best_i, best_diff = None, 0.011
            for i, d in enumerate(remaining):
                diff = abs(d - cf)
                if diff <= best_diff:
                    best_i, best_diff = i, diff
            if best_i is not None:
                remaining.pop(best_i)
        deltas = remaining

    gross_profit = round(sum(d for d in deltas if d > 0), 2)
    gross_loss = round(sum(d for d in deltas if d < 0), 2)
    pf = round(gross_profit / abs(gross_loss), 2) if gross_loss else None

    return {
        "start_balance": start_balance,
        "end_balance": end_balance,
        "net_change": net_change,
        "cash_flows_known": cash_flows is not None,
        "cash_flow_total": cash_flow_total,
        "trading_pnl": trading_pnl,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": pf,
        "n_up_steps": sum(1 for d in deltas if d > 0),
        "n_down_steps": sum(1 for d in deltas if d < 0),
    }


def _metrics_from_pnls(pnls: List[float], commissions: List[float]) -> dict:
    """Métricas (win rate, PF, expectancy, medias) de una lista de P/L."""
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_profit = round(sum(wins), 2)
    gross_loss = round(sum(losses), 2)
    decided = len(wins) + len(losses)
    total = round(sum(pnls), 2)
    # `pnl` ya viene NETO de la reconciliación (profit+comisión+swap), así que
    # `total_pnl` es el realizado neto. `commission` es la suma de costes (signo del
    # bróker, negativo) SOLO informativa: no se vuelve a restar de `total_pnl`.
    commission = round(sum(commissions), 2)
    return {
        "count": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / decided, 3) if decided else None,
        "avg_win": round(sum(wins) / len(wins), 3) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 3) if losses else None,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": round(gross_profit / abs(gross_loss), 2) if gross_loss else None,
        "expectancy": round(total / n, 3) if n else None,
        "total_pnl": total,
        "commission": commission,
        "net_pnl": total,
    }


def closed_trades_summary(platform: str = "mt4", symbol: Optional[str] = None,
                          direction: Optional[str] = None) -> dict:
    """Métricas desde ``closed_trades`` (filtrable por símbolo/dirección).

    Nota: hasta que la reconciliación por ticket contra el bróker esté activa,
    el ``pnl`` de esta tabla es aproximado (flotante, no realizado); compáralo con
    ``ledger_summary`` para ver la magnitud del error."""
    session = get_session()
    try:
        q = session.query(ClosedTrade).filter(ClosedTrade.platform == platform.upper(),
                                               ClosedTrade.pnl.isnot(None))
        if symbol:
            q = q.filter(ClosedTrade.symbol == symbol)
        if direction:
            q = q.filter(ClosedTrade.action == direction.upper())
        rows = q.all()
        pnls = [float(r.pnl) for r in rows]
        comms = [float(r.commission or 0.0) for r in rows]
    finally:
        session.close()
    return _metrics_from_pnls(pnls, comms)


def by_dimension(platform: str = "mt4", dimension: str = "symbol") -> dict:
    """Métricas de ``closed_trades`` agrupadas por ``symbol`` o ``action``."""
    col = ClosedTrade.action if dimension == "direction" else ClosedTrade.symbol
    session = get_session()
    try:
        rows = (session.query(col, ClosedTrade.pnl, ClosedTrade.commission)
                .filter(ClosedTrade.platform == platform.upper(),
                        ClosedTrade.pnl.isnot(None)).all())
    finally:
        session.close()
    buckets: dict = {}
    for key, pnl, comm in rows:
        b = buckets.setdefault(key or "?", {"pnls": [], "comms": []})
        b["pnls"].append(float(pnl))
        b["comms"].append(float(comm or 0.0))
    return {k: _metrics_from_pnls(v["pnls"], v["comms"]) for k, v in buckets.items()}


def data_quality(platform: str = "mt4") -> dict:
    """Banderas de fiabilidad del registro de cierres: ¿comisión siempre 0?,
    ¿duraciones negativas (bug de zona horaria)?"""
    session = get_session()
    try:
        rows = (session.query(ClosedTrade.commission, ClosedTrade.duration_seconds)
                .filter(ClosedTrade.platform == platform.upper()).all())
    finally:
        session.close()
    n = len(rows)
    nonzero_comm = sum(1 for c, _ in rows if c)
    neg_dur = sum(1 for _, d in rows if d is not None and d < 0)
    return {
        "closed_count": n,
        "commission_all_zero": n > 0 and nonzero_comm == 0,
        "nonzero_commission_count": nonzero_comm,
        "negative_durations": neg_dur,
    }


def performance_summary(platform: str = "mt4",
                        cash_flows: Optional[List[float]] = None) -> dict:
    """Resumen maestro: realizado por libro de balance + métricas de
    ``closed_trades`` + discrepancia entre ambos + banderas de calidad de datos."""
    ledger = ledger_summary(platform, cash_flows=cash_flows)
    closed = closed_trades_summary(platform)
    quality = data_quality(platform)

    ledger_pnl = ledger.get("trading_pnl")
    recorded_pnl = closed.get("total_pnl")
    discrepancy = (round(ledger_pnl - recorded_pnl, 2)
                   if ledger_pnl is not None and recorded_pnl is not None else None)

    return {
        "platform": platform.upper(),
        "ledger": ledger,
        "closed_trades": closed,
        "by_symbol": by_dimension(platform, "symbol"),
        "by_direction": by_dimension(platform, "direction"),
        "data_quality": quality,
        "recorded_vs_real": {
            "recorded_pnl": recorded_pnl,
            "real_trading_pnl": ledger_pnl,
            "discrepancy": discrepancy,
            "reliable": discrepancy is not None and abs(discrepancy) <= 1.0,
        },
    }


def format_summary_lines(summary: dict) -> List[str]:
    """Líneas de texto del resumen para consola/reporte/email."""
    def money(v):
        return f"${v:,.2f}" if isinstance(v, (int, float)) else "—"

    def pct(v):
        return f"{v * 100:.1f}%" if isinstance(v, (int, float)) else "n/a"

    led = summary.get("ledger", {})
    cl = summary.get("closed_trades", {})
    rv = summary.get("recorded_vs_real", {})
    dq = summary.get("data_quality", {})
    lines = ["RENDIMIENTO REAL (reconciliado con el libro de balance)", "=" * 56]
    lines.append(f"  Balance:   {money(led.get('start_balance'))} -> {money(led.get('end_balance'))}"
                 f"  (cambio neto {money(led.get('net_change'))})")
    if led.get("cash_flows_known"):
        lines.append(f"  Flujos de caja conocidos: {money(led.get('cash_flow_total'))}")
    else:
        lines.append("  ⚠ Flujos de caja DESCONOCIDOS (fija KNOWN_CASH_FLOWS para aislar el P/L de trading)")
    lines.append(f"  P/L de TRADING real: {money(led.get('trading_pnl'))}"
                 f"  · PF (libro) {led.get('profit_factor')}")
    lines.append("")
    lines.append(f"  Registrado en closed_trades: {money(cl.get('total_pnl'))}"
                 f" ({cl.get('count')} cierres) · win {pct(cl.get('win_rate'))}"
                 f" · PF {cl.get('profit_factor')} · expectancy {money(cl.get('expectancy'))}")
    if rv.get("discrepancy") is not None and not rv.get("reliable"):
        lines.append(f"  ⚠ DISCREPANCIA real vs registrado: {money(rv.get('discrepancy'))}"
                     " — el registro subestima las pérdidas (P/L del flotante, no realizado).")
    if dq.get("commission_all_zero"):
        lines.append("  ⚠ Comisión 0 en TODOS los cierres: los costes no se están capturando.")
    if dq.get("negative_durations"):
        lines.append(f"  ⚠ {dq['negative_durations']} duraciones negativas (zona horaria).")
    return lines
