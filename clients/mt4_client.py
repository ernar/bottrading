import os
import time
from typing import Optional, List
from datetime import datetime
from clients.base_client import BaseMTClient


class SymbolInfo:
    """Imita la estructura de symbol_info."""
    def __init__(self, data: dict):
        self.name = data.get("symbol", "")
        self.point = float(data.get("point", 0.00001))
        self.digits = int(data.get("digits", 5))
        self.spread = float(data.get("spread", 0))
        self.trade_tick_value = float(data.get("tick_value", 1.0))
        self.volume_min = float(data.get("lot_min", 0.01))
        self.volume_max = float(data.get("lot_max", 100.0))
        self.volume_step = float(data.get("lot_step", 0.01))


class TickInfo:
    """Imita la estructura de symbol_info_tick."""
    def __init__(self, bid: float, ask: float, time: int):
        self.bid = bid
        self.ask = ask
        self.time = time


class MT4Client(BaseMTClient):
    """
    Bridge con MT4 via archivos en MQL4/Files/.
    El EA PythonBridge.mq4 debe estar corriendo en MT4.
    """

    def __init__(self, files_path: str = None, timeout: float = 10.0):
        # Ruta a MQL4/Files/ del terminal MT4
        if files_path is None:
            appdata = os.environ.get("APPDATA", "")
            mq_base = os.path.join(appdata, "MetaQuotes", "Terminal")
            # Buscar el primer terminal MT4 que tenga MQL4/Files
            files_path = self._find_files_dir(mq_base)
        self._files_path = files_path
        self._timeout = timeout
        self._connected = False

        self._cmd_file  = os.path.join(files_path, "pb_cmd.txt")
        self._resp_file = os.path.join(files_path, "pb_resp.txt")
        self._lock_file = os.path.join(files_path, "pb_lock.txt")

        # Comisión por lote aprendida de operaciones reales (símbolo -> $/lote).
        # Se actualiza como efecto colateral de get_positions().
        self._commission_cache: dict = {}

    def _find_files_dir(self, mq_base: str) -> str:
        """Busca automáticamente la carpeta MQL4/Files del primer terminal MT4."""
        if not os.path.isdir(mq_base):
            return ""
        for terminal_id in os.listdir(mq_base):
            candidate = os.path.join(mq_base, terminal_id, "MQL4", "Files")
            if os.path.isdir(candidate):
                return candidate
        return ""

    def _send(self, command: str, timeout: float = None) -> str:
        if not self._files_path:
            return "ERROR|MQL4/Files path not found"

        wait = timeout if timeout is not None else self._timeout

        # Borrar respuesta previa
        if os.path.exists(self._resp_file):
            os.remove(self._resp_file)

        # Escribir comando
        with open(self._cmd_file, "w", encoding="ascii") as f:
            f.write(command)

        # Esperar respuesta
        deadline = time.time() + wait
        while time.time() < deadline:
            if os.path.exists(self._resp_file) and not os.path.exists(self._lock_file):
                try:
                    with open(self._resp_file, "r", encoding="ascii") as f:
                        return f.read().strip()
                except Exception:
                    pass
            time.sleep(0.05)

        # Timeout — limpiar
        if os.path.exists(self._cmd_file):
            os.remove(self._cmd_file)
        return "ERROR|timeout waiting for EA response"

    def _parse_kv(self, payload: str) -> dict:
        result = {}
        for part in payload.split("|"):
            if "=" in part:
                k, v = part.split("=", 1)
                result[k.strip()] = v.strip()
        return result

    # ------------------------------------------------------------------
    def connect(self, login: int = None, password: str = None, server: str = "") -> bool:
        response = self._send("PING")
        self._connected = response == "PONG"
        return self._connected

    def disconnect(self):
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def get_account_info(self) -> Optional[dict]:
        resp = self._send("ACCOUNT_INFO")
        if not resp.startswith("OK|"):
            return None
        data = self._parse_kv(resp[3:])
        return {
            "login": int(data.get("login", 0)),
            "balance": float(data.get("balance", 0)),
            "equity": float(data.get("equity", 0)),
            "used_margin": float(data.get("margin", 0)),
            "free_margin": float(data.get("free_margin", 0)),
            "margin_level": float(data.get("margin_level", 0)),
            "profit": float(data.get("profit", 0)),
            "leverage": int(data.get("leverage", 0)),
            "currency": data.get("currency", ""),
            "broker": data.get("broker", ""),
            # MT4 siempre permite posiciones opuestas a la vez (cobertura real).
            "hedging": True,
            "platform": "MT4",
        }

    def get_symbols(self) -> List[str]:
        resp = self._send("SYMBOLS")
        if not resp.startswith("OK|"):
            return []
        return [s for s in resp[3:].split(",") if s]

    def get_symbol_info(self, symbol: str) -> Optional[SymbolInfo]:
        resp = self._send(f"SYMBOL_INFO|{symbol}")
        if not resp.startswith("OK|"):
            return None
        return SymbolInfo(self._parse_kv(resp[3:]))

    def get_tick(self, symbol: str) -> Optional[TickInfo]:
        resp = self._send(f"TICK|{symbol}")
        if not resp.startswith("OK|"):
            return None
        data = self._parse_kv(resp[3:])
        return TickInfo(
            bid=float(data.get("bid", 0)),
            ask=float(data.get("ask", 0)),
            time=int(data.get("time", 0)),
        )

    def get_positions(self, symbol: Optional[str] = None) -> List[dict]:
        cmd = f"POSITIONS|{symbol}" if symbol else "POSITIONS"
        resp = self._send(cmd)
        if not resp.startswith("OK|"):
            return []
        payload = resp[3:]
        if not payload:
            return []
        positions = []
        for entry in payload.split(";"):
            if not entry:
                continue
            data = self._parse_kv(entry.replace(",", "|"))
            positions.append(data)
        self._learn_commission(positions)
        return positions

    def _learn_commission(self, positions: List[dict]):
        """Deduce la comisión por lote de las posiciones reportadas y la cachea
        por símbolo: |comisión total| / volumen total del símbolo. Robusto frente
        a varias posiciones del mismo símbolo. El EA debe reportar `commission`
        (PythonBridge.mq4 >= versión con OrderCommission)."""
        agg: dict = {}  # symbol -> [comisión acumulada, volumen acumulado]
        for p in positions:
            sym = p.get("symbol")
            if not sym or "commission" not in p:
                continue
            try:
                comm = abs(float(p.get("commission", 0)))
                vol = float(p.get("volume", 0))
            except (ValueError, TypeError):
                continue
            if vol <= 0:
                continue
            acc = agg.setdefault(sym, [0.0, 0.0])
            acc[0] += comm
            acc[1] += vol
        for sym, (comm, vol) in agg.items():
            if vol > 0:
                self._commission_cache[sym] = round(comm / vol, 4)

    def get_commission_per_lot(self, symbol: str) -> Optional[float]:
        return self._commission_cache.get(symbol)

    def get_orders(self, symbol: Optional[str] = None) -> List[dict]:
        resp = self._send("ORDERS")
        if not resp.startswith("OK|"):
            return []
        payload = resp[3:]
        if not payload:
            return []
        orders = []
        for entry in payload.split(";"):
            if not entry:
                continue
            data = self._parse_kv(entry.replace(",", "|"))
            orders.append(data)
        return orders

    def get_atr(self, symbol: str, period: int = 14) -> float:
        resp = self._send(f"OHLCV|{symbol}|{period + 1}")
        if not resp.startswith("OK|"):
            return 0.0
        candles = resp[3:].split(";")
        if len(candles) < 2:
            return 0.0
        trs = []
        prev_close = None
        for candle in candles:
            parts = candle.split(",")
            if len(parts) < 5:
                continue
            high = float(parts[2])
            low = float(parts[3])
            close = float(parts[4])
            if prev_close is not None:
                tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
                trs.append(tr)
            prev_close = close
        return sum(trs) / len(trs) if trs else 0.0

    def get_ohlcv(self, symbol: str, timeframe: str = "H1", bars: int = 120) -> List[dict]:
        # El EA bridge solo entrega velas H1 (comando OHLCV sin timeframe)
        if timeframe.upper() != "H1":
            return []
        resp = self._send(f"OHLCV|{symbol}|{bars}")
        if not resp.startswith("OK|"):
            return []
        result = []
        for candle in resp[3:].split(";"):
            parts = candle.split(",")
            if len(parts) < 6:
                continue
            try:
                result.append({
                    "time": int(parts[0]),
                    "open": float(parts[1]),
                    "high": float(parts[2]),
                    "low": float(parts[3]),
                    "close": float(parts[4]),
                    "volume": int(float(parts[5])),
                })
            except ValueError:
                continue
        return result

    def get_market_data(self, symbol: str, bars: int = 100) -> str:
        resp = self._send(f"OHLCV|{symbol}|{bars}")
        tick = self.get_tick(symbol)
        lines = [f"Symbol: {symbol}"]
        if tick:
            lines.append(f"Current: Ask={tick.ask} Bid={tick.bid}")
        lines += ["", f"OHLCV H1 (last {bars} candles):", "time,open,high,low,close,volume"]
        if resp.startswith("OK|"):
            for candle in resp[3:].split(";"):
                parts = candle.split(",")
                if len(parts) >= 6:
                    dt = datetime.fromtimestamp(int(parts[0])).strftime("%Y-%m-%d %H:%M")
                    lines.append(f"{dt},{parts[1]},{parts[2]},{parts[3]},{parts[4]},{parts[5]}")
        return "\n".join(lines)

    def place_order(self, symbol: str, volume: float, order_type: str,
                    price: float = None, stop_loss: float = None,
                    take_profit: float = None, comment: str = "",
                    deviation: int = 10) -> Optional[dict]:
        price = price or 0
        sl = stop_loss or 0
        tp = take_profit or 0
        cmd = f"PLACE_ORDER|{symbol}|{order_type.upper()}|{volume}|{price}|{sl}|{tp}|{comment}"
        # Las órdenes implican un viaje al servidor del broker: timeout amplio.
        resp = self._send(cmd, timeout=30.0)
        if resp.startswith("OK|"):
            data = self._parse_kv(resp[3:])
            return {"success": True, "retcode": 0,
                    "order": int(data.get("ticket", 0)),
                    "price": float(data.get("price", 0))}
        # timeout == estado desconocido: la orden PUEDE haberse ejecutado igualmente.
        return {"success": False, "error": resp, "timeout": "timeout" in resp}

    def close_position(self, symbol: str, direction: str = None) -> Optional[dict]:
        resp = self._send(f"CLOSE_POSITION|{symbol}", timeout=30.0)
        if resp.startswith("OK|"):
            return {"success": True, "retcode": 0, "comment": resp[3:]}
        # Sin posición abierta: None para que el API devuelva 404.
        if "no open position" in resp:
            return None
        return {"success": False, "error": resp, "timeout": "timeout" in resp}
