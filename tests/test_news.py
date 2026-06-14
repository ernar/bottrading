"""Tests de la detección de eventos RED (alto impacto) en NewsProvider."""
from datetime import datetime, timezone, timedelta

from core.news import NewsProvider


def _iso(hours_from_now: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours_from_now)).isoformat()


def _provider(events):
    """NewsProvider con calendario inyectado (sin red) y noticias activadas."""
    p = NewsProvider()
    p.enabled = True
    p._get_calendar = lambda: events  # type: ignore[method-assign]
    return p


def test_only_high_impact_within_window():
    events = [
        {"country": "USD", "impact": "High", "title": "NFP", "date": _iso(2)},       # ✓
        {"country": "USD", "impact": "Medium", "title": "Claims", "date": _iso(2)},  # ✗ medio
        {"country": "USD", "impact": "High", "title": "FOMC futuro", "date": _iso(48)},  # ✗ fuera de ventana
        {"country": "EUR", "impact": "High", "title": "ECB", "date": _iso(2)},       # ✗ divisa no del símbolo
        {"country": "All", "impact": "High", "title": "OPEC", "date": _iso(1)},      # ✓ global
    ]
    out = _provider(events).get_high_impact_events("BTCUSD")
    titles = {e["title"] for e in out}
    assert titles == {"NFP", "OPEC"}
    assert all(e["impact"] == "High" for e in out)


def test_stable_key_format():
    date = _iso(3)
    events = [{"country": "USD", "impact": "High", "title": "CPI", "date": date}]
    out = _provider(events).get_high_impact_events("BTCUSD")
    assert out[0]["key"] == f"USD|CPI|{date}"


def test_disabled_returns_empty():
    p = _provider([{"country": "USD", "impact": "High", "title": "X", "date": _iso(1)}])
    p.enabled = False
    assert p.get_high_impact_events("BTCUSD") == []


def test_unmapped_symbol_returns_empty():
    p = _provider([{"country": "USD", "impact": "High", "title": "X", "date": _iso(1)}])
    assert p.get_high_impact_events("NOSUCHSYM") == []
