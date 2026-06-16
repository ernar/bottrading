"""Esquema de ajustes editables del .env desde el dashboard.

Define QUÉ variables del `.env` se pueden leer/editar por la API, con su tipo,
si son secretas (no se devuelven nunca al front) y si se aplican EN CALIENTE o
requieren reiniciar el bot. La lógica de aplicar en caliente vive en el
orquestador (`reload_runtime_config`); aquí solo está el esquema y la
lectura/escritura del fichero .env preservando comentarios y claves ajenas.

SEGURIDAD: los valores secretos (contraseñas, token) nunca se serializan hacia
el front (solo se informa si están definidos). Al escribir, un secreto vacío se
ignora (no se borra el valor existente al guardar el formulario enmascarado).
"""
import os
from typing import Any

# Cada entrada: (key, label, group, type, secret, hot, help)
#   type:   "bool" | "int" | "float" | "str"
#   secret: True  -> el valor no se devuelve al front; vacío al guardar = no tocar
#   hot:    True  -> se aplica sin reiniciar (orquestador.reload_runtime_config)
SETTINGS_SCHEMA: list[dict] = [
    # --- Riesgo (en caliente) ---
    {"key": "MAX_DAILY_LOSS_PCT", "label": "Pérdida máx. por ventana (%)", "group": "Riesgo", "type": "float", "secret": False, "hot": True,
     "help": "Cooldown por pérdida: deja de abrir y espacia el análisis. Fracción del equity (0.05 = 5%). 0 = off."},
    {"key": "RISK_LOSS_WINDOW_SECONDS", "label": "Ventana de pérdida (s)", "group": "Riesgo", "type": "int", "secret": False, "hot": True,
     "help": "Tamaño de la ventana móvil que rearma el cooldown. 21600 = 6 h."},
    {"key": "MAX_TOTAL_EXPOSURE_PCT", "label": "Exposición total máx.", "group": "Riesgo", "type": "float", "secret": False, "hot": True,
     "help": "Margen usado / equity por encima del cual no se aprueban entradas. 0.5 = 50%."},
    {"key": "MAX_SYMBOL_ALLOCATION_PCT", "label": "Asignación máx. por símbolo", "group": "Riesgo", "type": "float", "secret": False, "hot": True,
     "help": "Fracción del equity asignable a un solo símbolo. 0.4 = 40%."},
    {"key": "MAX_NET_DIRECTION_PCT", "label": "Sesgo neto direccional máx.", "group": "Riesgo", "type": "float", "secret": False, "hot": True,
     "help": "Tope de exposición neta direccional por símbolo. Frena apilar en la dirección saturada."},
    {"key": "MAX_PYRAMID_DIRECTION_PCT", "label": "Sesgo neto máx. al piramidar", "group": "Riesgo", "type": "float", "secret": False, "hot": True,
     "help": "Tope superior del sesgo neto tolerado SOLO al piramidar ganadores (posición en ganancia + tendencia confirma). >= el sesgo neto máx."},
    {"key": "REVERSAL_DRAWDOWN_PCT", "label": "Umbral de reversión", "group": "Riesgo", "type": "float", "secret": False, "hot": True,
     "help": "Pérdida flotante que, con conflicto de tendencia, fuerza reduce/close. 0 = off."},
    {"key": "MAX_SYMBOL_LOSS_PCT", "label": "Hard-stop por símbolo", "group": "Riesgo", "type": "float", "secret": False, "hot": True,
     "help": "Si la pérdida flotante del símbolo supera este % del equity, se fuerza el cierre. 0 = off."},
    {"key": "MIN_HOLD_SECONDS", "label": "Período de gracia (s)", "group": "Riesgo", "type": "float", "secret": False, "hot": True,
     "help": "Las posiciones recién abiertas no se gestionan hasta cumplir esta edad. 300 = 5 min."},

    # --- Coordinador (mezcla hot/reinicio) ---
    # La mesa de dirección está SIEMPRE activa (todo el flujo es coordinado): no
    # hay toggle para desactivarla. El LLM director es FIJO (gemini-3.5-flash),
    # por eso no se ofrece elegir proveedor/modelo aquí.
    {"key": "COORDINATOR_CAN_CLOSE", "label": "Cierre automático (kill-switch)", "group": "Coordinador", "type": "bool", "secret": False, "hot": True,
     "help": "Si es false, ni las guardias deterministas cierran posiciones."},
    {"key": "COORDINATOR_LLM_CAN_CLOSE", "label": "Gestión discrecional del LLM", "group": "Coordinador", "type": "bool", "secret": False, "hot": True,
     "help": "Si es false, la mesa solo cierra por fuerza mayor (hard-stop/reversión)."},
    {"key": "COORDINATOR_TEMPERATURE", "label": "Temperatura del director", "group": "Coordinador", "type": "float", "secret": False, "hot": True,
     "help": "Aleatoriedad del LLM director al decidir la cartera. 0 = casi determinista y "
             "repetible (mismas señales -> mismas decisiones, más disciplinado); subirla da más "
             "variedad/creatividad pero menos consistencia y más riesgo de decisiones erráticas. "
             "Rango útil 0.0-1.0. 0.2 por defecto (estable). No afecta a las guardias de riesgo, "
             "que son deterministas pase lo que pase."},

    # --- Cadencias / ejecución (en caliente) ---
    {"key": "ROTATION_SECONDS", "label": "Rotación (s)", "group": "Cadencias", "type": "int", "secret": False, "hot": True,
     "help": "Tick base del bucle: cada cuánto se analiza/coordina. 60 por defecto."},
    {"key": "NEWS_POLL_SECONDS", "label": "Sonda de noticias (s)", "group": "Cadencias", "type": "int", "secret": False, "hot": True,
     "help": "Cada cuánto se sondean noticias RED. 1800 = 30 min."},
    {"key": "JUNTA_INTERVAL_SECONDS", "label": "Junta global (s)", "group": "Cadencias", "type": "int", "secret": False, "hot": True,
     "help": "Cada cuánto la mesa revisa todo el libro. 3600 = 1 h."},
    {"key": "REPORT_INTERVAL_SECONDS", "label": "Reporte (s)", "group": "Cadencias", "type": "int", "secret": False, "hot": True,
     "help": "Cada cuánto se genera/envía el reporte. 7200 = 2 h."},
    {"key": "PARALLEL_ANALYSIS", "label": "Análisis en paralelo", "group": "Cadencias", "type": "bool", "secret": False, "hot": True,
     "help": "Solapa las llamadas al LLM de los agentes (solo útil con backend concurrente)."},

    # --- Email / SMTP (en caliente vía schedule_cfg) ---
    {"key": "SMTP_ENABLED", "label": "Envío de email activo", "group": "Email (SMTP)", "type": "bool", "secret": False, "hot": True,
     "help": "Si es false, el reporte se genera y se muestra pero no se envía."},
    {"key": "SMTP_HOST", "label": "Servidor SMTP", "group": "Email (SMTP)", "type": "str", "secret": False, "hot": True, "help": "p. ej. smtp.gmail.com"},
    {"key": "SMTP_PORT", "label": "Puerto SMTP", "group": "Email (SMTP)", "type": "int", "secret": False, "hot": True, "help": "587 (STARTTLS) habitual."},
    {"key": "SMTP_USER", "label": "Usuario SMTP", "group": "Email (SMTP)", "type": "str", "secret": False, "hot": True, "help": "Cuenta de envío."},
    {"key": "SMTP_PASSWORD", "label": "Contraseña SMTP", "group": "Email (SMTP)", "type": "str", "secret": True, "hot": True, "help": "Se guarda solo si escribes una nueva."},
    {"key": "SMTP_FROM", "label": "Remitente", "group": "Email (SMTP)", "type": "str", "secret": False, "hot": True, "help": "Vacío = usa el usuario SMTP."},
    {"key": "SMTP_USE_TLS", "label": "STARTTLS", "group": "Email (SMTP)", "type": "bool", "secret": False, "hot": True, "help": "Cifra tras conectar."},
    {"key": "REPORT_EMAIL_TO", "label": "Destinatario del reporte", "group": "Email (SMTP)", "type": "str", "secret": False, "hot": True, "help": "Correo que recibe el informe."},

    # --- Asistente (responsable de la organización) ---
    {"key": "GEMINI_API_KEY", "label": "Token Gemini (API key)", "group": "Asistente", "type": "str", "secret": True, "hot": True,
     "help": "Clave de Google Gemini. La usa el asistente del dashboard (y los agentes Gemini). Se guarda solo si escribes una nueva."},
    {"key": "ASSISTANT_PROVIDER", "label": "Proveedor LLM asistente", "group": "Asistente", "type": "str", "secret": False, "hot": True,
     "help": "gemini / openai / ollama. Por defecto gemini."},
    {"key": "ASSISTANT_MODEL", "label": "Modelo del asistente", "group": "Asistente", "type": "str", "secret": False, "hot": True,
     "help": "Modelo conversacional del asistente. Por defecto gemini-3.5-flash."},

    # --- Noticias ---
    {"key": "NEWS_ENABLED", "label": "Noticias activas", "group": "Noticias", "type": "bool", "secret": False, "hot": False,
     "help": "Calendario económico + titulares en el prompt. Requiere reinicio."},

    # --- Conexión / credenciales (requieren reinicio) ---
    {"key": "MT4_LOGIN", "label": "MT4 login", "group": "Conexión / credenciales", "type": "str", "secret": False, "hot": False, "help": "Número de cuenta MT4."},
    {"key": "MT4_PASSWORD", "label": "MT4 contraseña", "group": "Conexión / credenciales", "type": "str", "secret": True, "hot": False, "help": "Se guarda solo si escribes una nueva."},
    {"key": "MT4_HOST", "label": "MT4 host", "group": "Conexión / credenciales", "type": "str", "secret": False, "hot": False, "help": "Host del puente/terminal."},
    {"key": "MT4_PORT", "label": "MT4 puerto", "group": "Conexión / credenciales", "type": "int", "secret": False, "hot": False, "help": "Puerto del puente."},
    {"key": "MT4_SERVER", "label": "MT4 servidor", "group": "Conexión / credenciales", "type": "str", "secret": False, "hot": False, "help": "Servidor del broker (relogin auto-login)."},
    {"key": "MT4_TERMINAL_PATH", "label": "Ruta terminal.exe (relogin)", "group": "Conexión / credenciales", "type": "str", "secret": False, "hot": False, "help": "Ruta a terminal.exe. Si se define, main.py reinicia el terminal con auto-login al arrancar."},
    {"key": "MT4_RELOGIN_WAIT", "label": "Espera relogin (s)", "group": "Conexión / credenciales", "type": "int", "secret": False, "hot": False, "help": "Segundos a esperar tras relanzar el terminal. Default 12."},
    {"key": "MT_SERVER_GMT_OFFSET", "label": "Offset GMT del servidor MT (h)", "group": "Conexión / credenciales", "type": "float", "secret": False, "hot": True,
     "help": "Horas GMT del servidor del bróker (p. ej. 3 = GMT+3). Corrige la hora de apertura mostrada para que salga en hora local. 0 = sin corrección."},
    {"key": "MODEL", "label": "Modelo LLM por defecto", "group": "Conexión / credenciales", "type": "str", "secret": False, "hot": False, "help": "Modelo base de los agentes. Requiere reinicio."},
    {"key": "SYMBOLS", "label": "Símbolos", "group": "Conexión / credenciales", "type": "str", "secret": False, "hot": False, "help": "Lista de símbolos (coma). Requiere reinicio."},
    {"key": "API_HOST", "label": "API host", "group": "Conexión / credenciales", "type": "str", "secret": False, "hot": False, "help": "127.0.0.1 (local) o 0.0.0.0 (red, exige API_TOKEN)."},
    {"key": "API_TOKEN", "label": "API token", "group": "Conexión / credenciales", "type": "str", "secret": True, "hot": True, "help": "Protege las rutas que mutan estado. Se aplica al guardar."},
]

_BY_KEY = {s["key"]: s for s in SETTINGS_SCHEMA}

# Truthy/falsy coherente con core/config._env_bool.
_FALSE = {"0", "false", "no", "off", ""}


def _env_path() -> str:
    """Ruta al .env junto a la raíz del proyecto (un nivel sobre core/)."""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")


def _typed(spec: dict, raw: str | None) -> Any:
    """Convierte el string del entorno al tipo del esquema para el front."""
    if raw is None:
        return None
    raw = raw.strip()
    t = spec["type"]
    try:
        if t == "bool":
            return raw.lower() not in _FALSE
        if t == "int":
            return int(float(raw)) if raw else None
        if t == "float":
            return float(raw) if raw else None
    except (ValueError, TypeError):
        return None
    return raw


def read_settings() -> list[dict]:
    """Devuelve el esquema con el valor actual (os.getenv) de cada clave.

    Los secretos NO incluyen `value`; en su lugar `is_set` indica si hay valor."""
    out = []
    for spec in SETTINGS_SCHEMA:
        raw = os.getenv(spec["key"])
        entry = {
            "key": spec["key"], "label": spec["label"], "group": spec["group"],
            "type": spec["type"], "secret": spec["secret"], "hot": spec["hot"],
            "help": spec["help"],
        }
        if spec["secret"]:
            entry["is_set"] = bool(raw and raw.strip())
        else:
            entry["value"] = _typed(spec, raw)
        out.append(entry)
    return out


def _normalize_value(spec: dict, value: Any) -> str:
    """Serializa un valor entrante (del front) a string para el .env."""
    t = spec["type"]
    if t == "bool":
        return "true" if (value is True or str(value).strip().lower() not in _FALSE) else "false"
    if t in ("int", "float"):
        if value in (None, ""):
            return ""
        num = float(value)
        return str(int(num)) if t == "int" else repr(num)
    return "" if value is None else str(value)


def validate_and_serialize(updates: dict) -> dict:
    """Valida `updates` ({key: value}) contra el esquema y los serializa a str.

    Reglas:
    - Ignora claves desconocidas (no editables).
    - Un secreto con valor vacío/None se OMITE (no se sobrescribe el existente).
    Devuelve {key: str_value} solo con lo que debe escribirse.
    Lanza ValueError si un valor no encaja con su tipo.
    """
    serialized: dict[str, str] = {}
    for key, value in (updates or {}).items():
        spec = _BY_KEY.get(key)
        if spec is None:
            continue  # clave no editable: se ignora en silencio
        if spec["secret"] and (value is None or str(value).strip() == ""):
            continue  # no tocar secretos al guardar el formulario enmascarado
        if spec["type"] in ("int", "float") and value not in (None, ""):
            try:
                float(value)
            except (ValueError, TypeError):
                raise ValueError(f"{key}: se esperaba un número, llegó {value!r}")
        serialized[key] = _normalize_value(spec, value)
    return serialized


def write_env(serialized: dict) -> list[str]:
    """Escribe los pares clave=valor en el .env preservando comentarios, orden y
    claves ajenas. Actualiza también os.environ del proceso. Devuelve la lista de
    claves realmente modificadas (valor distinto al previo)."""
    path = _env_path()
    lines: list[str] = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()

    remaining = dict(serialized)
    changed: list[str] = []
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            new_lines.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in remaining:
            new_val = remaining.pop(key)
            old_val = line.split("=", 1)[1].strip() if "=" in line else ""
            if old_val != new_val:
                changed.append(key)
            new_lines.append(f"{key}={new_val}")
        else:
            new_lines.append(line)

    # Claves nuevas que no existían en el fichero: se añaden al final.
    for key, val in remaining.items():
        new_lines.append(f"{key}={val}")
        changed.append(key)

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines) + "\n")

    # Refleja en el proceso vivo (os.getenv) lo escrito.
    for key, val in serialized.items():
        os.environ[key] = val

    return changed


def restart_required(changed_keys: list[str]) -> list[str]:
    """De las claves cambiadas, las que NO se aplican en caliente (hot=False)."""
    return [k for k in changed_keys if not _BY_KEY.get(k, {}).get("hot", False)]
