"""Agente especializado por símbolo.

Un `SymbolAgent` reúne todo lo que define la "personalidad" de trading para
un instrumento concreto:

- símbolo del broker con el que opera,
- proveedor + modelo LLM (por ahora el default de Ollama),
- umbrales de riesgo propios (confianza mínima, R:R, múltiplos de ATR...),
- persona: texto de especialización inyectado en el system prompt,
- su propia memoria de señales aislada (tabla signal_memory, scope=<name>).

El agente sabe analizar su símbolo (analyze) y validar una señal (validate);
la ejecución de órdenes la coordina el orquestador.
"""
from typing import Optional
from dotenv import load_dotenv

from pydantic import BaseModel, Field

from core.models import BotConfig
from core.strategy import StrategyEngine
from core.memory import SignalMemory
from core.market_context import build_market_context, momentum_snapshot
from core.news import news_provider
from core.logger import log_signal
from core.config import get_commission_per_lot

load_dotenv()


class AgentParams(BaseModel):
    """Parámetros ajustables de un agente. El orquestador podrá modificarlos
    en caliente para optimizar resultados."""
    provider: str = "gemini"
    model: str = "gemini-2.0-flash"
    # Modo pensamiento (thinking/Reasoner) de DeepSeek, por agente: "auto" sigue
    # el default del modelo / DEEPSEEK_THINKING global; "enabled"/"disabled" lo
    # fuerzan para ESTE agente (mezclar especialistas pensantes y rápidos). Solo
    # afecta a los modelos híbridos deepseek-v4-*.
    thinking: str = "auto"
    # Profundidad del pensamiento cuando está activo: "" (la del modelo / global),
    # "high" o "max".
    reasoning_effort: str = ""
    min_confidence: float = 0.6
    min_rr: float = 1.0
    # Motor de señal: "llm" (genera con el LLM, default) o "deterministic"
    # (trend_state en `timeframe`, SIN LLM — el edge validado en backtest, coste $0).
    signal_mode: str = "llm"
    # Timeframe base de análisis ("H1" default; "D1" para el perfil diario) y el mayor
    # de contexto ("H4"/"W1"). Lo usan build_market_context/momentum/ATR.
    timeframe: str = "H1"
    higher_timeframe: str = "H4"
    # Selectividad del modo determinista: voto mínimo de trend_state (2 = base, 4-5 =
    # solo tendencias fuertes) y, si True, exige ruptura de estructura confirmada.
    det_min_score: int = 2
    det_require_break: bool = False
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
    # --- Gestión dinámica de posición ---
    # Si True, activa trailing stop dinámico: mueve el SL a breakeven cuando el
    # precio se mueve atr_trailing_breakeven_pct a favor, y luego lo sigue con
    # atr_trailing_step_pct.
    use_trailing_stop: bool = False
    # Multiplicador ATR para el trigger de breakeven (ej: 1.0×ATR a favor = mover
    # SL a entry). Solo aplica si use_trailing_stop=True.
    trailing_breakeven_atr_mult: float = 1.0
    # Multiplicador ATR para el paso del trailing stop después del breakeven
    # (ej: 0.5×ATR = el SL sigue al precio a medio ATR).
    trailing_step_atr_mult: float = 0.5
    # Porcentaje mínimo de movimiento a favor para activar partial profit.
    # Cuando el precio alcanza este % de ganancia, se cierra parcialmente la
    # posición (partial_profit_pct del volumen) y se mueve SL a breakeven.
    partial_profit_trigger_pct: float = 0.0
    partial_profit_pct: float = 0.5
    # Filtro de spread dinámico: si True, el umbral de spread se ajusta según la
    # hora del día (más permisivo en horas de baja liquidez).
    dynamic_spread_filter: bool = False
    # Multiplicador adicional para el spread en horas de alta volatilidad.
    # Se aplica cuando el spread medio de los últimos N ticks supera este umbral.
    volatility_spread_mult: float = 1.5


class SymbolAgent:

    def __init__(self, name: str, symbol: str, params: AgentParams,
                 description: str = "", persona: str = "",
                 debug_mode: bool = True):
        self.name = name
        self.symbol = symbol
        self.params = params
        self.description = description
        self.persona = persona
        # Si False, el orquestador omite a este agente en la fase de recolección:
        # deja de analizar y de proponer entradas en las siguientes rotaciones
        # (las posiciones ya abiertas las sigue gobernando la mesa). Conmutable
        # en caliente desde el dashboard.
        self.enabled = True

        self.config = BotConfig(
            model=params.model,
            symbols=[symbol],
            default_lot_size=params.lot_size,
            max_spread_filter=params.max_spread_filter,
            risk_per_trade=params.risk_per_trade,
            max_open_positions=params.max_open_positions,
            max_pos_override_confidence=params.max_pos_override_confidence,
            debug_mode=debug_mode,
            commission_per_lot=get_commission_per_lot(),
        )
        self.strategy = StrategyEngine(
            self.config,
            provider=params.provider,
            system_suffix=persona,
            min_confidence=params.min_confidence,
            min_rr=params.min_rr,
            temperature=params.temperature,
            thinking=params.thinking,
            reasoning_effort=params.reasoning_effort,
        )
        # Memoria aislada por agente: cada uno aprende de sus propias señales.
        self.memory = SignalMemory(scope=name)

    # ----- Análisis -----

    def analyze(self, client, platform: str = "mt4") -> Optional[dict]:
        """Genera una señal para el símbolo del agente. Devuelve el dict de
        señal (ya con SL/TP rellenados por ATR si faltaban) o None."""
        symbol = self.symbol
        tick = client.get_tick(symbol)
        if tick:
            # Feedback: evalúa señales pasadas contra el precio actual.
            self.memory.evaluate_pending(symbol, (tick.ask + tick.bid) / 2)

        positions = client.get_positions(symbol)
        if self.params.signal_mode == "deterministic":
            # Edge VALIDADO en backtest, sin LLM (coste $0): trend_state en el
            # timeframe del agente con SL/TP por ATR. No usa market_data/noticias.
            from core.signals import deterministic_signal
            signal = deterministic_signal(
                client, symbol, timeframe=self.params.timeframe,
                atr_sl_mult=self.params.atr_sl_mult, atr_tp_mult=self.params.atr_tp_mult,
                min_score=self.params.det_min_score, require_break=self.params.det_require_break,
                lot=self.params.lot_size)
        else:
            market_data = build_market_context(
                client, symbol,
                positions=positions,
                memory_summary=self.memory.get_summary(symbol),
                news_context=news_provider.get_news_context(symbol),
                base_tf=self.params.timeframe, higher_tf=self.params.higher_timeframe,
            )
            signal = self.strategy.generate_signal(symbol, market_data=market_data)
        if not signal:
            return None

        # Momento/estructura DETERMINISTA (menos rezagado que el cruce EMA del
        # prompt): se adjunta a la señal para que la mesa dispare la guardia de
        # reversión sin esperar a que el LLM relabele la tendencia. Campos extra
        # ignorados por log_signal/update_signal (cherry-pick de campos conocidos).
        ts = momentum_snapshot(client, symbol, timeframe=self.params.timeframe)
        if ts:
            signal["momentum"] = ts.get("direction")
            if ts.get("reversal"):
                signal["reversal"] = ts["reversal"]

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
        atr = client.get_atr(self.symbol, timeframe=self.params.timeframe)
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
                 spread_points: float = None, total_open_positions: int = None,
                 enforce_max_positions: bool = True, min_rr: float = None,
                 max_spread_override: float = None) -> bool:
        return self.strategy.validate_trade(
            signal, positions, tick=tick, spread_points=spread_points,
            total_open_positions=total_open_positions,
            enforce_max_positions=enforce_max_positions, min_rr=min_rr,
            max_spread_override=max_spread_override)

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
                thinking=new_params.thinking,
                reasoning_effort=new_params.reasoning_effort,
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
        # thinking/reasoning_effort se leen del motor vivo en cada llamada DeepSeek.
        self.strategy.thinking = new_params.thinking
        self.strategy.reasoning_effort = new_params.reasoning_effort

    # ----- Introspección (para CLI y dashboard/orquestador) -----

    def status(self) -> dict:
        return {
            "name": self.name,
            "symbol": self.symbol,
            "description": self.description,
            "provider": self.params.provider,
            "model": self.params.model,
            "thinking": self.params.thinking,
            "reasoning_effort": self.params.reasoning_effort,
            "min_confidence": self.params.min_confidence,
            "min_rr": self.params.min_rr,
            "lot_size": self.params.lot_size,
        }

    def __repr__(self) -> str:
        return f"<SymbolAgent {self.name} {self.symbol} {self.params.provider}/{self.params.model}>"
