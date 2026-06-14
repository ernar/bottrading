"""Catálogo de agentes disponibles.

Cada entrada es un "blueprint": la definición declarativa de un agente
(símbolo, modelo, parámetros, persona). `build_agent` los instancia.
Añadir un nuevo símbolo = añadir un blueprint aquí.
"""
from dataclasses import dataclass, field

from agents.base_agent import AgentParams, SymbolAgent
from core.config import get_agent_param_overrides


@dataclass
class AgentBlueprint:
    name: str
    symbol: str
    description: str
    persona: str
    params: AgentParams = field(default_factory=AgentParams)


# Persona del agente de Bitcoin: cripto opera 24/7, alta volatilidad, sin
# sesiones de mercado clásicas y muy sensible al sentimiento y al riesgo macro
# del USD (tasas, inflación, apetito de riesgo).
BTCUSD_PERSONA = (
    "Operas BTCUSD (Bitcoin contra dólar), un activo cripto que cotiza 24/7 "
    "sin cierres de sesión. Características a tener en cuenta:\n"
    "- Volatilidad muy alta: usa stops más amplios (en múltiplos de ATR) y no "
    "te dejes barrer por el ruido; un movimiento del 1-2% es normal.\n"
    "- No hay calendario económico propio de cripto, pero los datos macro de "
    "USD (CPI, FOMC, tasas) y el apetito de riesgo global sí mueven el precio.\n"
    "- El sentimiento de los titulares pesa más que en forex: regulación, ETFs, "
    "hackeos o adopción institucional pueden disparar movimientos bruscos.\n"
    "- Cuidado con mechas largas y barridos de liquidez alrededor de números "
    "redondos; confirma rupturas con volumen y confluencia de indicadores.\n"
    "- Si la volatilidad (ATR) está disparada y no hay tendencia clara, prefiere "
    "hold antes que entrar en medio del rango."
)


AGENT_BLUEPRINTS: dict[str, AgentBlueprint] = {
    "btc-agent": AgentBlueprint(
        name="btc-agent",
        symbol="BTCUSD",
        description="Especialista en Bitcoin (BTCUSD) — cripto 24/7, alta volatilidad",
        persona=BTCUSD_PERSONA,
        params=AgentParams(
            provider="gemini",
            model="gemini-2.0-flash",
            min_confidence=0.6,
            min_rr=1.5,         # cripto: exige mejor R:R por la volatilidad
            atr_sl_mult=1.8,
            atr_tp_mult=2.7,
            lot_size=0.01,
            risk_per_trade=0.02,
            max_open_positions=3,
            max_spread_filter=50.0,  # el spread de BTC en puntos es alto
        ),
    ),
}


def list_agents() -> list[AgentBlueprint]:
    """Lista ordenada de blueprints para mostrar en el menú."""
    return list(AGENT_BLUEPRINTS.values())


def build_agent(name: str, debug_mode: bool = True,
                provider: str | None = None, model: str | None = None) -> SymbolAgent:
    """Instancia un agente a partir de su blueprint.

    `provider`/`model` permiten sobreescribir el LLM por defecto del blueprint
    (p. ej. elegir Gemini desde el menú) sin tocar el catálogo.
    
    También aplica overrides de configuración desde .env (MAX_OPEN_POSITIONS_*,
    MIN_CONFIDENCE_*, etc.) según la precedencia: símbolo > modelo > default.
    """
    bp = AGENT_BLUEPRINTS[name]
    params = bp.params
    
    # Sobreescribir provider/model si se proporcionan
    if provider or model:
        params = bp.params.model_copy(update={
            "provider": provider or bp.params.provider,
            "model": model or bp.params.model,
        })
    
    # Aplicar overrides desde .env (precedencia: símbolo > modelo > default)
    overrides = get_agent_param_overrides(bp.symbol, params.model)
    if overrides:
        params = params.model_copy(update=overrides)
    
    return SymbolAgent(
        name=bp.name,
        symbol=bp.symbol,
        params=params,
        description=bp.description,
        persona=bp.persona,
        debug_mode=debug_mode,
    )
