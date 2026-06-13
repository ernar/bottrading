import threading
from typing import Dict, List, Optional
from datetime import datetime
from dataclasses import dataclass, asdict
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
    platform: str = "MT5"


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
                platform=signal_dict.get("platform", "MT5"),
            )
            self.signals[signal.symbol] = signal
            self.last_update = datetime.now().isoformat()

    def update_position(self, symbol: str, position) -> None:
        with self._lock:
            self.positions[symbol] = position
            self.last_update = datetime.now().isoformat()

    def remove_position(self, symbol: str) -> None:
        with self._lock:
            if symbol in self.positions:
                del self.positions[symbol]
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
                if hasattr(v, "model_dump"):
                    positions[k] = v.model_dump()
                elif hasattr(v, "dict"):
                    positions[k] = v.dict()
                else:
                    positions[k] = asdict(v)
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
