"""Construcción de contexto de mercado estructurado para la IA.

En lugar de volcar 100 velas crudas, calcula indicadores en Python y
entrega un resumen compacto + las últimas velas. Menos tokens, análisis
más fiable y rápido.
"""
from typing import List, Optional

from core import indicators as ta
from core.clock import broker_dt_from_mt_epoch


def _fmt(value: Optional[float], digits: int = 5) -> str:
    if value is None:
        return "N/A"
    return f"{value:.{digits}f}"


def _candles_section(rates: List[dict], digits: int, last_n: int = 24) -> List[str]:
    lines = [f"Últimas {min(last_n, len(rates))} velas (time, open, high, low, close, volume):"]
    for r in rates[-last_n:]:
        dt = broker_dt_from_mt_epoch(r["time"]).strftime("%m-%d %H:%M")
        lines.append(
            f"{dt}, {r['open']:.{digits}f}, {r['high']:.{digits}f}, "
            f"{r['low']:.{digits}f}, {r['close']:.{digits}f}, {int(r['volume'])}"
        )
    return lines


def _indicators_section(rates: List[dict], digits: int, label: str) -> List[str]:
    closes = [r["close"] for r in rates]
    highs = [r["high"] for r in rates]
    lows = [r["low"] for r in rates]

    rsi_val = ta.rsi(closes)
    ema20 = ta.ema(closes, 20)
    ema50 = ta.ema(closes, 50)
    macd_val = ta.macd(closes)
    bb = ta.bollinger(closes)
    atr_val = ta.atr(highs, lows, closes)
    sr = ta.support_resistance(highs, lows)
    chg_4 = ta.pct_change(closes, 4)
    chg_24 = ta.pct_change(closes, 24)

    price = closes[-1]
    lines = [f"--- Indicadores {label} ---"]
    lines.append(f"Cierre actual: {price:.{digits}f}")
    if chg_4 is not None:
        lines.append(f"Cambio 4 velas: {chg_4:+.2f}% | Cambio 24 velas: "
                     f"{f'{chg_24:+.2f}%' if chg_24 is not None else 'N/A'}")
    lines.append(f"RSI(14): {_fmt(rsi_val, 1)}")

    if ema20 is not None and ema50 is not None:
        cross = "EMA20 > EMA50 (alcista)" if ema20 > ema50 else "EMA20 < EMA50 (bajista)"
        pos = "encima" if price > ema20 else "debajo"
        lines.append(f"EMA20: {ema20:.{digits}f} | EMA50: {ema50:.{digits}f} | {cross} | precio {pos} de EMA20")

    if macd_val:
        macd_state = "alcista" if macd_val["histogram"] > 0 else "bajista"
        lines.append(f"MACD: {macd_val['macd']:.{digits}f} | Señal: {macd_val['signal']:.{digits}f} "
                     f"| Histograma: {macd_val['histogram']:.{digits}f} ({macd_state})")

    if bb:
        if price > bb["upper"]:
            bb_pos = "FUERA de banda superior (sobreextendido)"
        elif price < bb["lower"]:
            bb_pos = "FUERA de banda inferior (sobreextendido)"
        else:
            width = bb["upper"] - bb["lower"]
            pct = (price - bb["lower"]) / width * 100 if width else 50
            bb_pos = f"al {pct:.0f}% del rango de bandas"
        lines.append(f"Bollinger(20,2): sup={bb['upper']:.{digits}f} med={bb['middle']:.{digits}f} "
                     f"inf={bb['lower']:.{digits}f} | precio {bb_pos}")

    if atr_val:
        lines.append(f"ATR(14): {atr_val:.{digits}f}")

    if sr["supports"]:
        lines.append("Soportes: " + ", ".join(f"{s:.{digits}f}" for s in sr["supports"]))
    if sr["resistances"]:
        lines.append("Resistencias: " + ", ".join(f"{r:.{digits}f}" for r in sr["resistances"]))

    return lines


def _momentum_section(rates_h1: List[dict], rates_h4: List[dict]) -> List[str]:
    """Bloque de MOMENTO/ESTRUCTURA determinista (calculado en Python, no por el
    LLM) para que el especialista no dependa solo del cruce EMA20/50, que va
    rezagado en los giros. Resume el sesgo de momento de H1 (y H4 si hay) y avisa
    de un POSIBLE GIRO confirmado por ruptura de estructura."""
    def _state(rates):
        if not rates:
            return None
        closes = [r["close"] for r in rates]
        highs = [r["high"] for r in rates]
        lows = [r["low"] for r in rates]
        return ta.trend_state(closes, highs, lows)

    ts1 = _state(rates_h1)
    if not ts1:
        return []
    label = {"bullish": "alcista", "bearish": "bajista", "sideways": "lateral"}
    bos = {"up": "ruptura al alza", "down": "ruptura a la baja", None: "sin ruptura"}
    lines = ["--- Momento / estructura (señal rápida, determinista) ---"]
    lines.append(f"Momento H1: {label.get(ts1['direction'], ts1['direction'])} "
                 f"(score {ts1['score']:+d}) | estructura: {bos.get(ts1['broke_structure'])}")
    ts4 = _state(rates_h4)
    if ts4:
        lines.append(f"Momento H4: {label.get(ts4['direction'], ts4['direction'])} "
                     f"(score {ts4['score']:+d})")
    if ts1.get("reversal"):
        lines.append(
            f"⚠ POSIBLE GIRO {label.get(ts1['reversal'], ts1['reversal'])}: el momento H1 "
            f"se está dando la vuelta (ruptura de estructura + momentum). No abras a favor "
            f"del movimiento agotado; si tienes posición contraria, protégela.")
    return lines


def momentum_snapshot(client, symbol: str) -> Optional[dict]:
    """``trend_state`` determinista de H1 para ADJUNTAR a la señal: lo consume la
    mesa (RiskBook) para disparar la guardia de reversión sin esperar a que el LLM
    relabele su tendencia (que va rezagada). Fetch único de H1; devuelve None ante
    datos insuficientes o cualquier error (fail-safe: nunca tumba el análisis)."""
    try:
        rates = client.get_ohlcv(symbol, timeframe="H1", bars=120)
        if not rates:
            return None
        closes = [r["close"] for r in rates]
        highs = [r["high"] for r in rates]
        lows = [r["low"] for r in rates]
        return ta.trend_state(closes, highs, lows)
    except Exception:
        return None


def build_market_context(client, symbol: str, positions: list = None,
                         memory_summary: str = "", news_context: str = "") -> str:
    """Contexto completo para el prompt: tick, indicadores H1 (+H4 si hay), velas, posiciones, memoria."""
    sym_info = client.get_symbol_info(symbol)
    digits = getattr(sym_info, "digits", 5) if sym_info else 5
    tick = client.get_tick(symbol)

    lines = [f"=== {symbol} ==="]
    if tick:
        spread = (tick.ask - tick.bid) / (getattr(sym_info, "point", 1) or 1) if sym_info else 0
        lines.append(f"Precio actual: Ask={tick.ask} Bid={tick.bid} | Spread: {spread:.0f} puntos")

    rates_h1 = client.get_ohlcv(symbol, timeframe="H1", bars=120)
    if rates_h1:
        lines.append("")
        lines += _indicators_section(rates_h1, digits, "H1")

    rates_h4 = client.get_ohlcv(symbol, timeframe="H4", bars=80)
    if rates_h4:
        lines.append("")
        lines += _indicators_section(rates_h4, digits, "H4 (contexto de tendencia mayor)")

    if rates_h1:
        momentum_lines = _momentum_section(rates_h1, rates_h4)
        if momentum_lines:
            lines.append("")
            lines += momentum_lines

    if rates_h1:
        lines.append("")
        lines += _candles_section(rates_h1, digits, last_n=6)

    if positions:
        lines.append("")
        lines.append("--- Posiciones abiertas en este símbolo ---")
        total_profit = 0.0
        for p in positions:
            direction = getattr(p, "direction", None) or (p.get("type") if isinstance(p, dict) else "?")
            profit_raw = getattr(p, "profit", None) if not isinstance(p, dict) else p.get("profit", 0)
            try:
                profit = float(profit_raw)
            except (TypeError, ValueError):
                profit = 0.0
            open_price = getattr(p, "open_price", None) if not isinstance(p, dict) else p.get("open_price", "?")
            current_price = getattr(p, "current_price", None) if not isinstance(p, dict) else p.get("current_price", "?")
            total_profit += profit
            lines.append(f"{direction} @ {open_price} | Actual: {current_price} | P/L flotante: ${profit:+.2f}")
        lines.append(f"Profit no realizado total ({symbol}): ${total_profit:+.2f}")

    if news_context:
        lines.append("")
        lines.append("--- Noticias y eventos económicos ---")
        lines.append(news_context[:800])

    if memory_summary:
        lines.append("")
        lines.append("--- Rendimiento reciente de tus señales en este símbolo ---")
        lines.append(memory_summary[:400])

    return "\n".join(lines)
