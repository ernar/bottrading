"""Contexto de noticias y eventos económicos para el análisis de la IA.

Dos fuentes gratuitas, sin API key:
- Calendario económico de ForexFactory (JSON semanal público): eventos macro
  con impacto, filtrados por las divisas del símbolo.
- Titulares RSS de Yahoo Finance por ticker.

Todo va con caché en memoria (los feeds no cambian cada minuto) y es
fail-safe: ante cualquier error de red devuelve cadena vacía y el bot
sigue funcionando solo con el análisis técnico.
"""
import os
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
import json
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
YAHOO_RSS_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) mt5-gemini-bot/1.0"

HEADLINES_TTL = 15 * 60       # refrescar titulares cada 15 min
CALENDAR_TTL = 60 * 60        # el calendario semanal cambia poco
MAX_HEADLINES = 5
CALENDAR_WINDOW_HOURS = 24    # eventos en las próximas 24h

# Símbolo del broker -> (ticker de Yahoo Finance, divisas relevantes del calendario)
SYMBOL_NEWS_MAP = {
    "EURUSD": ("EURUSD=X", ["EUR", "USD"]),
    "GBPUSD": ("GBPUSD=X", ["GBP", "USD"]),
    "USDJPY": ("USDJPY=X", ["USD", "JPY"]),
    "USDCAD": ("USDCAD=X", ["USD", "CAD"]),
    "AUDUSD": ("AUDUSD=X", ["AUD", "USD"]),
    "XAUUSD": ("GC=F", ["USD"]),
    "XAGUSD": ("SI=F", ["USD"]),
    "BTCUSD": ("BTC-USD", ["USD"]),
    "ETHUSD": ("ETH-USD", ["USD"]),
    "USOIL": ("CL=F", ["USD"]),
    "WTI": ("CL=F", ["USD"]),
    "BRENT": ("BZ=F", ["USD"]),
}


def _fetch(url: str, timeout: float = 10.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _humanize_age(published_ts) -> str:
    """Antigüedad legible ('hace <1h' / 'hace 2h' / 'hace 3d') a partir de un
    epoch UTC. '' si falta o es futura. Se calcula al leer (no se cachea) para
    que no se quede congelada dentro de la ventana de caché de titulares."""
    if not published_ts:
        return ""
    hours = (time.time() - published_ts) / 3600
    if hours < 0:
        return ""
    if hours < 1:
        return "hace <1h"
    if hours < 24:
        return f"hace {hours:.0f}h"
    return f"hace {hours / 24:.0f}d"


class NewsProvider:

    def __init__(self):
        self._lock = threading.Lock()
        self._headlines_cache: dict = {}   # ticker -> (timestamp, [lineas])
        self._calendar_cache: tuple = (0.0, [])
        self.enabled = os.getenv("NEWS_ENABLED", "true").lower() in ("1", "true", "yes")

    # ----- Titulares (Yahoo Finance RSS) -----

    def _get_headline_items(self, ticker: str) -> list:
        """Titulares del ticker como lista de dicts {title, link, published_ts}.
        Cacheado HEADLINES_TTL. La edad NO se cachea: se deriva al leer con
        `_humanize_age` para que no se congele dentro de la ventana de caché."""
        now = time.time()
        with self._lock:
            cached = self._headlines_cache.get(ticker)
            if cached and now - cached[0] < HEADLINES_TTL:
                return cached[1]
        try:
            raw = _fetch(YAHOO_RSS_URL.format(ticker=ticker))
            root = ET.fromstring(raw)
            items = []
            for item in root.iter("item"):
                title = (item.findtext("title") or "").strip()
                if not title:
                    continue
                link = (item.findtext("link") or "").strip()
                published_ts = None
                pub = item.findtext("pubDate")
                if pub:
                    try:
                        published_ts = parsedate_to_datetime(pub).timestamp()
                    except (ValueError, TypeError):
                        pass
                items.append({"title": title, "link": link, "published_ts": published_ts})
                if len(items) >= MAX_HEADLINES:
                    break
        except Exception:
            items = []
        with self._lock:
            self._headlines_cache[ticker] = (now, items)
        return items

    # ----- Calendario económico (ForexFactory) -----

    def _get_calendar(self) -> list:
        now = time.time()
        with self._lock:
            ts, events = self._calendar_cache
            if events and now - ts < CALENDAR_TTL:
                return events
        try:
            raw = _fetch(CALENDAR_URL)
            events = json.loads(raw)
        except Exception:
            events = []
        with self._lock:
            self._calendar_cache = (now, events)
        return events

    def _upcoming_events(self, currencies: list) -> list:
        """Eventos de impacto medio/alto para esas divisas en las próximas 24h."""
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(hours=CALENDAR_WINDOW_HOURS)
        lines = []
        for ev in self._get_calendar():
            # "All" = eventos globales (OPEC, G7...), relevantes para cualquier símbolo
            if ev.get("country") not in currencies and ev.get("country") != "All":
                continue
            impact = ev.get("impact", "")
            if impact not in ("High", "Medium"):
                continue
            try:
                dt = datetime.fromisoformat(ev["date"])
            except (KeyError, ValueError):
                continue
            if not (now - timedelta(hours=1) <= dt <= horizon):
                continue
            delta_h = (dt - now).total_seconds() / 3600
            when = f"en {delta_h:.1f}h" if delta_h >= 0 else f"hace {-delta_h:.1f}h"
            extra = ""
            if ev.get("forecast"):
                extra = f" | previsión: {ev['forecast']}, anterior: {ev.get('previous', 'N/A')}"
            lines.append(f"- [{ev['country']}] {ev.get('title', '?')} ({impact}, {when}){extra}")
        return lines[:8]

    # ----- API pública -----

    def get_high_impact_events(self, symbol: str) -> list:
        """Eventos RED (impacto ALTO) próximos para las divisas del símbolo.

        "RED" = la etiqueta roja del calendario de ForexFactory, es decir
        `impact == "High"` (los Medium quedan fuera; esos van al contexto del
        prompt vía `get_news_context`, no disparan reacción inmediata).

        Devuelve una lista de dicts con una **clave estable** (`key`) para que el
        orquestador pueda deduplicar y reaccionar a cada evento una sola vez.
        Fail-safe: lista vacía si las noticias están desactivadas, el símbolo no
        está mapeado o el calendario falla."""
        if not self.enabled:
            return []
        mapping = SYMBOL_NEWS_MAP.get(symbol.upper())
        if not mapping:
            return []
        _, currencies = mapping

        now = datetime.now(timezone.utc)
        horizon = now + timedelta(hours=CALENDAR_WINDOW_HOURS)
        events = []
        for ev in self._get_calendar():
            if ev.get("country") not in currencies and ev.get("country") != "All":
                continue
            if ev.get("impact") != "High":
                continue
            raw_date = ev.get("date", "")
            try:
                dt = datetime.fromisoformat(raw_date)
            except (KeyError, ValueError):
                continue
            if not (now - timedelta(hours=1) <= dt <= horizon):
                continue
            country = ev.get("country", "?")
            title = ev.get("title", "?")
            delta_h = (dt - now).total_seconds() / 3600
            when = f"en {delta_h:.1f}h" if delta_h >= 0 else f"hace {-delta_h:.1f}h"
            events.append({
                # Clave estable para deduplicar reacciones (no incluye el tiempo
                # relativo, que cambia en cada sondeo).
                "key": f"{country}|{title}|{raw_date}",
                "country": country,
                "title": title,
                "when": when,
                "impact": "High",
                "forecast": ev.get("forecast"),
                "previous": ev.get("previous"),
            })
        return events

    def get_news_context(self, symbol: str) -> str:
        """Sección de noticias/eventos para inyectar en el prompt. '' si no hay nada."""
        if not self.enabled:
            return ""
        mapping = SYMBOL_NEWS_MAP.get(symbol.upper())
        if not mapping:
            return ""
        ticker, currencies = mapping

        sections = []
        events = self._upcoming_events(currencies)
        if events:
            sections.append(f"Eventos económicos próximos ({'/'.join(currencies)}, impacto medio/alto):")
            sections.extend(events)

        items = self._get_headline_items(ticker)
        if items:
            if sections:
                sections.append("")
            sections.append("Titulares recientes:")
            for it in items:
                age = _humanize_age(it["published_ts"])
                sections.append(f"- {it['title']}" + (f" ({age})" if age else ""))

        return "\n".join(sections)

    def get_headlines(self, symbol: str) -> list:
        """Titulares recientes del símbolo como lista de dicts {title, link, age},
        para el slider de noticias del dashboard. Fail-safe: lista vacía si las
        noticias están desactivadas, el símbolo no está mapeado o el feed falla."""
        if not self.enabled:
            return []
        mapping = SYMBOL_NEWS_MAP.get(symbol.upper())
        if not mapping:
            return []
        ticker = mapping[0]
        return [{
            "title": it["title"],
            "link": it["link"],
            "age": _humanize_age(it["published_ts"]),
        } for it in self._get_headline_items(ticker)]


# Instancia compartida (caché común para todos los símbolos)
news_provider = NewsProvider()
