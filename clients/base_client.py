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

    def is_market_open(self, symbol: str) -> bool:
        """True si el mercado del símbolo admite operativa ahora mismo.

        Las subclases que sepan distinguir mercado abierto/cerrado lo
        sobreescriben (p. ej. MT4 vía MODE_TRADEALLOWED). Por defecto asume
        abierto para no bloquear a clientes que no expongan el dato.
        """
        return True

    @abstractmethod
    def get_positions(self, symbol: Optional[str] = None) -> List: ...

    def get_commission_per_lot(self, symbol: str) -> Optional[float]:
        """Comisión por lote observada de operaciones reales del bróker.

        Se deduce de las posiciones abiertas (no es una propiedad del símbolo).
        Las subclases que no la expongan devuelven None y el caller recurre al
        valor de `.env` como fallback.
        """
        return None

    def get_closed_deals(self, count: int = 50) -> List[dict]:
        """Historial de operaciones CERRADAS del bróker (P/L realizado + comisión
        + swap reales), del más reciente al más antiguo, hasta ``count``.

        Cada deal: ``{ticket, symbol, type, volume, open_price, close_price,
        profit, commission, swap, open_time, close_time}``. Lo usa el orquestador
        para reconciliar ``closed_trades`` por ticket (el flotante aproximado que
        registra al detectar el cierre no es el realizado). Las subclases que no lo
        soporten devuelven ``[]`` (el caller cae al flotante)."""
        return []

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
    def close_position(self, symbol: str, direction: str = None,
                       volume: float = None, ticket: int = None) -> Optional[dict]: ...

    def modify_position(self, symbol: str, ticket: int, stop_loss: float = None,
                        take_profit: float = None) -> Optional[dict]:
        """Mueve el SL/TP de una posición abierta. Implementación opcional: las
        subclases que no la soporten devuelven None (no-op)."""
        return None
