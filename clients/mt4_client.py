import os
import time
import threading
import unicodedata
from typing import Optional, List
from datetime import datetime
from clients.base_client import BaseMTClient


def _ascii_comment(text: str) -> str:
    """Normaliza el comentario de la orden a ASCII legible.

    El canal de archivos del EA es ASCII; los comentarios se arman con la razón
    del LLM (español con ñ/acentos). En vez de sustituir por "?" (lo que daría
    "se?ales"), descompone los acentos y conserva la letra base ("señales" →
    "senales"). Cualquier resto fuera de ASCII se descarta.
    """
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    return decomposed.encode("ascii", "ignore").decode("ascii")


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
        # Margen (moneda de la cuenta) por 1 lote. EA >= versión con
        # MODE_MARGINREQUIRED; 0.0 si el EA es antiguo (entonces se estima).
        self.margin_required = float(data.get("margin_required", 0.0))
        # El broker pone MODE_TRADEALLOWED a 0 con el mercado cerrado. Si el EA
        # es antiguo y no lo reporta, asumimos abierto (1) para no romper nada.
        self.trade_allowed = bool(int(float(data.get("trade_allowed", 1))))


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

        # El bridge usa UN ÚNICO canal de archivos (pb_cmd/pb_resp): si dos hilos
        # envían comandos a la vez se pisan la respuesta. Este lock serializa cada
        # roundtrip, lo que permite analizar agentes en paralelo (las llamadas al
        # LLM se solapan; los accesos al EA hacen una cola breve). Ver orchestrator.
        self._send_lock = threading.Lock()

    def _find_files_dir(self, mq_base: str) -> str:
        """Busca automáticamente la carpeta MQL4/Files del primer terminal MT4."""
        if not os.path.isdir(mq_base):
            return ""
        for terminal_id in os.listdir(mq_base):
            candidate = os.path.join(mq_base, terminal_id, "MQL4", "Files")
            if os.path.isdir(candidate):
                return candidate
        return ""

    def _safe_remove(self, path: str, retries: int = 20, delay: float = 0.05) -> bool:
        """Borra un archivo del bridge tolerando que el EA lo tenga abierto.

        En Windows, `os.remove` lanza PermissionError [WinError 32] si MetaTrader
        mantiene un handle sobre el archivo en ese instante (está escribiendo la
        respuesta). Reintentamos brevemente en vez de dejar reventar el roundtrip
        entero. Devuelve True si el archivo ya no existe al terminar."""
        for _ in range(retries):
            if not os.path.exists(path):
                return True
            try:
                os.remove(path)
                return True
            except FileNotFoundError:
                return True
            except PermissionError:
                # El EA lo tiene abierto: esperamos a que suelte el handle.
                time.sleep(delay)
        return not os.path.exists(path)

    def _send(self, command: str, timeout: float = None) -> str:
        if not self._files_path:
            return "ERROR|MQL4/Files path not found"

        wait = timeout if timeout is not None else self._timeout

        # Serializa el roundtrip completo: canal único compartido con el EA.
        with self._send_lock:
            # Borrar respuesta previa (tolerando que el EA aún la tenga abierta).
            if not self._safe_remove(self._resp_file):
                return "ERROR|no se pudo limpiar pb_resp.txt (EA ocupado)"

            # Escribir comando. `errors="replace"`: el comentario de la orden se
            # arma con la razón del LLM (texto en español con ñ/acentos) y el
            # canal de archivos del EA es ASCII; un glifo fuera de rango NO debe
            # tumbar el loop — se sustituye por "?" en vez de lanzar UnicodeEncodeError.
            with open(self._cmd_file, "w", encoding="ascii", errors="replace") as f:
                f.write(command)

            # Esperar respuesta
            deadline = time.time() + wait
            while time.time() < deadline:
                if os.path.exists(self._resp_file) and not os.path.exists(self._lock_file):
                    try:
                        with open(self._resp_file, "r", encoding="ascii", errors="replace") as f:
                            return f.read().strip()
                    except (PermissionError, OSError):
                        # El EA aún está escribiendo: reintentamos en el próximo giro.
                        pass
                time.sleep(0.05)

            # Timeout — limpiar
            self._safe_remove(self._cmd_file)
            return "ERROR|timeout waiting for EA response"

    def _parse_kv(self, payload: str) -> dict:
        result = {}
        for part in payload.split("|"):
            if "=" in part:
                k, v = part.split("=", 1)
                result[k.strip()] = v.strip()
        return result

    # Orden de columnas de POSITIONS/ORDERS en el EA (PythonBridge.mq4). Se usa
    # como fallback posicional cuando el EA cargado en MT4 está desactualizado y
    # devuelve CSV plano sin `clave=` (entonces _parse_kv daría {} y se perderían
    # todos los campos). El layout actual lleva commission/swap; el legacy no.
    _POSITION_FIELDS_12 = ["ticket", "symbol", "type", "volume", "open_price",
                           "sl", "tp", "profit", "commission", "swap",
                           "open_time", "comment"]
    _POSITION_FIELDS_10 = ["ticket", "symbol", "type", "volume", "open_price",
                           "sl", "tp", "profit", "open_time", "comment"]
    _ORDER_FIELDS_9 = ["ticket", "symbol", "type", "volume", "price",
                       "sl", "tp", "open_time", "comment"]

    def _parse_record(self, entry: str, positional_fields: list) -> dict:
        """Parsea una posición/orden tolerando dos formatos del EA:

        1) `clave=valor` separado por comas (EA actual) — vía _parse_kv.
        2) CSV plano sin claves (EA antiguo/desincronizado) — mapeo posicional
           contra `positional_fields`. El último campo (`comment`) puede contener
           comas, así que se absorbe el resto.
        """
        kv = self._parse_kv(entry.replace(",", "|"))
        if kv:
            return kv
        parts = entry.split(",")
        if not parts or not parts[0]:
            return {}
        result = {}
        last = len(positional_fields) - 1
        for idx, field in enumerate(positional_fields):
            if idx < last and idx < len(parts):
                result[field] = parts[idx].strip()
            elif idx == last and len(parts) > last:
                # `comment` final: reabsorbe cualquier coma interna.
                result[field] = ",".join(parts[last:]).strip()
        return result

    def _position_fields(self, entry: str) -> list:
        """Elige el layout posicional según el número de columnas del CSV."""
        n = len(entry.split(","))
        return self._POSITION_FIELDS_12 if n >= 12 else self._POSITION_FIELDS_10

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

    def is_market_open(self, symbol: str) -> bool:
        """True si el mercado del símbolo está abierto para operar.

        Se apoya en MODE_TRADEALLOWED del broker (reportado por el EA): inmune a
        la zona horaria. Fail-safe: si no se puede consultar el símbolo, asume
        abierto para no bloquear el bot por un fallo puntual del bridge.
        """
        info = self.get_symbol_info(symbol)
        if info is None:
            return True
        return getattr(info, "trade_allowed", True)

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
        tick_cache: dict = {}  # symbol -> precio actual (1 roundtrip por símbolo)
        for entry in payload.split(";"):
            if not entry:
                continue
            data = self._parse_record(entry, self._position_fields(entry))
            self._coerce_position_numbers(data)
            self._normalize_position(data, tick_cache)
            positions.append(data)
        self._learn_commission(positions)
        return positions

    # Campos numéricos de una posición: el parsing del EA los devuelve como
    # strings, pero el estado/serialización y el frontend (p. ej. volume.toFixed)
    # esperan números. Se convierten aquí, en el origen, de forma tolerante.
    _POSITION_NUMERIC_FIELDS = ("volume", "open_price", "sl", "tp",
                                "profit", "commission", "swap")

    def _coerce_position_numbers(self, data: dict) -> None:
        for field in self._POSITION_NUMERIC_FIELDS:
            if field in data:
                try:
                    data[field] = float(data[field])
                except (ValueError, TypeError):
                    pass

    def _normalize_position(self, data: dict, tick_cache: dict) -> None:
        """Añade los alias que espera el frontend (Position) sin quitar las
        claves originales del EA, que usan orquestador/coordinador (`type`,
        `sl`, `tp`). Así la pestaña Positions deja de romper por campos ausentes.

        - `direction`     ← `type` (BUY/SELL en mayúsculas)
        - `current_price` ← tick actual del símbolo (bid/ask según el lado),
                            con fallback a `open_price`
        - `stop_loss`/`take_profit` ← `sl`/`tp`"""
        raw_type = str(data.get("type", "")).upper()
        # El EA puede mandar el tipo como número (0=BUY, 1=SELL) o como texto.
        if raw_type in ("0", "BUY"):
            direction = "BUY"
        elif raw_type in ("1", "SELL"):
            direction = "SELL"
        else:
            direction = raw_type or "BUY"
        data["direction"] = direction

        symbol = data.get("symbol")
        if symbol and symbol not in tick_cache:
            tick = self.get_tick(symbol)
            # Al cerrar, un BUY se valora a bid y un SELL a ask; usamos el lado
            # de salida para que el P/L mostrado sea coherente.
            tick_cache[symbol] = tick if tick else None
        tick = tick_cache.get(symbol)
        if tick:
            data["current_price"] = tick.bid if direction == "BUY" else tick.ask
        else:
            data["current_price"] = data.get("open_price", 0.0)

        data.setdefault("stop_loss", data.get("sl", 0.0))
        data.setdefault("take_profit", data.get("tp", 0.0))

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
            data = self._parse_record(entry, self._ORDER_FIELDS_9)
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
        cmd = f"PLACE_ORDER|{symbol}|{order_type.upper()}|{volume}|{price}|{sl}|{tp}|{_ascii_comment(comment)}"
        # Las órdenes implican un viaje al servidor del broker: timeout amplio.
        resp = self._send(cmd, timeout=30.0)
        if resp.startswith("OK|"):
            data = self._parse_kv(resp[3:])
            return {"success": True, "retcode": 0,
                    "order": int(data.get("ticket", 0)),
                    "price": float(data.get("price", 0))}
        # timeout == estado desconocido: la orden PUEDE haberse ejecutado igualmente.
        return {"success": False, "error": resp, "timeout": "timeout" in resp}

    def close_position(self, symbol: str, direction: str = None,
                       volume: float = None, ticket: int = None) -> Optional[dict]:
        """Cierra una posición del símbolo. Con `volume`/`ticket` realiza un cierre
        PARCIAL del ticket indicado (MT4 deja el remanente en un ticket nuevo); sin
        ellos, cierra la primera posición del símbolo completa (comportamiento
        previo). `direction` lo ignora MT4 (best-effort vía el EA)."""
        cmd = f"CLOSE_POSITION|{symbol}"
        if volume or ticket:
            # El EA acepta CLOSE_POSITION|symbol|volume|ticket (campos opcionales).
            cmd += f"|{volume or 0}|{int(ticket or 0)}"
        resp = self._send(cmd, timeout=30.0)
        if resp.startswith("OK|"):
            return {"success": True, "retcode": 0, "comment": resp[3:]}
        # Sin posición abierta: None para que el API devuelva 404.
        if "no open position" in resp:
            return None
        return {"success": False, "error": resp, "timeout": "timeout" in resp}

    def modify_position(self, symbol: str, ticket: int, stop_loss: float = None,
                        take_profit: float = None) -> Optional[dict]:
        """Mueve el SL/TP de una posición abierta vía el EA (OrderModify). Un nivel
        en None/0 deja el actual sin cambios. Devuelve {success, ...} o None ante
        error de selección. Usado por el trailing stop del orquestador."""
        sl = stop_loss or 0
        tp = take_profit or 0
        resp = self._send(f"MODIFY_POSITION|{int(ticket)}|{sl}|{tp}", timeout=30.0)
        if resp.startswith("OK|"):
            return {"success": True, "retcode": 0, "comment": resp[3:]}
        return {"success": False, "error": resp, "timeout": "timeout" in resp}
