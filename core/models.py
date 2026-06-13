from pydantic import BaseModel
from typing import Optional, List
from enum import Enum


class OrderType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    BUY_LIMIT = "BUY_LIMIT"
    SELL_LIMIT = "SELL_LIMIT"
    BUY_STOP = "BUY_STOP"
    SELL_STOP = "SELL_STOP"


class SymbolInfo(BaseModel):
    name: str
    description: str
    point: float
    digits: int
    spread: float
    contract_size: float


class Position(BaseModel):
    symbol: str
    ticket: Optional[int] = None
    direction: OrderType
    volume: float
    open_price: float
    current_price: float
    profit: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None


class PendingOrder(BaseModel):
    symbol: str
    order_type: OrderType
    volume: float
    price: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    comment: str = ""


class TradeRequest(BaseModel):
    action: OrderType
    symbol: str
    volume: float
    price: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    comment: str = ""
    deviation: int = 10


class BotConfig(BaseModel):
    api_key: str = ""
    model: str = "qwen3:8b"
    symbols: List[str] = ["EURUSD", "GBPUSD", "XAUUSD"]
    default_lot_size: float = 0.01
    max_spread_filter: float = 2.0
    risk_per_trade: float = 0.02
    max_open_positions: int = 5
    # Por encima de esta confianza, una señal se salta el límite de posiciones
    # abiertas (cuenta máxima y no-duplicar dirección). 1.0 = nunca saltar.
    max_pos_override_confidence: float = 0.90
    debug_mode: bool = False
    commission_per_lot: float = 7.0  # Comisión en $ por lote
