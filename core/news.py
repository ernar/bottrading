"""Contexto de noticias y eventos económicos para el análisis de la IA.

Fuentes gratuitas, sin API key:
- Calendario económico de ForexFactory (JSON semanal público): eventos macro
  con impacto, filtrados por las divisas del símbolo.
- Titulares RSS: el de Yahoo Finance por ticker (específico del activo) MÁS
  feeds especializados por clase de activo (crypto, metales, forex, energía).
  Se agregan, se deduplican por título y se descartan los rancios o sin fecha
  (ventana `NEWS_MAX_AGE_HOURS`, default 48h), ordenados por recencia.

Todo va con caché en memoria por URL de feed (los feeds no cambian cada minuto)
y es fail-safe por feed: ante cualquier error de red ese feed aporta cero y el
bot sigue con los demás (o solo con el análisis técnico).
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
MAX_HEADLINES = 6             # titulares por símbolo (tras agregar/dedup/filtrar)
PER_FEED_LIMIT = 12           # tope leído de cada feed antes de agregar
CALENDAR_WINDOW_HOURS = 24    # eventos en las próximas 24h
DEFAULT_MAX_AGE_HOURS = 48    # se descartan los titulares más viejos que esto

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

# Símbolo del broker -> clase de activo (para escoger los feeds especializados).
SYMBOL_ASSET_CLASS = {
    "BTCUSD": "crypto", "ETHUSD": "crypto",
    "XAUUSD": "metals", "XAGUSD": "metals",
    "EURUSD": "forex", "GBPUSD": "forex", "USDJPY": "forex",
    "USDCAD": "forex", "AUDUSD": "forex",
    "USOIL": "energy", "WTI": "energy", "BRENT": "energy",
}

# Feeds RSS especializados por clase de activo (además del de Yahoo por ticker).
# Cada uno es (nombre_fuente, url). Son feeds RSS 2.0 públicos sin API key,
# verificados como parseables y frescos. Fail-safe por feed: si uno cae (404/403,
# red), simplemente no aporta y los demás siguen.
SPECIALIZED_FEEDS = {
    "crypto": [
        ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
        ("Cointelegraph", "https://cointelegraph.com/rss"),
        ("Decrypt", "https://decrypt.co/feed"),
    ],
    "metals": [
        ("Investing", "https://www.investing.com/rss/commodities_Metals.rss"),
        ("Mining.com", "https://www.mining.com/feed/"),
    ],
    "forex": [
        ("FXStreet", "https://www.fxstreet.com/rss/news"),
    ],
    "energy": [
        ("OilPrice", "https://oilprice.com/rss/main"),
        ("Rigzone", "https://www.rigzone.com/news/rss/rigzone_latest.aspx"),
    ],
}

# Clases de activo en las que se OMITE el feed de Yahoo por ticker: sus feeds
# (p. ej. BTC-USD/ETH-USD) mezclan ruido irrelevante (renta variable, retailers)
# y los especializados de la clase ya dan cobertura abundante y de calidad.
YAHOO_SKIP_CLASSES = {"crypto"}


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
        self._headlines_cache: dict = {}   # url de feed -> (timestamp, [items])
        self._calendar_cache: tuple = (0.0, [])
        self.enabled = os.getenv("NEWS_ENABLED", "true").lower() in ("1", "true", "yes")
        # Ventana de frescura: se descartan los titulares más viejos que esto
        # (también los sin fecha verificable). Configurable vía .env.
        try:
            self.max_age_hours = float(os.getenv("NEWS_MAX_AGE_HOURS", "") or DEFAULT_MAX_AGE_HOURS)
        except ValueError:
            self.max_age_hours = DEFAULT_MAX_AGE_HOURS

    # ----- Titulares (RSS: Yahoo por ticker + especializados por activo) -----

    def _get_feed_items(self, url: str, source: str) -> list:
        """Items de un feed RSS 2.0 como dicts {title, link, published_ts, source}.
        Cacheado HEADLINES_TTL por URL (un mismo feed se comparte entre símbolos de
        la misma clase). La edad NO se cachea: se deriva al leer con `_humanize_age`
        para que no se congele dentro de la ventana de caché."""
        now = time.time()
        with self._lock:
            cached = self._headlines_cache.get(url)
            if cached and now - cached[0] < HEADLINES_TTL:
                return cached[1]
        try:
            raw = _fetch(url)
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
                items.append({"title": title, "link": link,
                              "published_ts": published_ts, "source": source})
                if len(items) >= PER_FEED_LIMIT:
                    break
        except Exception:
            items = []
        with self._lock:
            self._headlines_cache[url] = (now, items)
        return items

    def _symbol_feeds(self, symbol: str) -> list:
        """(source, url) de los feeds de un símbolo: el de Yahoo por su ticker
        (específico del activo) + los especializados de su clase de activo. En las
        clases de `YAHOO_SKIP_CLASSES` (crypto) se omite Yahoo por su ruido."""
        sym = symbol.upper()
        cls = SYMBOL_ASSET_CLASS.get(sym, "")
        feeds = []
        mapping = SYMBOL_NEWS_MAP.get(sym)
        if mapping and cls not in YAHOO_SKIP_CLASSES:
            feeds.append(("Yahoo", YAHOO_RSS_URL.format(ticker=mapping[0])))
        feeds.extend(SPECIALIZED_FEEDS.get(cls, []))
        return feeds

    def _collect_headlines(self, symbol: str) -> list:
        """Agrega los feeds del símbolo: descarta lo rancio/sin fecha (ventana
        `max_age_hours`), deduplica por título, ordena por recencia y recorta a
        MAX_HEADLINES. Devuelve dicts {title, link, published_ts, source}."""
        cutoff = time.time() - self.max_age_hours * 3600
        items = []
        for source, url in self._symbol_feeds(symbol):
            items.extend(self._get_feed_items(url, source))
        # Solo frescos y con fecha verificable (descarta los de hace días y los
        # sin pubDate, que no se pueden garantizar recientes).
        fresh = [it for it in items
                 if it["published_ts"] is not None and it["published_ts"] >= cutoff]
        fresh.sort(key=lambda x: x["published_ts"], reverse=True)
        out, seen = [], set()
        for it in fresh:
            key = it["title"].strip().lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(it)
            if len(out) >= MAX_HEADLINES:
                break
        return out

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
        _, currencies = mapping

        sections = []
        events = self._upcoming_events(currencies)
        if events:
            sections.append(f"Eventos económicos próximos ({'/'.join(currencies)}, impacto medio/alto):")
            sections.extend(events)

        items = self._collect_headlines(symbol)
        if items:
            if sections:
                sections.append("")
            sections.append("Titulares recientes:")
            for it in items:
                age = _humanize_age(it["published_ts"])
                src = f" [{it['source']}]" if it.get("source") else ""
                sections.append(f"- {it['title']}{src}" + (f" ({age})" if age else ""))

        return "\n".join(sections)

    def get_headlines(self, symbol: str) -> list:
        """Titulares recientes del símbolo como lista de dicts {title, link, age,
        source}, para el teletipo de noticias del dashboard. Agrega varios feeds
        (Yahoo + especializados), descarta lo rancio y deduplica. Fail-safe: lista
        vacía si las noticias están desactivadas o ningún feed aporta nada."""
        if not self.enabled:
            return []
        return [{
            "title": it["title"],
            "link": it["link"],
            "age": _humanize_age(it["published_ts"]),
            "source": it["source"],
        } for it in self._collect_headlines(symbol)]


# Instancia compartida (caché común para todos los símbolos)
news_provider = NewsProvider()
