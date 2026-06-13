"""Catálogo de agentes disponibles.

Cada entrada es un "blueprint": la definición declarativa de un agente
(símbolo, modelo, parámetros, persona). `build_agent` los instancia.
Añadir un nuevo símbolo = añadir un blueprint aquí.
"""
from dataclasses import dataclass, field

from agents.base_agent import AgentParams, SymbolAgent


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
            provider="ollama",
            model="qwen3:8b",   # default por ahora; el orquestador podrá cambiarlo
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


def build_agent(name: str, debug_mode: bool = True) -> SymbolAgent:
    bp = AGENT_BLUEPRINTS[name]
    return SymbolAgent(
        name=bp.name,
        symbol=bp.symbol,
        params=bp.params,
        description=bp.description,
        persona=bp.persona,
        debug_mode=debug_mode,
    )
