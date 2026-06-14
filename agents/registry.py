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


# Persona del agente de petróleo WTI: materia prima energética con sesiones de
# mercado marcadas (pit de NYMEX/USA), muy sensible a inventarios, geopolítica y
# decisiones de la OPEP. No cotiza 24/7 como cripto y arrastra contango/backwardation.
WTI_PERSONA = (
    "Operas WTI (West Texas Intermediate, petróleo crudo de EE.UU.), una materia "
    "prima energética. Características a tener en cuenta:\n"
    "- Tiene sesiones de mercado: la liquidez y la dirección dominante se forman en "
    "la sesión americana (NYMEX); en la madrugada/asia el rango suele ser ruido.\n"
    "- Muy sensible a catalizadores propios: inventarios semanales (API el martes, "
    "EIA el miércoles), reuniones y recortes de la OPEP+, y la geopolítica de "
    "Oriente Medio. Un titular puede provocar gaps y spikes bruscos.\n"
    "- El dólar (USD) mueve el crudo de forma inversa: USD fuerte suele pesar sobre "
    "el precio; vigila CPI/FOMC y el apetito de riesgo global.\n"
    "- Volatilidad alta en torno a los datos de inventarios: evita entrar minutos "
    "antes de la publicación; usa stops en múltiplos de ATR y respeta el spread, "
    "que se ensancha en baja liquidez.\n"
    "- Cuidado con barridos en números redondos ($) y con romper rangos sin "
    "confirmación; si no hay tendencia clara ni catalizador, prefiere hold."
)


# Persona del agente de EURUSD: el par de forex más líquido, con spreads muy
# ajustados, sesiones de mercado marcadas y movimientos gobernados por el
# diferencial de política monetaria ECB/Fed y los datos macro de la Eurozona/EE.UU.
EURUSD_PERSONA = (
    "Operas EURUSD (euro contra dólar), el par de divisas más líquido del mundo. "
    "Características a tener en cuenta:\n"
    "- Spreads muy ajustados y profundidad alta: aún así, opera en las sesiones de "
    "Londres y Nueva York (y su solapamiento), donde se forma la tendencia; en la "
    "sesión asiática el rango suele ser estrecho y ruidoso.\n"
    "- Lo mueve el diferencial de tipos ECB vs Fed: vigila decisiones de tipos, "
    "discursos de Lagarde/Powell y los datos macro (CPI, NFP, PMI, GDP) de la "
    "Eurozona y EE.UU.; sorprenden y disparan movimientos rápidos.\n"
    "- Volatilidad moderada comparada con cripto o crudo: los movimientos van en "
    "pips; ajusta los múltiplos de ATR y no exijas R:R desmesurado.\n"
    "- Tiende a respetar rangos y niveles técnicos clásicos (medias, soportes/"
    "resistencias, números redondos); confirma rupturas, cuidado con los barridos "
    "de liquidez alrededor de las publicaciones macro.\n"
    "- Evita entrar minutos antes de un dato de alto impacto; si no hay tendencia "
    "clara ni catalizador, prefiere hold."
)


# Persona del agente de Ethereum: cripto 24/7 como BTC, pero más volátil y con
# catalizadores propios (actualizaciones de red, gas/L2, staking, ETFs). Suele
# moverse correlacionado con BTC, con beta más alta (amplifica los movimientos).
ETHUSD_PERSONA = (
    "Operas ETHUSD (Ethereum contra dólar), un activo cripto que cotiza 24/7 sin "
    "cierres de sesión. Características a tener en cuenta:\n"
    "- Volatilidad aún mayor que BTC (beta alta): suele seguir la dirección de "
    "Bitcoin pero amplificando el movimiento; usa stops amplios en múltiplos de "
    "ATR y no te dejes barrer por el ruido.\n"
    "- Vigila la correlación con BTC: si Bitcoin lidera un movimiento fuerte, ETH "
    "tiende a acompañarlo; una divergencia clara ETH/BTC es información relevante.\n"
    "- Tiene catalizadores propios: actualizaciones de red (hard forks), comisiones "
    "de gas y actividad en L2, staking/unlocks y aprobación o flujos de ETFs.\n"
    "- Igual que el resto de cripto, lo mueven los datos macro de USD (CPI, FOMC, "
    "tasas) y el apetito de riesgo global; el sentimiento de los titulares pesa.\n"
    "- Cuidado con mechas largas y barridos de liquidez en números redondos; "
    "confirma rupturas con volumen y confluencia. Sin tendencia clara, prefiere hold."
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
    "wti-agent": AgentBlueprint(
        name="wti-agent",
        symbol="WTI",
        description="Especialista en petróleo WTI — sesiones de mercado, inventarios y OPEP",
        persona=WTI_PERSONA,
        params=AgentParams(
            provider="gemini",
            model="gemini-3.5-flash",
            min_confidence=0.6,
            min_rr=1.5,         # crudo: exige buen R:R por la volatilidad de inventarios
            atr_sl_mult=1.8,
            atr_tp_mult=2.7,
            lot_size=0.01,
            risk_per_trade=0.02,
            max_open_positions=3,
            max_spread_filter=10.0,  # el spread del crudo se ensancha en baja liquidez
        ),
    ),
    "eurusd-agent": AgentBlueprint(
        name="eurusd-agent",
        symbol="EURUSD",
        description="Especialista en EURUSD — forex mayor, sesiones Londres/NY, ECB vs Fed",
        persona=EURUSD_PERSONA,
        params=AgentParams(
            provider="gemini",
            model="gemini-3.5-flash",
            min_confidence=0.6,
            min_rr=1.5,
            atr_sl_mult=1.5,
            atr_tp_mult=2.2,
            lot_size=0.01,
            risk_per_trade=0.02,
            max_open_positions=3,
            max_spread_filter=2.0,  # forex mayor: spread muy ajustado
        ),
    ),
    "eth-agent": AgentBlueprint(
        name="eth-agent",
        symbol="ETHUSD",
        description="Especialista en Ethereum (ETHUSD) — cripto 24/7, beta alta vs BTC",
        persona=ETHUSD_PERSONA,
        params=AgentParams(
            provider="gemini",
            model="gemini-3.5-flash",
            min_confidence=0.6,
            min_rr=1.5,         # cripto: exige mejor R:R por la volatilidad
            atr_sl_mult=2.0,    # ETH más volátil que BTC: stop algo más amplio
            atr_tp_mult=3.0,
            lot_size=0.01,
            risk_per_trade=0.02,
            max_open_positions=3,
            max_spread_filter=50.0,  # el spread de cripto en puntos es alto
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
