import MetaTrader5 as mt5
from typing import Optional, List
from datetime import datetime
from clients.base_client import BaseMTClient
from core.models import Position



class MT5Client(BaseMTClient):

    def __init__(self):
        self._connected = False

    def connect(self, login: int = None, password: str = None, server: str = "") -> bool:
        if mt5.initialize():
            if login:
                self._connected = mt5.login(login, password=password, server=server)
            else:
                self._connected = mt5.login()
            return self._connected
        return False

    def disconnect(self):
        mt5.shutdown()
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def get_account_info(self) -> Optional[dict]:
        account = mt5.account_info()
        if account is None:
            return None
        return {
            "login": account.login,
            "balance": account.balance,
            "equity": account.equity,
            "used_margin": account.margin,
            "free_margin": account.margin_free,
            "margin_level": account.margin_level,
            "profit": account.profit,
            "leverage": account.leverage,
            "currency": account.currency,
            "platform": "MT5",
        }

    def get_symbols(self) -> List[str]:
        symbols = mt5.symbols_get()
        if symbols is None:
            return []
        return [s.name for s in symbols]

    def get_symbol_info(self, symbol: str):
        return mt5.symbol_info(symbol)

    def get_tick(self, symbol: str):
        return mt5.symbol_info_tick(symbol)

    def get_positions(self, symbol: Optional[str] = None) -> List[Position]:
        raw = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        if not raw:
            return []
        result = []
        for p in raw:
            direction = "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL"
            result.append(Position(
                symbol=p.symbol,
                ticket=p.ticket,
                direction=direction,
                volume=p.volume,
                open_price=p.price_open,
                current_price=p.price_current,
                profit=p.profit,
                stop_loss=p.sl if p.sl else None,
                take_profit=p.tp if p.tp else None,
            ))
        return result

    def get_orders(self, symbol: Optional[str] = None) -> List:
        orders = mt5.orders_get(symbol=symbol) if symbol else mt5.orders_get()
        return list(orders) if orders else []

    def get_atr(self, symbol: str, period: int = 14) -> float:
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, period + 1)
        if rates is None or len(rates) < 2:
            return 0.0
        trs = []
        for i in range(1, len(rates)):
            high = rates[i]["high"]
            low = rates[i]["low"]
            prev_close = rates[i - 1]["close"]
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            trs.append(tr)
        return sum(trs) / len(trs) if trs else 0.0

    _TIMEFRAMES = {
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
    }

    def get_ohlcv(self, symbol: str, timeframe: str = "H1", bars: int = 120) -> List[dict]:
        tf = self._TIMEFRAMES.get(timeframe.upper())
        if tf is None:
            return []
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars)
        if rates is None:
            return []
        return [
            {
                "time": int(r["time"]),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": int(r["tick_volume"]),
            }
            for r in rates
        ]

    def get_market_data(self, symbol: str, bars: int = 100) -> str:
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, bars)
        tick = mt5.symbol_info_tick(symbol)
        lines = [f"Symbol: {symbol}"]
        if tick:
            lines.append(f"Current: Ask={tick.ask} Bid={tick.bid}")
        lines += ["", f"OHLCV H1 (last {bars} candles):", "time,open,high,low,close,volume"]
        if rates is not None:
            for r in rates:
                dt = datetime.fromtimestamp(r["time"]).strftime("%Y-%m-%d %H:%M")
                lines.append(f"{dt},{r['open']},{r['high']},{r['low']},{r['close']},{int(r['tick_volume'])}")
        return "\n".join(lines)

    def place_order(self, symbol: str, volume: float, order_type: str,
                    price: float = None, stop_loss: float = None,
                    take_profit: float = None, comment: str = "", deviation: int = 10) -> Optional[dict]:
        order_type = order_type.upper()
        sym_info = mt5.symbol_info(symbol)
        if price is None:
            if order_type in ("BUY", "BUY_LIMIT", "BUY_STOP"):
                price = mt5.symbol_info_tick(symbol).ask
            else:
                price = mt5.symbol_info_tick(symbol).bid
            price = round(price, sym_info.digits)

        action_map = {
            "BUY": mt5.ORDER_TYPE_BUY,
            "SELL": mt5.ORDER_TYPE_SELL,
            "BUY_LIMIT": mt5.ORDER_TYPE_BUY_LIMIT,
            "SELL_LIMIT": mt5.ORDER_TYPE_SELL_LIMIT,
            "BUY_STOP": mt5.ORDER_TYPE_BUY_STOP,
            "SELL_STOP": mt5.ORDER_TYPE_SELL_STOP,
        }
        action = action_map.get(order_type)
        if action is None:
            raise ValueError(f"Tipo de orden no soportado: {order_type}")

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": action,
            "price": price,
            "deviation": deviation,
            "magic": 234000,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        if stop_loss:
            request["sl"] = stop_loss
        if take_profit:
            request["tp"] = take_profit

        result = mt5.order_send(request)
        if result is None:
            return {"success": False, "error": f"order_send devolvió None ({mt5.last_error()})"}
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return {"success": False, "retcode": result.retcode,
                    "comment": result.comment, "error": result.comment}
        return {"success": True, "retcode": result.retcode, "order": result.order,
                "price": result.price, "comment": result.comment}

    def close_position(self, symbol: str, direction: str = None) -> Optional[dict]:
        raw = mt5.positions_get(symbol=symbol)
        if not raw:
            return None

        for position in raw:
            pos_dir = "BUY" if position.type == mt5.POSITION_TYPE_BUY else "SELL"
            if direction and pos_dir != direction:
                continue

            tick = mt5.symbol_info_tick(symbol)
            if position.type == mt5.POSITION_TYPE_BUY:
                close_price = tick.bid
                order_type = mt5.ORDER_TYPE_SELL
            else:
                close_price = tick.ask
                order_type = mt5.ORDER_TYPE_BUY

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": position.volume,
                "type": order_type,
                "position": position.ticket,
                "price": close_price,
                "deviation": 10,
                "magic": 234000,
                "comment": "Close position",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            result = mt5.order_send(request)
            if result is None:
                return {"success": False, "error": f"order_send devolvió None ({mt5.last_error()})"}
            success = result.retcode == mt5.TRADE_RETCODE_DONE
            return {"success": success, "retcode": result.retcode,
                    "comment": result.comment, "error": None if success else result.comment}

        return None
