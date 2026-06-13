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
    temperature: float = 0.2
    # Si True, el volumen se calcula por riesgo (risk_per_trade hasta el SL) en
    # lugar de usar lot_size fijo. Desactivado por defecto: activarlo cambia el
    # tamaño real de las operaciones.
    use_risk_sizing: bool = False
    # Por encima de esta confianza, la señal se salta el límite de posiciones
    # abiertas del símbolo (cuenta y no-duplicar dirección).
    max_pos_override_confidence: float = 0.90


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
            max_pos_override_confidence=params.max_pos_override_confidence,
            debug_mode=debug_mode,
        )
        self.strategy = StrategyEngine(
            self.config,
            provider=params.provider,
            system_suffix=persona,
            min_confidence=params.min_confidence,
            min_rr=params.min_rr,
            temperature=params.temperature,
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

        signal = self.strategy.generate_signal(symbol, market_data=market_data)
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
        """Rellena SL y/o TP con múltiplos de ATR del agente si el modelo no los
        dio. Cada uno se calcula por separado: una señal con SL pero sin TP queda
        igualmente completa (antes se saltaba la validación de R:R por TP=0)."""
        if signal["action"] == "HOLD" or not tick:
            return
        has_sl = bool(signal.get("stop_loss"))
        has_tp = bool(signal.get("take_profit"))
        if has_sl and has_tp:
            return
        atr = client.get_atr(self.symbol)
        if atr <= 0:
            return
        sym_info = client.get_symbol_info(self.symbol)
        digits = sym_info.digits if sym_info else 5
        entry = tick.ask if signal["action"] == "BUY" else tick.bid
        sl_mult, tp_mult = self.params.atr_sl_mult, self.params.atr_tp_mult
        sign = 1 if signal["action"] == "BUY" else -1
        if not has_sl:
            signal["stop_loss"] = round(entry - sign * sl_mult * atr, digits)
        if not has_tp:
            signal["take_profit"] = round(entry + sign * tp_mult * atr, digits)

    def validate(self, signal: dict, positions: list = None, tick=None,
                 spread_points: float = None, total_open_positions: int = None) -> bool:
        return self.strategy.validate_trade(
            signal, positions, tick=tick, spread_points=spread_points,
            total_open_positions=total_open_positions)

    def resolve_volume(self, client, signal: dict) -> float:
        """Volumen a operar. Lote fijo (params.lot_size) salvo que el agente
        tenga use_risk_sizing activado, en cuyo caso se dimensiona por riesgo
        contra el SL usando el valor real del tick del símbolo. Si falta cualquier
        dato necesario, cae al lote fijo para no bloquear la operación."""
        if not self.params.use_risk_sizing:
            return self.params.lot_size
        sym = client.get_symbol_info(self.symbol)
        account = client.get_account_info() or {}
        balance = account.get("balance") or 0
        entry = signal.get("entry") or 0
        sl = signal.get("stop_loss") or 0
        point = getattr(sym, "point", 0) or 0
        tick_value = getattr(sym, "trade_tick_value", 0) or 0
        if not sym or balance <= 0 or entry <= 0 or sl <= 0 or point <= 0 or tick_value <= 0:
            return self.params.lot_size
        return self.strategy.calculate_lot_size(
            account_balance=balance,
            entry_price=entry,
            stop_loss_price=sl,
            point=point,
            tick_value=tick_value,
            volume_min=getattr(sym, "volume_min", 0.01) or 0.01,
            volume_max=getattr(sym, "volume_max", 100.0) or 100.0,
            volume_step=getattr(sym, "volume_step", 0.01) or 0.01,
        )

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
                temperature=new_params.temperature,
            )
        self.params = new_params
        self.config.default_lot_size = new_params.lot_size
        self.config.risk_per_trade = new_params.risk_per_trade
        self.config.max_open_positions = new_params.max_open_positions
        self.config.max_spread_filter = new_params.max_spread_filter
        self.config.max_pos_override_confidence = new_params.max_pos_override_confidence
        self.config.model = new_params.model
        self.strategy.min_confidence = new_params.min_confidence
        self.strategy.min_rr = new_params.min_rr
        self.strategy.temperature = new_params.temperature

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
