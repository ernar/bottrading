"""Carga de configuración desde .env.

Centraliza la lectura de variables de entorno con defaults razonables.
Permite configurar operaciones máximas por símbolo, por modelo, o globalmente.
"""
import os
import json
from typing import Optional


def _env_bool(name: str, default: bool) -> bool:
    """Lee un flag booleano del entorno."""
    val = os.getenv(name, "").strip().lower()
    if not val:
        return default
    return val not in ("0", "false", "no", "off")


def _env_int(name: str, default: int) -> int:
    """Lee un entero del entorno con fallback."""
    val = os.getenv(name, "").strip()
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    """Lee un float del entorno con fallback."""
    val = os.getenv(name, "").strip()
    if not val:
        return default
    try:
        return float(val)
    except ValueError:
        return default


def _env_json(name: str, default: Optional[dict] = None) -> dict:
    """Lee un JSON del entorno (esperado: {"key": value, ...})."""
    val = os.getenv(name, "").strip()
    if not val:
        return default or {}
    try:
        return json.loads(val)
    except json.JSONDecodeError:
        return default or {}


def get_max_open_positions(symbol: str, model: str, default: int = 5) -> int:
    """Resuelve el límite de operaciones máximas abiertas para un símbolo/modelo.
    
    Orden de precedencia (primera coincidencia gana):
    1. MAX_OPEN_POSITIONS_<SYMBOL> (p. ej. MAX_OPEN_POSITIONS_BTCUSD)
    2. MAX_OPEN_POSITIONS_<MODEL> (p. ej. MAX_OPEN_POSITIONS_QWE3:8B)
    3. MAX_OPEN_POSITIONS_DEFAULT
    4. `default` (por defecto 5)
    
    Ejemplo en .env:
        MAX_OPEN_POSITIONS_BTCUSD=3
        MAX_OPEN_POSITIONS_QWE3:8B=4
        MAX_OPEN_POSITIONS_DEFAULT=5
    """
    # 1. Intenta por símbolo
    symbol_key = f"MAX_OPEN_POSITIONS_{symbol.upper()}"
    val = os.getenv(symbol_key)
    if val:
        try:
            return int(val.strip())
        except ValueError:
            pass
    
    # 2. Intenta por modelo
    model_key = f"MAX_OPEN_POSITIONS_{model.upper().replace(':', '_')}"
    val = os.getenv(model_key)
    if val:
        try:
            return int(val.strip())
        except ValueError:
            pass
    
    # 3. Default global
    val = os.getenv("MAX_OPEN_POSITIONS_DEFAULT")
    if val:
        try:
            return int(val.strip())
        except ValueError:
            pass
    
    # 4. Fallback programático
    return default


def get_agent_param_overrides(symbol: str, model: str) -> dict:
    """Resuelve parámetros del agente desde .env para un símbolo/modelo.
    
    Soporta: min_confidence, min_rr, atr_sl_mult, atr_tp_mult, max_spread_filter,
             risk_per_trade, lot_size, max_open_positions, temperature.
    
    Nomenclatura:
    - MAX_OPEN_POSITIONS_<SYMBOL> (p. ej. MAX_OPEN_POSITIONS_BTCUSD=3)
    - MIN_CONFIDENCE_<SYMBOL> (p. ej. MIN_CONFIDENCE_BTCUSD=0.65)
    - <PARAM>_DEFAULT (p. ej. MIN_CONFIDENCE_DEFAULT=0.6)
    
    Devuelve un dict con solo los parámetros que pudieron ser leídos exitosamente
    (vacío si no hay configuración adicional).
    """
    overrides = {}
    
    # Mapeo: (nombre del parámetro, tipo)
    params_to_check = [
        ("min_confidence", float),
        ("min_rr", float),
        ("atr_sl_mult", float),
        ("atr_tp_mult", float),
        ("max_spread_filter", float),
        ("risk_per_trade", float),
        ("lot_size", float),
        ("temperature", float),
        ("max_open_positions", int),
    ]
    
    for param_name, param_type in params_to_check:
        param_upper = param_name.upper()
        
        # 1. Por símbolo: PARAM_<SYMBOL>
        symbol_key = f"{param_upper}_{symbol.upper()}"
        val = os.getenv(symbol_key, "").strip()
        if val:
            try:
                overrides[param_name] = param_type(val)
                continue
            except (ValueError, TypeError):
                pass
        
        # 2. Por modelo: PARAM_<MODEL>
        model_key = f"{param_upper}_{model.upper().replace(':', '_')}"
        val = os.getenv(model_key, "").strip()
        if val:
            try:
                overrides[param_name] = param_type(val)
                continue
            except (ValueError, TypeError):
                pass
        
        # 3. Default global: PARAM_DEFAULT
        default_key = f"{param_upper}_DEFAULT"
        val = os.getenv(default_key, "").strip()
        if val:
            try:
                overrides[param_name] = param_type(val)
            except (ValueError, TypeError):
                pass
    
    return overrides


def get_coordinator_config() -> dict:
    """Configuración del coordinador (mesa de dirección) desde .env.

    El coordinador es una capa por encima de los agentes especialistas que
    reparte capital y decide go/no-go por símbolo. Variables soportadas:

    - COORDINATOR_ENABLED (bool, default True): activa la capa coordinadora.
      Si está desactivado, el orquestador usa la ruta clásica por agente.
    - COORDINATOR_PROVIDER / COORDINATOR_MODEL: LLM del coordinador. Si están
      vacíos, en main.py se hereda el LLM del primer agente como default.
    - COORDINATOR_CAN_CLOSE (bool, default True): permite cerrar/reducir
      posiciones abiertas. Kill-switch del cierre automático.
    - COORDINATOR_TEMPERATURE (float, default 0.2): temperatura del LLM.
    - MAX_TOTAL_EXPOSURE_PCT (float, default 0.5): exposición total máxima
      (margen usado / equity) por encima de la cual no se aprueban entradas.
    - MAX_SYMBOL_ALLOCATION_PCT (float, default 0.4): asignación máxima de
      capital por símbolo (fracción del equity).
    """
    return {
        "enabled": _env_bool("COORDINATOR_ENABLED", True),
        "provider": os.getenv("COORDINATOR_PROVIDER", "").strip(),
        "model": os.getenv("COORDINATOR_MODEL", "").strip(),
        "can_close": _env_bool("COORDINATOR_CAN_CLOSE", True),
        "temperature": _env_float("COORDINATOR_TEMPERATURE", 0.2),
        "max_total_exposure_pct": _env_float("MAX_TOTAL_EXPOSURE_PCT", 0.5),
        "max_symbol_allocation_pct": _env_float("MAX_SYMBOL_ALLOCATION_PCT", 0.4),
    }


def get_commission_per_lot(default: float = 7.0) -> float:
    """Lee la comisión por lote desde .env.
    
    Variables soportadas:
    - COMMISSION_PER_LOT (valor general)
    - COMMISSION (alias para COMMISSION_PER_LOT)
    
    Ejemplo en .env:
        COMMISSION_PER_LOT=0.13
    
    Devuelve el valor en float, o `default` si no está configurado.
    """
    val = os.getenv("COMMISSION_PER_LOT") or os.getenv("COMMISSION")
    if val:
        try:
            return float(val.strip())
        except (ValueError, TypeError):
            pass
    return default
