from abc import ABC, abstractmethod
from typing import Optional, List


class BaseMTClient(ABC):

    @abstractmethod
    def connect(self, login: int = None, password: str = None, server: str = "") -> bool: ...

    @abstractmethod
    def disconnect(self): ...

    @property
    @abstractmethod
    def is_connected(self) -> bool: ...

    @abstractmethod
    def get_account_info(self) -> Optional[dict]: ...

    @abstractmethod
    def get_symbols(self) -> List[str]: ...

    @abstractmethod
    def get_symbol_info(self, symbol: str) -> Optional[object]: ...

    @abstractmethod
    def get_tick(self, symbol: str) -> Optional[object]: ...

    @abstractmethod
    def get_positions(self, symbol: Optional[str] = None) -> List: ...

    def get_commission_per_lot(self, symbol: str) -> Optional[float]:
        """Comisión por lote observada de operaciones reales del bróker.

        Se deduce de las posiciones abiertas (no es una propiedad del símbolo).
        Las subclases que no la expongan devuelven None y el caller recurre al
        valor de `.env` como fallback.
        """
        return None

    @abstractmethod
    def get_orders(self, symbol: Optional[str] = None) -> List: ...

    @abstractmethod
    def get_atr(self, symbol: str, period: int = 14) -> float: ...

    @abstractmethod
    def get_market_data(self, symbol: str, bars: int = 20) -> str: ...

    def get_ohlcv(self, symbol: str, timeframe: str = "H1", bars: int = 120) -> List[dict]:
        """Velas como lista de dicts {time, open, high, low, close, volume}.

        Las subclases que no soporten un timeframe devuelven [].
        """
        return []

    @abstractmethod
    def place_order(self, symbol: str, volume: float, order_type: str,
                    price: float = None, stop_loss: float = None,
                    take_profit: float = None, comment: str = "",
                    deviation: int = 10) -> Optional[dict]: ...

    @abstractmethod
    def close_position(self, symbol: str, direction: str = None) -> Optional[dict]: ...
