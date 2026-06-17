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
        # Gestión dinámica de posición (la mueve el selector de horizonte):
        ("trailing_breakeven_atr_mult", float),
        ("trailing_step_atr_mult", float),
        ("partial_profit_trigger_pct", float),
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


def get_active_agents() -> list:
    """Lista de agentes activos guardada para reusar entre sesiones.

    Se persiste en .env como ACTIVE_AGENTS (JSON), p. ej.:
        ACTIVE_AGENTS=[{"name":"btc-agent","provider":"gemini","model":"gemini-2.0-flash","enabled":true}]

    Devuelve una lista de dicts {name, provider, model, enabled}. Vacía si no hay
    selección guardada o el JSON es inválido (fail-safe). La escribe el dashboard
    (botón "Guardar selección" -> POST /api/agents/save)."""
    raw = os.getenv("ACTIVE_AGENTS", "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if isinstance(item, dict) and item.get("name"):
            out.append({
                "name": str(item["name"]),
                "provider": (item.get("provider") or "").lower().strip() or None,
                "model": (item.get("model") or "").strip() or None,
                "enabled": bool(item.get("enabled", True)),
                # Modo pensamiento DeepSeek por agente (None = sin override guardado).
                "thinking": (item.get("thinking") or "").lower().strip() or None,
                "reasoning_effort": (item.get("reasoning_effort") or "").lower().strip() or None,
            })
    return out


def get_coordinator_config() -> dict:
    """Configuración del coordinador (mesa de dirección) desde .env.

    El coordinador es una capa por encima de los agentes especialistas que
    reparte capital y decide go/no-go por símbolo. La mesa está SIEMPRE activa
    (todo el flujo es coordinado; no existe ruta clásica). Variables soportadas:

    - COORDINATOR_PROVIDER / COORDINATOR_MODEL: LLM del coordinador (director de
      la mesa). Se elige y se cambia EN CALIENTE desde el dashboard (pestaña
      "Mesa" -> POST /api/coordinator/model), que persiste la elección aquí. Si
      están vacíos, en main.py se usa gemini-3.5-flash por defecto.
    - COORDINATOR_CAN_CLOSE (bool, default True): permite cerrar/reducir
      posiciones abiertas. Kill-switch del cierre automático (si es False, ni
      siquiera las guardias deterministas cierran).
    - COORDINATOR_LLM_CAN_CLOSE (bool, default False): permite la gestión
      DISCRECIONAL del LLM (reduce/close/hedge "por criterio", p. ej. por
      exposición). Por defecto desactivado: la mesa solo cierra por FUERZA
      MAYOR (guardias deterministas: hard-stop y reversión). Las posiciones
      tienen su propio Stop Loss y se respeta.
    - COORDINATOR_TEMPERATURE (float, default 0.2): temperatura del LLM.
    - MAX_TOTAL_EXPOSURE_PCT (float, default 0.5): exposición total máxima
      (margen usado / equity) por encima de la cual no se aprueban entradas.
    - MAX_SYMBOL_ALLOCATION_PCT (float, default 0.4): asignación máxima de
      capital por símbolo (fracción del equity).

    Control de concentración direccional / reversión de tendencia:
    - MAX_NET_DIRECTION_PCT (float, default 0.6): tope de exposición NETA
      direccional por símbolo (fracción del equity). Frena seguir apilando
      posiciones en la dirección ya saturada.
    - REVERSAL_DRAWDOWN_PCT (float, default 0.015): pérdida flotante (sobre el
      equity) que, JUNTO a un conflicto entre el sesgo abierto y la tendencia
      nueva del especialista, dispara una reducción/cierre forzado del lado
      perdedor. 0 = desactiva la guardia de reversión.
    - MAX_SYMBOL_LOSS_PCT (float, default 0 = off): hard-stop por símbolo
      independiente de la tendencia; si la pérdida flotante del símbolo supera
      este % del equity, se fuerza el cierre.
    - MIN_HOLD_SECONDS (float, default 300): período de gracia para posiciones
      recién abiertas. Mientras la posición más reciente de un símbolo sea más
      joven que esto, la guardia de reversión se pausa y un reduce/close que
      proponga el LLM se aplaza a hold (se le da tiempo a evolucionar). Solo el
      hard-stop catastrófico (MAX_SYMBOL_LOSS_PCT) rompe la gracia. 0 = off.
      La antigüedad se registra en la DB (tabla risk_first_seen) y se recarga al
      arrancar: la gracia SOBREVIVE a los reinicios de la terminal.

    Objetivo de beneficio (TP) gobernado por la mesa:
    - COORDINATOR_TP_RR_MIN (float, default 1.0) y COORDINATOR_TP_RR_MAX (float,
      default 4.0): límites dentro de los que la mesa puede fijar el R:R objetivo
      (tp_rr) de cada entrada. La mesa recorta (objetivo más cercano = rotación
      más rápida) o amplía el TP del especialista; el RiskBook lo acota a este
      rango. Sin tp_rr informado se respeta el TP del especialista.

    Tamaño de posición (lote) gobernado por la mesa:
    - COORDINATOR_SIZE_MULT_MIN (float, default 0.5) y COORDINATOR_SIZE_MULT_MAX
      (float, default 2.0): rango del multiplicador EXPLÍCITO (size_mult) que la
      mesa puede aplicar sobre el lote base del especialista por entrada. >1 agranda
      (convicción / piramidar ganadores), <1 encoge. El RiskBook lo acota a este
      rango; el ajuste por margen libre y los topes de exposición recortan el lote
      final, así que size_mult nunca sobrepasa los límites duros. Sin size_mult
      informado (0/ausente) se respeta el lote base del especialista (×1).

    Nº máximo de posiciones abiertas POR SÍMBOLO (lo gobierna la mesa):
    - MAX_OPEN_POSITIONS_DEFAULT (int, default 3): nº BASE de posiciones que fija
      el perfil de RIESGO.
    - MAX_POSITIONS_HORIZON_MULT (float, default 1.0): multiplicador del HORIZONTE
      sobre ese base (corto = más concurrentes, largo = menos). El producto
      (redondeado, mínimo 1) es el tope DURO por símbolo que el RiskBook impone en
      clamp(): una entrada aprobada que dejaría el símbolo por encima se veta.
      0 en el base = sin tope (la mesa gobierna solo por exposición).
    - MAX_OPEN_POSITIONS_<SÍMBOLO> (int, opcional): override del tope ANTERIOR para
      UN símbolo concreto (p. ej. MAX_OPEN_POSITIONS_BTCUSD=2 limita el apilamiento
      de BTC sin tocar el global). Si no se define, el símbolo usa el tope global.
    """
    # Tope de posiciones por símbolo: base del perfil de RIESGO × multiplicador
    # del HORIZONTE (ambos ejes del front). 0 en el base = sin tope.
    pos_base = _env_int("MAX_OPEN_POSITIONS_DEFAULT", 3)
    pos_mult = _env_float("MAX_POSITIONS_HORIZON_MULT", 1.0)
    max_open_positions = max(1, int(pos_base * pos_mult + 0.5)) if pos_base > 0 else 0
    # Overrides POR SÍMBOLO del tope de posiciones (MAX_OPEN_POSITIONS_<SÍMBOLO>),
    # para acotar el apilamiento de UN símbolo (p. ej. BTCUSD en cuenta pequeña)
    # sin tocar el tope global de la mesa. Se excluye _DEFAULT (es el base global de
    # arriba). Las claves de modelo que pudieran existir aquí nunca casan con un
    # símbolo, así que son inocuas. La mesa lo aplica en clamp() (ver RiskBook).
    pos_by_symbol: dict[str, int] = {}
    for key, raw in os.environ.items():
        if not key.startswith("MAX_OPEN_POSITIONS_") or key == "MAX_OPEN_POSITIONS_DEFAULT":
            continue
        suffix = key[len("MAX_OPEN_POSITIONS_"):].strip()
        raw = (raw or "").strip()
        if not suffix or not raw:
            continue
        try:
            pos_by_symbol[suffix.upper()] = int(float(raw))
        except (TypeError, ValueError):
            continue
    return {
        "provider": os.getenv("COORDINATOR_PROVIDER", "").strip(),
        "model": os.getenv("COORDINATOR_MODEL", "").strip(),
        "can_close": _env_bool("COORDINATOR_CAN_CLOSE", True),
        "llm_can_close": _env_bool("COORDINATOR_LLM_CAN_CLOSE", False),
        "temperature": _env_float("COORDINATOR_TEMPERATURE", 0.2),
        "max_total_exposure_pct": _env_float("MAX_TOTAL_EXPOSURE_PCT", 0.5),
        "max_symbol_allocation_pct": _env_float("MAX_SYMBOL_ALLOCATION_PCT", 0.4),
        "max_net_direction_pct": _env_float("MAX_NET_DIRECTION_PCT", 0.6),
        # Tope superior del sesgo neto tolerado SOLO al piramidar ganadores (posición
        # en ganancia + tendencia confirma). Default = max_net_direction_pct (sin
        # piramidación extra si no se configura).
        "max_pyramid_direction_pct": _env_float(
            "MAX_PYRAMID_DIRECTION_PCT", _env_float("MAX_NET_DIRECTION_PCT", 0.6)),
        "reversal_drawdown_pct": _env_float("REVERSAL_DRAWDOWN_PCT", 0.015),
        "max_symbol_loss_pct": _env_float("MAX_SYMBOL_LOSS_PCT", 0.0),
        "min_hold_seconds": _env_float("MIN_HOLD_SECONDS", 300.0),
        # Rango del R:R objetivo (tp_rr) que la mesa puede fijar por entrada.
        "tp_rr_min": _env_float("COORDINATOR_TP_RR_MIN", 1.0),
        "tp_rr_max": _env_float("COORDINATOR_TP_RR_MAX", 4.0),
        # Rango del multiplicador de lote (size_mult) que la mesa puede aplicar sobre
        # el lote base del especialista por convicción/piramidación.
        "size_mult_min": _env_float("COORDINATOR_SIZE_MULT_MIN", 0.5),
        "size_mult_max": _env_float("COORDINATOR_SIZE_MULT_MAX", 2.0),
        # Tope DURO de posiciones abiertas por símbolo (perfil de riesgo × horizonte).
        "max_open_positions": max_open_positions,
        # Overrides por símbolo de ese tope (MAX_OPEN_POSITIONS_<SÍMBOLO>): acotan
        # un símbolo concreto sin mover el global. Vacío => todos usan el global.
        "max_open_positions_by_symbol": pos_by_symbol,
        # Registro persistente de antigüedad de posiciones (período de gracia):
        # {ticket: epoch del primer avistamiento}, guardado en la DB (tabla
        # risk_first_seen). Se recarga al arrancar para que la gracia NO se
        # reinicie a cero en cada reinicio de la terminal.
        "persist_first_seen": True,
    }


def get_schedule_config() -> dict:
    """Configuración del planificador de cadencias del orquestador desde .env.

    El bot corre en un único bucle (sin hilos extra para la lógica de trading).
    El tick base es la rotación; las demás tareas se "abren" por tiempo:

    - ROTATION_SECONDS (int, default 60): cada cuánto el orquestador analiza a
      los especialistas y, si procede, convoca la mesa para coordinar/ejecutar.
      Es además el tick base del bucle.
    - NEWS_POLL_SECONDS (int, default 1800 = 30 min): cada cuánto se sondean las
      noticias de los símbolos con agente buscando eventos RED (alto impacto).
    - JUNTA_INTERVAL_SECONDS (int, default 3600 = 1 h): cada cuánto se convoca
      una junta de la mesa para una revisión global del libro, aunque la rotación
      no haya tenido actividad.
    - REPORT_INTERVAL_SECONDS (int, default 7200 = 2 h): cada cuánto se genera el
      reporte y se intenta enviar por correo.

    Reporte / email (ver core/mailer.py). El envío real está APAGADO por defecto
    (SMTP_ENABLED=false): el reporte se genera y se muestra, pero no se manda
    hasta configurar credenciales SMTP.

    - SMTP_ENABLED (bool, default False): activa el envío real por SMTP.
    - SMTP_HOST / SMTP_PORT (default 587) / SMTP_USER / SMTP_PASSWORD: servidor.
    - SMTP_FROM: remitente (si vacío, se usa SMTP_USER).
    - SMTP_USE_TLS (bool, default True): STARTTLS tras conectar.
    - REPORT_EMAIL_TO: destinatario del reporte.
    """
    return {
        "rotation_seconds": _env_int("ROTATION_SECONDS", 60),
        "news_poll_seconds": _env_int("NEWS_POLL_SECONDS", 30 * 60),
        "junta_interval_seconds": _env_int("JUNTA_INTERVAL_SECONDS", 60 * 60),
        "report_interval_seconds": _env_int("REPORT_INTERVAL_SECONDS", 2 * 60 * 60),
        "smtp_enabled": _env_bool("SMTP_ENABLED", False),
        "smtp_host": os.getenv("SMTP_HOST", "").strip(),
        "smtp_port": _env_int("SMTP_PORT", 587),
        "smtp_user": os.getenv("SMTP_USER", "").strip(),
        "smtp_password": os.getenv("SMTP_PASSWORD", ""),
        "smtp_from": os.getenv("SMTP_FROM", "").strip(),
        "smtp_use_tls": _env_bool("SMTP_USE_TLS", True),
        "report_email_to": os.getenv("REPORT_EMAIL_TO", "luismiguel.cano@blankpage.es").strip(),
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
