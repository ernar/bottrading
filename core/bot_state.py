import threading
from typing import Dict, List, Optional
from datetime import datetime
from dataclasses import dataclass, asdict, is_dataclass
from core.models import Position


@dataclass
class Signal:
    symbol: str
    trend: str
    action: str
    confidence: float
    entry: float
    stop_loss: float
    take_profit: float
    risk_level: str
    reason: str
    timestamp: str
    platform: str = "MT4"
    agent: str = ""


@dataclass
class Trade:
    symbol: str
    action: str
    entry_price: float
    exit_price: Optional[float]
    volume: float
    pnl: float
    open_time: str
    close_time: Optional[str]
    duration_seconds: Optional[int]


@dataclass
class AccountInfo:
    balance: float
    equity: float
    free_margin: float
    used_margin: float
    margin_level: float
    leverage: int
    platform: str = "MT4"


class BotState:
    def __init__(self):
        self._lock = threading.RLock()
        self.signals: Dict[str, Signal] = {}
        self.positions: Dict[str, Position] = {}
        self.closed_trades: List[Trade] = []
        self.account_info: Optional[AccountInfo] = None
        self.bot_running = False
        self.connected = False
        self.last_update = datetime.now().isoformat()

    def update_signal(self, signal_dict: dict) -> None:
        with self._lock:
            signal = Signal(
                symbol=signal_dict["symbol"],
                trend=signal_dict["trend"],
                action=signal_dict["action"],
                confidence=signal_dict["confidence"],
                entry=signal_dict["entry"],
                stop_loss=signal_dict["stop_loss"],
                take_profit=signal_dict["take_profit"],
                risk_level=signal_dict["risk_level"],
                reason=signal_dict["reason"],
                timestamp=datetime.now().isoformat(),
                platform=signal_dict.get("platform", "MT4"),
                agent=signal_dict.get("agent", ""),
            )
            self.signals[signal.symbol] = signal
            self.last_update = datetime.now().isoformat()

    @staticmethod
    def _pos_field(position, field: str):
        """Lee un campo de una posición sea Position (pydantic) o dict (MT4)."""
        if isinstance(position, dict):
            return position.get(field)
        return getattr(position, field, None)

    def _pos_key(self, symbol: str, position) -> str:
        """Clave única de la posición: ticket si existe, si no símbolo+índice.

        Keyear por símbolo colapsaba varias posiciones del mismo símbolo en una
        sola; el ticket es único por posición."""
        ticket = self._pos_field(position, "ticket")
        return str(ticket) if ticket else symbol

    def sync_positions(self, symbol: str, positions: list) -> None:
        """Reemplaza TODAS las posiciones de `symbol` por las actuales.

        Así se reflejan varias posiciones abiertas del mismo símbolo y
        desaparecen las que ya se cerraron, en una sola operación atómica."""
        with self._lock:
            self.positions = {
                k: v for k, v in self.positions.items()
                if self._pos_field(v, "symbol") != symbol
            }
            for position in positions or []:
                self.positions[self._pos_key(symbol, position)] = position
            self.last_update = datetime.now().isoformat()

    def update_position(self, symbol: str, position) -> None:
        with self._lock:
            self.positions[self._pos_key(symbol, position)] = position
            self.last_update = datetime.now().isoformat()

    def remove_position(self, symbol: str) -> None:
        """Quita del estado las posiciones del símbolo dado (se resincroniza
        en el siguiente ciclo del orquestador)."""
        with self._lock:
            keys = [k for k, v in self.positions.items()
                    if self._pos_field(v, "symbol") == symbol]
            for k in keys:
                del self.positions[k]
            if keys:
                self.last_update = datetime.now().isoformat()

    def add_closed_trade(self, trade: Trade) -> None:
        with self._lock:
            self.closed_trades.append(trade)
            self.last_update = datetime.now().isoformat()

    def update_account(self, account_dict: dict) -> None:
        with self._lock:
            self.account_info = AccountInfo(
                balance=account_dict.get("balance", 0),
                equity=account_dict.get("equity", 0),
                free_margin=account_dict.get("free_margin", 0),
                used_margin=account_dict.get("used_margin", 0),
                margin_level=account_dict.get("margin_level", 0),
                leverage=account_dict.get("leverage", 1),
                platform=account_dict.get("platform", "MT4"),
            )
            self.last_update = datetime.now().isoformat()

    def set_bot_running(self, running: bool) -> None:
        with self._lock:
            self.bot_running = running
            self.last_update = datetime.now().isoformat()

    def set_connected(self, connected: bool) -> None:
        with self._lock:
            self.connected = connected
            self.last_update = datetime.now().isoformat()

    def get_state(self) -> dict:
        with self._lock:
            positions = {}
            for k, v in self.positions.items():
                # MT4 devuelve dicts planas. Soportamos dataclasses sin reventar.
                if isinstance(v, dict):
                    positions[k] = v
                elif hasattr(v, "model_dump"):
                    positions[k] = v.model_dump()
                elif hasattr(v, "dict"):
                    positions[k] = v.dict()
                elif is_dataclass(v):
                    positions[k] = asdict(v)
                else:
                    positions[k] = v
            return {
                "signals": {k: asdict(v) for k, v in self.signals.items()},
                "positions": positions,
                "closed_trades": [asdict(t) for t in self.closed_trades],
                "account_info": asdict(self.account_info) if self.account_info else None,
                "bot_running": self.bot_running,
                "connected": self.connected,
                "last_update": self.last_update,
            }

    def clear_session(self) -> None:
        with self._lock:
            self.signals.clear()
            self.positions.clear()
            self.closed_trades.clear()
            self.account_info = None
            self.last_update = datetime.now().isoformat()
