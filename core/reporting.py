"""Construcción del reporte periódico de la mesa de dirección.

`build_report` es (casi) puro: recibe los datos ya recolectados por el
orquestador y devuelve un dict con `subject`, `text` y `html`. No hace E/S ni
llamadas a red — eso lo hace `core/mailer.py`. Así es fácil de testear y de
mostrar en consola/dashboard aunque el envío esté desactivado.
"""
from datetime import datetime
from typing import Optional


def _pct(value, dp: int = 1) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{value * 100:.{dp}f}%"
    except (TypeError, ValueError):
        return "n/a"


def _money(value) -> str:
    if value is None:
        return "—"
    try:
        return f"${value:,.2f}"
    except (TypeError, ValueError):
        return "—"


def _net_label(direction: str, net_exposure_pct) -> str:
    if direction in ("LONG", "SHORT"):
        return f"neto {direction} ({_pct(net_exposure_pct)})"
    return "neto FLAT"


def build_report(
    account: Optional[dict],
    snapshot: Optional[dict],
    coordination: Optional[dict],
    agents_overview: Optional[dict],
    closed_trades: Optional[list] = None,
    generated_at: Optional[str] = None,
) -> dict:
    """Arma el reporte de estado global.

    - account: info de cuenta (dict de get_account_info / bot_state).
    - snapshot: snapshot del RiskBook (exposición, sesgo neto por símbolo...).
    - coordination: última coordinación/junta {rationale, decisions}.
    - agents_overview: resumen por agente (params + stats + rendimiento).
    - closed_trades: historial de cierres (se resume el total y el P/L).

    Devuelve {subject, text, html}. Tolerante a None / campos ausentes.
    """
    ts = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    snapshot = snapshot or {}
    account = account or {}
    coordination = coordination or {}
    agents_overview = agents_overview or {}
    closed_trades = closed_trades or []

    equity = snapshot.get("equity", account.get("equity"))
    balance = snapshot.get("balance", account.get("balance"))
    daily_pnl_pct = snapshot.get("daily_pnl_pct")

    lines = []
    lines.append(f"REPORTE DE LA MESA DE DIRECCIÓN — {ts}")
    lines.append("=" * 56)

    # --- Cuenta / cartera ---
    lines.append("")
    lines.append("CUENTA")
    lines.append(f"  Equity:   {_money(equity)}")
    lines.append(f"  Balance:  {_money(balance)}")
    lines.append(f"  P/L día:  {_pct(daily_pnl_pct, 2)}"
                 + ("  ⚠ COOLDOWN activo" if snapshot.get("in_cooldown") else ""))
    lines.append(
        f"  Exposición total: {_pct(snapshot.get('total_exposure_pct'))}"
        f" / tope {_pct(snapshot.get('max_total_exposure_pct'), 0)}"
    )
    lines.append(
        f"  Cobertura (hedge): {'disponible' if snapshot.get('hedging') else 'no disponible'}"
        f" · cierre automático: {'sí' if snapshot.get('can_close') else 'no'}"
    )

    # --- Por símbolo (sesgo neto + P/L flotante + exposición) ---
    symbols = snapshot.get("symbols", {}) or {}
    lines.append("")
    lines.append("POR SÍMBOLO")
    if symbols:
        for sym, s in sorted(symbols.items()):
            lines.append(
                f"  {sym}: {_net_label(s.get('net_direction', 'FLAT'), s.get('net_exposure_pct'))}"
                f" · {s.get('long_positions', 0)}L/{s.get('short_positions', 0)}S"
                f" · P/L {_money(s.get('floating_pnl'))}"
                f" · exp {_pct(s.get('exposure_pct'))}/{_pct(s.get('max_allocation_pct'), 0)}"
            )
    else:
        lines.append("  (sin posiciones abiertas)")

    # --- Decisiones de la última coordinación / junta ---
    decisions = coordination.get("decisions", []) or []
    rationale = coordination.get("rationale", "")
    lines.append("")
    lines.append("ÚLTIMA COORDINACIÓN / JUNTA")
    if rationale:
        lines.append(f"  Razón: {rationale}")
    if decisions:
        for d in decisions:
            tag = "APROBADA" if d.get("approve") else "vetada"
            md = f" -> {d['manage_direction']}" if d.get("manage_direction") else ""
            clamp = f" | {d['clamp']}" if d.get("clamp") else ""
            lines.append(
                f"  {d.get('symbol')}: {tag} | pos: {d.get('position_action', 'hold')}{md}"
                f" | asignación {_pct(d.get('allocation_pct'), 0)}{clamp}"
            )
    else:
        lines.append("  (sin decisiones registradas)")

    # --- Rendimiento por agente ---
    agents = agents_overview.get("agents", []) or []
    lines.append("")
    lines.append("AGENTES")
    if agents:
        for a in agents:
            perf = a.get("performance", {}) or {}
            stats = a.get("stats", {}) or {}
            lines.append(
                f"  {a.get('name')} [{a.get('symbol')}] {a.get('provider', '').upper()}/{a.get('model', '')}"
            )
            lines.append(
                f"     señales {stats.get('signals', 0)} · trades {stats.get('trades', 0)}"
                f" · holds {stats.get('holds', 0)}"
                f" | win {_pct(perf.get('win_rate'), 0)} · SL {_pct(perf.get('sl_hit_rate'), 0)}"
                f" · TP {_pct(perf.get('tp_hit_rate'), 0)} ({perf.get('samples', 0)} muestras)"
            )
    else:
        lines.append("  (sin agentes)")

    # --- Cierres recientes ---
    total_pnl = sum((t.get("pnl") or 0.0) for t in closed_trades)
    lines.append("")
    lines.append(
        f"CIERRES REGISTRADOS (sesión): {len(closed_trades)} · P/L acumulado {_money(total_pnl)}"
    )

    text = "\n".join(lines)
    subject = f"[Bot Trading] Reporte {ts} · Equity {_money(equity)} · P/L día {_pct(daily_pnl_pct, 2)}"
    html = "<pre style=\"font-family:Consolas,monospace;font-size:13px\">" + _escape(text) + "</pre>"
    return {"subject": subject, "text": text, "html": html}


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
