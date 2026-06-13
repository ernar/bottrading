"""Agente especializado por símbolo.

Un `SymbolAgent` reúne todo lo que define la "personalidad" de trading para
un instrumento concreto:

- símbolo del broker con el que opera,
- proveedor + modelo LLM (por ahora el default de Ollama),
- umbrales de riesgo propios (confianza mínima, R:R, múltiplos de ATR...),
- persona: texto de especialización inyectado en el system prompt,
- su propia memoria de señales aislada (logs/agents/<name>_memory.json).

El agente sabe analizar su símbolo (analyze) y validar una señal (validate);
la ejecución de órdenes la coordina el orquestador.
"""
from typing import Optional

from pydantic import BaseModel, Field

from core.models import BotConfig
from core.strategy import StrategyEngine
from core.memory import SignalMemory
from core.market_context import build_market_context
from core.news import news_provider
from core.logger import log_signal


class AgentParams(BaseModel):
    """Parámetros ajustables de un agente. El orquestador podrá modificarlos
    en caliente para optimizar resultados."""
    provider: str = "ollama"
    model: str = "qwen3:8b"
    min_confidence: float = 0.6
    min_rr: float = 1.0
    atr_sl_mult: float = 1.5
    atr_tp_mult: float = 2.0
    lot_size: float = 0.01
    risk_per_trade: float = 0.02
    max_open_positions: int = 5
    max_spread_filter: float = 2.0
    temperature: float = 0.2  # reservado para futuras tuning del orquestador


class SymbolAgent:

    def __init__(self, name: str, symbol: str, params: AgentParams,
                 description: str = "", persona: str = "",
                 debug_mode: bool = True):
        self.name = name
        self.symbol = symbol
        self.params = params
        self.description = description
        self.persona = persona

        self.config = BotConfig(
            model=params.model,
            symbols=[symbol],
            default_lot_size=params.lot_size,
            max_spread_filter=params.max_spread_filter,
            risk_per_trade=params.risk_per_trade,
            max_open_positions=params.max_open_positions,
            debug_mode=debug_mode,
        )
        self.strategy = StrategyEngine(
            self.config,
            provider=params.provider,
            system_suffix=persona,
            min_confidence=params.min_confidence,
            min_rr=params.min_rr,
        )
        # Memoria aislada por agente: cada uno aprende de sus propias señales.
        self.memory = SignalMemory(path=f"logs/agents/{name}_memory.json")

    # ----- Análisis -----

    def analyze(self, client, platform: str = "mt5") -> Optional[dict]:
        """Genera una señal para el símbolo del agente. Devuelve el dict de
        señal (ya con SL/TP rellenados por ATR si faltaban) o None."""
        symbol = self.symbol
        tick = client.get_tick(symbol)
        if tick:
            # Feedback: evalúa señales pasadas contra el precio actual.
            self.memory.evaluate_pending(symbol, (tick.ask + tick.bid) / 2)

        positions = client.get_positions(symbol)
        market_data = build_market_context(
            client, symbol,
            positions=positions,
            memory_summary=self.memory.get_summary(symbol),
            news_context=news_provider.get_news_context(symbol),
        )

        signal = self.strategy.generate_signal(symbol, positions, market_data=market_data)
        if not signal:
            return None

        self._fill_sl_tp(client, signal, tick)
        signal["platform"] = platform.upper()
        signal["agent"] = self.name

        log_signal(signal, platform=platform)
        if tick:
            ref_price = tick.ask if signal["action"] == "BUY" else tick.bid
            self.memory.record_signal(symbol, signal, ref_price)
        return signal

    def _fill_sl_tp(self, client, signal: dict, tick):
        """Rellena SL/TP con múltiplos de ATR propios del agente si el modelo
        no los proporcionó."""
        if signal["action"] == "HOLD":
            return
        if signal.get("stop_loss") and signal["stop_loss"] != 0:
            return
        atr = client.get_atr(self.symbol)
        sym_info = client.get_symbol_info(self.symbol)
        digits = sym_info.digits if sym_info else 5
        if atr <= 0 or not tick:
            return
        entry = tick.ask if signal["action"] == "BUY" else tick.bid
        sl_mult, tp_mult = self.params.atr_sl_mult, self.params.atr_tp_mult
        if signal["action"] == "BUY":
            signal["stop_loss"] = round(entry - sl_mult * atr, digits)
            if not signal.get("take_profit"):
                signal["take_profit"] = round(entry + tp_mult * atr, digits)
        else:
            signal["stop_loss"] = round(entry + sl_mult * atr, digits)
            if not signal.get("take_profit"):
                signal["take_profit"] = round(entry - tp_mult * atr, digits)

    def validate(self, signal: dict, positions: list = None, tick=None) -> bool:
        return self.strategy.validate_trade(signal, positions, tick=tick)

    # ----- Ajuste de parámetros (usado por el orquestador) -----

    def apply_params(self, new_params: "AgentParams"):
        """Reemplaza los parámetros del agente y sincroniza la estrategia viva.

        `min_confidence`/`min_rr` viven en la instancia de StrategyEngine; los
        múltiplos de ATR y el lote se leen de `self.params` en cada ciclo, así
        que basta con actualizar ambos. El provider/modelo NO se cambian en
        caliente (requeriría reconstruir la estrategia); si difieren se avisa."""
        if (new_params.provider != self.params.provider
                or new_params.model != self.params.model):
            self.strategy = StrategyEngine(
                self.config,
                provider=new_params.provider,
                system_suffix=self.persona,
                min_confidence=new_params.min_confidence,
                min_rr=new_params.min_rr,
            )
        self.params = new_params
        self.config.default_lot_size = new_params.lot_size
        self.config.risk_per_trade = new_params.risk_per_trade
        self.config.max_open_positions = new_params.max_open_positions
        self.config.max_spread_filter = new_params.max_spread_filter
        self.config.model = new_params.model
        self.strategy.min_confidence = new_params.min_confidence
        self.strategy.min_rr = new_params.min_rr

    # ----- Introspección (para CLI y dashboard/orquestador) -----

    def status(self) -> dict:
        return {
            "name": self.name,
            "symbol": self.symbol,
            "description": self.description,
            "provider": self.params.provider,
            "model": self.params.model,
            "min_confidence": self.params.min_confidence,
            "min_rr": self.params.min_rr,
            "lot_size": self.params.lot_size,
        }

    def __repr__(self) -> str:
        return f"<SymbolAgent {self.name} {self.symbol} {self.params.provider}/{self.params.model}>"
