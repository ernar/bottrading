"""Tests de la construcción del reporte (build_report) y del gating del envío
SMTP (mailer.send_report)."""
from core.reporting import build_report
from core.mailer import send_report


def _snapshot():
    return {
        "equity": 10_000.0,
        "balance": 10_200.0,
        "total_exposure_pct": 0.25,
        "max_total_exposure_pct": 0.5,
        "daily_pnl_pct": -0.012,
        "in_cooldown": False,
        "hedging": True,
        "can_close": True,
        "open_positions_total": 3,
        "symbols": {
            "BTCUSD": {
                "net_direction": "LONG",
                "net_exposure_pct": 0.18,
                "long_positions": 3,
                "short_positions": 0,
                "floating_pnl": -45.0,
                "exposure_pct": 0.18,
                "max_allocation_pct": 0.4,
            }
        },
    }


def _coordination():
    return {
        "rationale": "reducir riesgo direccional",
        "decisions": [
            {"symbol": "BTCUSD", "approve": False, "position_action": "reduce",
             "manage_direction": "BUY", "allocation_pct": 0.0,
             "clamp": "guardia de reversión"},
        ],
    }


def _agents_overview():
    return {
        "agents": [
            {"name": "btc-agent", "symbol": "BTCUSD", "provider": "ollama",
             "model": "qwen3:8b",
             "stats": {"signals": 12, "trades": 4, "holds": 6},
             "performance": {"samples": 8, "win_rate": 0.5, "sl_hit_rate": 0.25,
                             "tp_hit_rate": 0.375, "avg_move_pct": 0.4}},
        ],
    }


def test_build_report_sections():
    rep = build_report(
        account={"equity": 10_000.0},
        snapshot=_snapshot(),
        coordination=_coordination(),
        agents_overview=_agents_overview(),
        closed_trades=[{"pnl": -10.0}, {"pnl": 25.0}],
    )
    assert set(rep.keys()) == {"subject", "text", "html"}
    text = rep["text"]
    assert "REPORTE DE LA MESA" in text
    assert "BTCUSD" in text
    assert "neto LONG" in text           # sesgo direccional
    assert "reduce" in text              # decisión de la mesa
    assert "btc-agent" in text           # rendimiento por agente
    assert "CIERRES REGISTRADOS" in text
    assert rep["subject"].startswith("[Bot Trading]")
    assert rep["html"].startswith("<pre")


def test_build_report_tolerates_none():
    rep = build_report(account=None, snapshot=None, coordination=None,
                       agents_overview=None, closed_trades=None)
    assert "REPORTE DE LA MESA" in rep["text"]
    assert "sin posiciones abiertas" in rep["text"]


def test_send_report_disabled_returns_false():
    # SMTP apagado: no debe intentar conectar y devuelve False.
    assert send_report("s", "t", cfg={"smtp_enabled": False}) is False


def test_send_report_enabled_missing_host_returns_false():
    # Habilitado pero sin host/destinatario -> no envía (sin abrir sockets).
    cfg = {"smtp_enabled": True, "smtp_host": "", "report_email_to": "x@y.z"}
    assert send_report("s", "t", cfg=cfg) is False
