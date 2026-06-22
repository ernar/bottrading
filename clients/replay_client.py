"""Cliente de REPLAY para backtesting: sirve velas históricas con un cursor y
simula la cartera en memoria, implementando la misma interfaz ``BaseMTClient`` que
el cliente MT real.

Idea clave: el path de señal de producción (``build_market_context``,
``SymbolAgent`` , ``validate_trade``…) habla SIEMPRE con el cliente a través de
``BaseMTClient``. Si le damos un cliente que devuelve velas históricas hasta un
cursor y simula órdenes, podemos REUTILIZAR la lógica real de señales sin
duplicarla y medir su rendimiento antes de arriesgar dinero.

Modelo de ejecución (determinista, conservador):
- Orden a mercado: BUY entra a ``ask`` (= close + spread), SELL a ``bid`` (= close).
- SL/TP: se comprueban barra a barra contra el rango (high/low) de la NUEVA vela.
  Si la vela toca el SL, se cierra en el SL; si toca el TP, en el TP. Si una misma
  vela toca AMBOS, se asume SL primero (peor caso).
- Coste: ``commission_per_lot`` (ida+vuelta) por lote; el spread se paga al entrar.
No es un simulador tick a tick: trabaja con velas (H1), suficiente para comparar
estrategias de forma reproducible.
"""
from types import SimpleNamespace
from typing import List, Optional

from clients.base_client import BaseMTClient
from core import indicators as ta


class ReplayClient(BaseMTClient):

    def __init__(self, symbol: str, candles: List[dict], *,
                 point: float = 0.01, digits: int = 2, contract_size: float = 1.0,
                 spread_points: float = 0.0, commission_per_lot: float = 0.0,
                 tick_value: float = 1.0, volume_min: float = 0.01,
                 volume_step: float = 0.01, starting_balance: float = 1000.0,
                 leverage: int = 100):
        """``candles``: lista cronológica de dicts {time, open, high, low, close,
        volume} en H1. El cursor empieza al final del periodo de calentamiento."""
        self.symbol = symbol
        self._candles = candles or []
        self.point = point
        self.digits = digits
        self.contract_size = contract_size
        self.spread = spread_points * point
        self.commission_per_lot = commission_per_lot
        self.tick_value = tick_value
        self.volume_min = volume_min
        self.volume_step = volume_step
        self.leverage = leverage

        self.starting_balance = starting_balance
        self.balance = starting_balance
        self._cursor = 0
        self._positions: List[dict] = []
        self._closed: List[dict] = []
        self._next_ticket = 1
        self._connected = True

    # ----- Cursor / barra actual -----

    def set_cursor(self, i: int):
        self._cursor = max(0, min(i, len(self._candles) - 1))

    @property
    def cursor(self) -> int:
        return self._cursor

    @property
    def at_end(self) -> bool:
        return self._cursor >= len(self._candles) - 1

    def _bar(self) -> Optional[dict]:
        if 0 <= self._cursor < len(self._candles):
            return self._candles[self._cursor]
        return None

    def _price(self) -> float:
        bar = self._bar()
        return float(bar["close"]) if bar else 0.0

    # ----- Avance + liquidación de SL/TP -----

    def step(self) -> Optional[List[dict]]:
        """Avanza una vela y liquida SL/TP de las posiciones abiertas contra el
        rango de la nueva vela. Devuelve la lista de cierres realizados, o None si
        ya no quedan velas (fin del backtest)."""
        if self.at_end:
            return None
        self._cursor += 1
        bar = self._bar()
        realized = []
        for pos in list(self._positions):
            exit_price, reason = self._hit(pos, bar)
            if exit_price is not None:
                realized.append(self._close_pos(pos, exit_price, bar, reason))
        return realized

    def _hit(self, pos: dict, bar: dict):
        """Precio de salida si la vela toca SL o TP (SL primero), o (None, '')."""
        high, low = float(bar["high"]), float(bar["low"])
        sl, tp = pos.get("sl") or 0.0, pos.get("tp") or 0.0
        if pos["direction"] == "BUY":
            if sl and low <= sl:
                return sl, "Stop Loss"
            if tp and high >= tp:
                return tp, "Take Profit"
        else:  # SELL
            if sl and high >= sl:
                return sl, "Stop Loss"
            if tp and low <= tp:
                return tp, "Take Profit"
        return None, ""

    def _close_pos(self, pos: dict, exit_price: float, bar: dict, reason: str,
                   close_volume: float = None) -> dict:
        """Liquida ``pos`` (entera o parcialmente). Con ``close_volume`` menor que el
        volumen abierto, realiza solo esa porción y deja el remanente abierto (cierre
        parcial de la mesa)."""
        full = pos["volume"]
        vol = (full if close_volume is None or close_volume >= full - 1e-9
               else round(close_volume, 6))
        direction = 1 if pos["direction"] == "BUY" else -1
        gross = (exit_price - pos["open_price"]) * direction * vol * self.contract_size
        comm = self.commission_per_lot * vol
        pnl = round(gross - comm, 2)
        self.balance = round(self.balance + pnl, 2)
        if vol < full - 1e-9:
            pos["volume"] = round(full - vol, 6)   # remanente sigue abierto
        else:
            self._positions.remove(pos)
        rec = {
            "ticket": pos["ticket"], "symbol": pos["symbol"], "direction": pos["direction"],
            "volume": vol, "open_price": pos["open_price"], "exit_price": exit_price,
            "pnl": pnl, "commission": round(-comm, 2), "close_reason": reason,
            "open_time": pos.get("open_time"), "close_time": int(bar.get("time", 0)),
            "open_cursor": pos.get("open_cursor"), "close_cursor": self._cursor,
        }
        self._closed.append(rec)
        return rec

    def _floating(self, pos: dict) -> float:
        direction = 1 if pos["direction"] == "BUY" else -1
        return (self._price() - pos["open_price"]) * direction * pos["volume"] * self.contract_size

    @property
    def closed_trades(self) -> List[dict]:
        return list(self._closed)

    def equity(self) -> float:
        return round(self.balance + sum(self._floating(p) for p in self._positions), 2)

    # ----- Interfaz BaseMTClient -----

    def connect(self, login=None, password=None, server="") -> bool:
        self._connected = True
        return True

    def disconnect(self):
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def get_account_info(self) -> Optional[dict]:
        eq = self.equity()
        used = sum(p["open_price"] * p["volume"] * self.contract_size / max(self.leverage, 1)
                   for p in self._positions)
        return {"balance": self.balance, "equity": eq, "free_margin": round(eq - used, 2),
                "margin": round(used, 2), "used_margin": round(used, 2),
                "leverage": self.leverage, "hedging": True, "platform": "replay"}

    def get_symbols(self) -> List[str]:
        return [self.symbol]

    def get_symbol_info(self, symbol: str):
        return SimpleNamespace(
            digits=self.digits, point=self.point, contract_size=self.contract_size,
            trade_contract_size=self.contract_size, volume_min=self.volume_min,
            volume_step=self.volume_step, trade_tick_value=self.tick_value)

    def get_tick(self, symbol: str):
        price = self._price()
        if price <= 0:
            return None
        return SimpleNamespace(ask=round(price + self.spread, self.digits),
                               bid=round(price, self.digits))

    def get_positions(self, symbol: Optional[str] = None) -> List[dict]:
        out = []
        for p in self._positions:
            d = dict(p)
            d["current_price"] = self._price()
            d["profit"] = round(self._floating(p), 2)
            d["type"] = 0 if p["direction"] == "BUY" else 1
            out.append(d)
        return out

    def get_orders(self, symbol: Optional[str] = None) -> List[dict]:
        return []

    def get_ohlcv(self, symbol: str, timeframe: str = "H1", bars: int = 120) -> List[dict]:
        """Velas hasta el cursor (incluido). H1 directo; H4 por agregación de 4 H1."""
        window = self._candles[: self._cursor + 1]
        if timeframe.upper() == "H4":
            window = self._aggregate_h4(window)
        return window[-bars:] if bars else window

    @staticmethod
    def _aggregate_h4(h1: List[dict]) -> List[dict]:
        out = []
        for i in range(0, len(h1) - len(h1) % 4, 4):
            grp = h1[i:i + 4]
            out.append({
                "time": grp[0].get("time", 0),
                "open": grp[0]["open"],
                "high": max(c["high"] for c in grp),
                "low": min(c["low"] for c in grp),
                "close": grp[-1]["close"],
                "volume": sum(c.get("volume", 0) for c in grp),
            })
        return out

    def get_atr(self, symbol: str, period: int = 14) -> float:
        window = self._candles[: self._cursor + 1]
        if len(window) < period + 1:
            return 0.0
        highs = [c["high"] for c in window]
        lows = [c["low"] for c in window]
        closes = [c["close"] for c in window]
        return ta.atr(highs, lows, closes, period) or 0.0

    def get_market_data(self, symbol: str, bars: int = 20) -> str:
        return ""

    def place_order(self, symbol: str, volume: float, order_type: str,
                    price: float = None, stop_loss: float = None,
                    take_profit: float = None, comment: str = "",
                    deviation: int = 10) -> Optional[dict]:
        direction = str(order_type).upper()
        if direction not in ("BUY", "SELL"):
            return {"success": False, "error": f"tipo no soportado: {order_type}"}
        tick = self.get_tick(symbol)
        if not tick:
            return {"success": False, "error": "sin precio"}
        fill = tick.ask if direction == "BUY" else tick.bid
        bar = self._bar() or {}
        pos = {
            "ticket": self._next_ticket, "symbol": symbol, "direction": direction,
            "volume": round(volume, 6), "open_price": fill,
            "sl": stop_loss or 0.0, "tp": take_profit or 0.0,
            "open_time": int(bar.get("time", 0)), "open_cursor": self._cursor,
        }
        self._next_ticket += 1
        self._positions.append(pos)
        return {"success": True, "order": pos["ticket"], "price": fill}

    def close_position(self, symbol: str, direction: str = None,
                       volume: float = None, ticket: int = None) -> Optional[dict]:
        bar = self._bar() or {}
        price = self._price()
        closed_any = False
        for pos in list(self._positions):
            if ticket is not None and pos["ticket"] != ticket:
                continue
            if direction is not None and pos["direction"] != str(direction).upper():
                continue
            if symbol and pos["symbol"] != symbol:
                continue
            self._close_pos(pos, price, bar, "Cierre", close_volume=volume)
            closed_any = True
            if ticket is not None:
                break
        return {"success": closed_any}

    def modify_position(self, symbol: str, ticket: int, stop_loss: float = None,
                        take_profit: float = None) -> Optional[dict]:
        """Mueve SL/TP de una posición abierta (lo usa el trailing stop de la mesa)."""
        for pos in self._positions:
            if pos["ticket"] == ticket and (not symbol or pos["symbol"] == symbol):
                if stop_loss is not None:
                    pos["sl"] = stop_loss
                if take_profit is not None:
                    pos["tp"] = take_profit
                return {"success": True}
        return {"success": False}


def _hit_price(direction: str, sl: float, tp: float, high: float, low: float):
    """Precio de salida si la vela toca SL o TP (SL primero, peor caso)."""
    if direction == "BUY":
        if sl and low <= sl:
            return sl, "Stop Loss"
        if tp and high >= tp:
            return tp, "Take Profit"
    else:  # SELL
        if sl and high >= sl:
            return sl, "Stop Loss"
        if tp and low <= tp:
            return tp, "Take Profit"
    return None, ""


class MultiSymbolReplayClient(BaseMTClient):
    """Como ``ReplayClient`` pero con VARIOS símbolos sobre UNA cuenta (un solo
    balance, posiciones de todos los símbolos) — necesario para el backtest del
    pipeline COORDINADO, donde la mesa razona la cartera entera (exposición total,
    competencia por asignación, grupo correlacionado BTC/ETH).

    Las series por símbolo se alinean POR ÍNDICE (se truncan a la longitud común);
    se asume que vienen muestreadas a la misma rejilla temporal (H1)."""

    def __init__(self, series: dict, infos: dict = None, *,
                 starting_balance: float = 1000.0, leverage: int = 100):
        self.symbols = list(series.keys())
        if not self.symbols:
            raise ValueError("MultiSymbolReplayClient requiere al menos un símbolo")
        n = min(len(c) for c in series.values())
        self._series = {s: list(series[s])[:n] for s in self.symbols}
        self._len = n
        infos = infos or {}
        self._info = {}
        for s in self.symbols:
            i = infos.get(s, {})
            point = float(i.get("point", 0.01) or 0.01)
            self._info[s] = {
                "point": point, "digits": int(i.get("digits", 2)),
                "contract_size": float(i.get("contract_size", 1.0) or 1.0),
                "spread": float(i.get("spread_points", 0.0) or 0.0) * point,
                "commission": float(i.get("commission_per_lot", 0.0) or 0.0),
                "tick_value": float(i.get("tick_value", 1.0) or 1.0),
                "volume_min": float(i.get("volume_min", 0.01) or 0.01),
                "volume_step": float(i.get("volume_step", 0.01) or 0.01),
            }
        self.starting_balance = starting_balance
        self.balance = starting_balance
        self.leverage = leverage
        self._cursor = 0
        self._positions: List[dict] = []
        self._closed: List[dict] = []
        self._next_ticket = 1
        self._connected = True

    # ----- Cursor / barras -----

    def set_cursor(self, i: int):
        self._cursor = max(0, min(i, self._len - 1))

    @property
    def cursor(self) -> int:
        return self._cursor

    @property
    def at_end(self) -> bool:
        return self._cursor >= self._len - 1

    def _bar(self, symbol: str) -> Optional[dict]:
        s = self._series.get(symbol)
        if s and 0 <= self._cursor < len(s):
            return s[self._cursor]
        return None

    def _price(self, symbol: str) -> float:
        bar = self._bar(symbol)
        return float(bar["close"]) if bar else 0.0

    def step(self) -> Optional[List[dict]]:
        if self.at_end:
            return None
        self._cursor += 1
        realized = []
        for pos in list(self._positions):
            bar = self._bar(pos["symbol"])
            if not bar:
                continue
            exit_price, reason = _hit_price(pos["direction"], pos.get("sl") or 0.0,
                                            pos.get("tp") or 0.0,
                                            float(bar["high"]), float(bar["low"]))
            if exit_price is not None:
                realized.append(self._close_pos(pos, exit_price, bar, reason))
        return realized

    def _close_pos(self, pos, exit_price, bar, reason, close_volume=None) -> dict:
        cs = self._info[pos["symbol"]]["contract_size"]
        comm_lot = self._info[pos["symbol"]]["commission"]
        full = pos["volume"]
        vol = (full if close_volume is None or close_volume >= full - 1e-9
               else round(close_volume, 6))
        sign = 1 if pos["direction"] == "BUY" else -1
        gross = (exit_price - pos["open_price"]) * sign * vol * cs
        comm = comm_lot * vol
        pnl = round(gross - comm, 2)
        self.balance = round(self.balance + pnl, 2)
        if vol < full - 1e-9:
            pos["volume"] = round(full - vol, 6)
        else:
            self._positions.remove(pos)
        rec = {
            "ticket": pos["ticket"], "symbol": pos["symbol"], "direction": pos["direction"],
            "volume": vol, "open_price": pos["open_price"], "exit_price": exit_price,
            "pnl": pnl, "commission": round(-comm, 2), "close_reason": reason,
            "open_time": pos.get("open_time"), "close_time": int(bar.get("time", 0)),
            "open_cursor": pos.get("open_cursor"), "close_cursor": self._cursor,
        }
        self._closed.append(rec)
        return rec

    def _floating(self, pos) -> float:
        cs = self._info[pos["symbol"]]["contract_size"]
        sign = 1 if pos["direction"] == "BUY" else -1
        return (self._price(pos["symbol"]) - pos["open_price"]) * sign * pos["volume"] * cs

    @property
    def closed_trades(self) -> List[dict]:
        return list(self._closed)

    def equity(self) -> float:
        return round(self.balance + sum(self._floating(p) for p in self._positions), 2)

    def _used_margin(self) -> float:
        return sum(p["open_price"] * p["volume"] * self._info[p["symbol"]]["contract_size"]
                   / max(self.leverage, 1) for p in self._positions)

    # ----- Interfaz BaseMTClient -----

    def connect(self, login=None, password=None, server="") -> bool:
        self._connected = True
        return True

    def disconnect(self):
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def get_account_info(self) -> Optional[dict]:
        eq = self.equity()
        used = self._used_margin()
        return {"balance": self.balance, "equity": eq, "free_margin": round(eq - used, 2),
                "margin": round(used, 2), "used_margin": round(used, 2),
                "leverage": self.leverage, "hedging": True, "platform": "replay"}

    def get_symbols(self) -> List[str]:
        return list(self.symbols)

    def get_symbol_info(self, symbol: str):
        i = self._info.get(symbol)
        if not i:
            return None
        return SimpleNamespace(
            digits=i["digits"], point=i["point"], contract_size=i["contract_size"],
            trade_contract_size=i["contract_size"], volume_min=i["volume_min"],
            volume_step=i["volume_step"], trade_tick_value=i["tick_value"])

    def get_tick(self, symbol: str):
        price = self._price(symbol)
        if price <= 0:
            return None
        i = self._info[symbol]
        return SimpleNamespace(ask=round(price + i["spread"], i["digits"]),
                               bid=round(price, i["digits"]))

    def get_positions(self, symbol: Optional[str] = None) -> List[dict]:
        out = []
        for p in self._positions:
            if symbol and p["symbol"] != symbol:
                continue
            d = dict(p)
            d["current_price"] = self._price(p["symbol"])
            d["profit"] = round(self._floating(p), 2)
            d["type"] = 0 if p["direction"] == "BUY" else 1
            out.append(d)
        return out

    def get_orders(self, symbol: Optional[str] = None) -> List[dict]:
        return []

    def get_ohlcv(self, symbol: str, timeframe: str = "H1", bars: int = 120) -> List[dict]:
        window = self._series.get(symbol, [])[: self._cursor + 1]
        if timeframe.upper() == "H4":
            window = ReplayClient._aggregate_h4(window)
        return window[-bars:] if bars else window

    def get_atr(self, symbol: str, period: int = 14) -> float:
        window = self._series.get(symbol, [])[: self._cursor + 1]
        if len(window) < period + 1:
            return 0.0
        return ta.atr([c["high"] for c in window], [c["low"] for c in window],
                      [c["close"] for c in window], period) or 0.0

    def get_market_data(self, symbol: str, bars: int = 20) -> str:
        return ""

    def place_order(self, symbol: str, volume: float, order_type: str,
                    price: float = None, stop_loss: float = None,
                    take_profit: float = None, comment: str = "",
                    deviation: int = 10) -> Optional[dict]:
        direction = str(order_type).upper()
        if direction not in ("BUY", "SELL"):
            return {"success": False, "error": f"tipo no soportado: {order_type}"}
        tick = self.get_tick(symbol)
        if not tick:
            return {"success": False, "error": "sin precio"}
        fill = tick.ask if direction == "BUY" else tick.bid
        bar = self._bar(symbol) or {}
        pos = {
            "ticket": self._next_ticket, "symbol": symbol, "direction": direction,
            "volume": round(volume, 6), "open_price": fill,
            "sl": stop_loss or 0.0, "tp": take_profit or 0.0,
            "open_time": int(bar.get("time", 0)), "open_cursor": self._cursor,
        }
        self._next_ticket += 1
        self._positions.append(pos)
        return {"success": True, "order": pos["ticket"], "price": fill}

    def close_position(self, symbol: str, direction: str = None,
                       volume: float = None, ticket: int = None) -> Optional[dict]:
        closed_any = False
        for pos in list(self._positions):
            if ticket is not None and pos["ticket"] != ticket:
                continue
            if direction is not None and pos["direction"] != str(direction).upper():
                continue
            if symbol and pos["symbol"] != symbol:
                continue
            self._close_pos(pos, self._price(pos["symbol"]), self._bar(pos["symbol"]) or {},
                            "Cierre", close_volume=volume)
            closed_any = True
            if ticket is not None:
                break
        return {"success": closed_any}

    def modify_position(self, symbol: str, ticket: int, stop_loss: float = None,
                        take_profit: float = None) -> Optional[dict]:
        for pos in self._positions:
            if pos["ticket"] == ticket and (not symbol or pos["symbol"] == symbol):
                if stop_loss is not None:
                    pos["sl"] = stop_loss
                if take_profit is not None:
                    pos["tp"] = take_profit
                return {"success": True}
        return {"success": False}
