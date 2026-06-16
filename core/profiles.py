"""Perfiles de trading: dos ejes independientes que el dashboard mueve en vivo.

- **Riesgo** (conservative/moderate/aggressive/extreme): apetito, exposición y
  selectividad. Cuántas operaciones se abren y cuánto capital se arriesga.
- **Horizonte** (corto/medio/largo): duración de las operaciones. Distancia del
  TP, periodo de gracia, trailing/parcial y cadencia de reanálisis.

Cada perfil es un conjunto coherente de claves `.env` (las escribe el endpoint
correspondiente con `write_env`) MÁS una **directiva de prompt**: el texto que se
inyecta en el prompt del especialista y de la mesa para que el LLM realmente
cambie de disposición. Sin la directiva, subir topes/bajar umbrales solo afloja
filtros: el LLM seguiría igual de cauto (era el bug que reportaba el usuario).

Los topes/umbrales son el GUARDARRAÍL determinista; la directiva es el INCENTIVO.
"""
import os


# ----- Eje RIESGO: apetito / exposición / selectividad -----

RISK_PROFILES: dict[str, dict[str, str]] = {
    "conservative": {
        "MAX_TOTAL_EXPOSURE_PCT": "0.30", "MAX_SYMBOL_ALLOCATION_PCT": "0.20",
        "MAX_NET_DIRECTION_PCT": "0.40", "MAX_PYRAMID_DIRECTION_PCT": "0.40",
        "REVERSAL_DRAWDOWN_PCT": "0.025",
        "MIN_CONFIDENCE_DEFAULT": "0.70", "MIN_RR_DEFAULT": "1.5",
        "MAX_OPEN_POSITIONS_DEFAULT": "2",
    },
    "moderate": {
        "MAX_TOTAL_EXPOSURE_PCT": "0.50", "MAX_SYMBOL_ALLOCATION_PCT": "0.40",
        "MAX_NET_DIRECTION_PCT": "0.60", "MAX_PYRAMID_DIRECTION_PCT": "0.60",
        "REVERSAL_DRAWDOWN_PCT": "0.015",
        "MIN_CONFIDENCE_DEFAULT": "0.60", "MIN_RR_DEFAULT": "1.3",
        "MAX_OPEN_POSITIONS_DEFAULT": "3",
    },
    "aggressive": {
        "MAX_TOTAL_EXPOSURE_PCT": "0.75", "MAX_SYMBOL_ALLOCATION_PCT": "0.60",
        "MAX_NET_DIRECTION_PCT": "0.90", "MAX_PYRAMID_DIRECTION_PCT": "1.20",
        "REVERSAL_DRAWDOWN_PCT": "0.010",
        "MIN_CONFIDENCE_DEFAULT": "0.50", "MIN_RR_DEFAULT": "1.1",
        "MAX_OPEN_POSITIONS_DEFAULT": "5",
    },
    "extreme": {
        "MAX_TOTAL_EXPOSURE_PCT": "0.90", "MAX_SYMBOL_ALLOCATION_PCT": "0.80",
        "MAX_NET_DIRECTION_PCT": "1.20", "MAX_PYRAMID_DIRECTION_PCT": "1.80",
        "REVERSAL_DRAWDOWN_PCT": "0.008",
        "MIN_CONFIDENCE_DEFAULT": "0.45", "MIN_RR_DEFAULT": "1.0",
        "MAX_OPEN_POSITIONS_DEFAULT": "8",
    },
}

# Directiva de RIESGO inyectada en el prompt del especialista (disposición a operar).
_RISK_AGENT_DIRECTIVE = {
    "conservative": (
        "Perfil de riesgo CONSERVADOR: prioriza preservar capital. Sé MUY selectivo: "
        "solo propón buy/sell con confluencia clara de varios indicadores y tendencia "
        "definida; ante la duda, HOLD. Mejor perder una oportunidad que forzar una mala."),
    "moderate": (
        "Perfil de riesgo MODERADO: equilibra oportunidad y prudencia. Propón entradas "
        "cuando haya una ventaja razonable; usa HOLD si el cuadro es confuso."),
    "aggressive": (
        "Perfil de riesgo AGRESIVO: busca ACTIVAMENTE oportunidades. Inclínate por operar "
        "cuando haya una ventaja aunque no sea perfecta; reserva HOLD solo para cuando NO "
        "haya señal o el riesgo sea claramente desfavorable. No exijas confluencia total."),
    "extreme": (
        "Perfil de riesgo EXTREMO: máxima proactividad. Toma cada ventaja técnica plausible; "
        "evita HOLD salvo ausencia total de señal. Asume más operaciones y más ruido a cambio "
        "de no perder movimientos."),
}

# Directiva de RIESGO inyectada en el prompt de la mesa (apetito de cartera).
_RISK_COORD_DIRECTIVE = {
    "conservative": (
        "Apetito CONSERVADOR: aprueba con cautela, asigna capital pequeño y prioriza no "
        "sobreexponerte. Ante duda de cartera, no abras."),
    "moderate": (
        "Apetito MODERADO: aprueba las señales con ventaja y reparte el capital con sensatez."),
    "aggressive": (
        "Apetito AGRESIVO: tu postura por defecto es APROBAR y asignar capital generoso; "
        "aprovecha el margen libre y piramida las posiciones ganadoras a favor de tendencia. "
        "Solo veta por una razón concreta de cartera."),
    "extreme": (
        "Apetito EXTREMO: aprueba casi todo lo accionable, asigna agresivamente y piramida con "
        "decisión los ganadores. Maximiza el uso del capital disponible."),
}


# ----- Eje HORIZONTE: duración de las operaciones -----

HORIZON_PROFILES: dict[str, dict[str, str]] = {
    "corto": {
        "COORDINATOR_TP_RR_MIN": "0.8", "COORDINATOR_TP_RR_MAX": "2.0",
        "ATR_TP_MULT_DEFAULT": "1.2", "ATR_SL_MULT_DEFAULT": "1.0",
        "MIN_HOLD_SECONDS": "60",
        "TRAILING_BREAKEVEN_ATR_MULT_DEFAULT": "0.6",
        "TRAILING_STEP_ATR_MULT_DEFAULT": "0.4",
        "PARTIAL_PROFIT_TRIGGER_PCT_DEFAULT": "0.3",
        "AT_MAX_ANALYSIS_INTERVAL": "180",
    },
    "medio": {
        "COORDINATOR_TP_RR_MIN": "1.0", "COORDINATOR_TP_RR_MAX": "4.0",
        "ATR_TP_MULT_DEFAULT": "2.0", "ATR_SL_MULT_DEFAULT": "1.6",
        "MIN_HOLD_SECONDS": "300",
        "TRAILING_BREAKEVEN_ATR_MULT_DEFAULT": "1.0",
        "TRAILING_STEP_ATR_MULT_DEFAULT": "0.6",
        "PARTIAL_PROFIT_TRIGGER_PCT_DEFAULT": "0.5",
        "AT_MAX_ANALYSIS_INTERVAL": "600",
    },
    "largo": {
        "COORDINATOR_TP_RR_MIN": "1.5", "COORDINATOR_TP_RR_MAX": "8.0",
        "ATR_TP_MULT_DEFAULT": "3.2", "ATR_SL_MULT_DEFAULT": "2.2",
        "MIN_HOLD_SECONDS": "900",
        "TRAILING_BREAKEVEN_ATR_MULT_DEFAULT": "1.6",
        "TRAILING_STEP_ATR_MULT_DEFAULT": "1.0",
        "PARTIAL_PROFIT_TRIGGER_PCT_DEFAULT": "0.7",
        "AT_MAX_ANALYSIS_INTERVAL": "1200",
    },
}

# Directiva de HORIZONTE (misma para especialista y mesa: define la duración objetivo).
_HORIZON_DIRECTIVE = {
    "corto": (
        "Horizonte CORTO (scalping): busca movimientos rápidos. Propón objetivos (TP) CERCANOS "
        "al precio y stops ajustados; asegura beneficio pronto y rota rápido. No persigas "
        "recorridos largos."),
    "medio": (
        "Horizonte MEDIO: objetivos y stops equilibrados; deja respirar la operación sin "
        "perseguir recorridos extremos."),
    "largo": (
        "Horizonte LARGO (swing): busca recorridos amplios. Propón objetivos (TP) LEJANOS con "
        "stops holgados; deja correr las operaciones a favor de tendencia y no cierres por ruido."),
}


# ----- Helpers -----

def get_active_risk() -> str:
    """Nivel de riesgo activo (RISK_PROFILE en .env). Default 'moderate'."""
    val = (os.getenv("RISK_PROFILE", "") or "").strip().lower()
    return val if val in RISK_PROFILES else "moderate"


def get_active_horizon() -> str:
    """Horizonte activo (HORIZON en .env). Default 'medio'."""
    val = (os.getenv("HORIZON", "") or "").strip().lower()
    return val if val in HORIZON_PROFILES else "medio"


# Perfiles cuyo apetito permite ABRIR señales de riesgo ALTO (risk_level="high")
# aunque ya haya posiciones abiertas en el símbolo. Los perfiles más cautos las
# vetan en `validate_trade` ("riesgo alto con posiciones abiertas"); en los de
# apetito alto ese veto se levanta para que la mesa pueda asumir más riesgo
# cuando el usuario sube el toggle. El resto de guardarraíles (exposición por
# símbolo/total, margen libre, R:R mínimo) siguen vigentes.
_HIGH_RISK_WITH_POSITIONS_PROFILES = {"aggressive", "extreme"}


def allows_high_risk_with_positions(risk: str) -> bool:
    """True si el perfil de riesgo permite operar señales ``risk_level="high"``
    aun con posiciones abiertas (apetito alto). Lo consulta el orquestador para
    fijar `StrategyEngine.allow_high_risk_with_positions`. Ver `validate_trade`."""
    return risk in _HIGH_RISK_WITH_POSITIONS_PROFILES


def build_agent_directive(risk: str, horizon: str) -> str:
    """Directiva combinada (riesgo + horizonte) para el prompt del especialista."""
    parts = [_RISK_AGENT_DIRECTIVE.get(risk, ""), _HORIZON_DIRECTIVE.get(horizon, "")]
    return "\n".join(p for p in parts if p)


def build_coordinator_directive(risk: str, horizon: str) -> str:
    """Directiva combinada (riesgo + horizonte) para el prompt de la mesa."""
    parts = [_RISK_COORD_DIRECTIVE.get(risk, ""), _HORIZON_DIRECTIVE.get(horizon, "")]
    return "\n".join(p for p in parts if p)
