"""Catálogo de proveedores y modelos LLM disponibles, leído del .env.

Permite que el menú de selección de agentes ofrezca elegir provider/modelo
(ollama, openai, deepseek, gemini) según las claves realmente configuradas. Sin
clave, un proveedor de pago no aparece en el menú (así no se elige algo que
fallaría).
"""
import os
import sys


def _split(value: str) -> list[str]:
    return [m.strip() for m in (value or "").split(",") if m.strip()]


def _dedup(items: list[str]) -> list[str]:
    seen: list[str] = []
    for it in items:
        if it and it not in seen:
            seen.append(it)
    return seen


def _env_bool(name: str, default: bool) -> bool:
    """Lee un flag booleano del entorno. Falsos: 0/false/no/off (sin distinguir
    mayúsculas). Cualquier otro valor no vacío es verdadero."""
    val = os.getenv(name)
    if val is None or val.strip() == "":
        return default
    return val.strip().lower() not in ("0", "false", "no", "off")


# Modelos Gemini RECOMENDADOS (curados, en orden de preferencia). Solo estos se
# ofrecen en el selector — para los agentes (señales) y para la mesa (director) —
# en vez del catálogo completo de la API, que lista decenas de variantes. El
# listado en vivo se INTERSECTA con esta allowlist (así solo se ofrece lo que la
# cuenta realmente tiene Y está recomendado); si la consulta falla, se usa esta
# lista tal cual. Ampliable/forzable con GEMINI_MODELS (coma) en el .env.
#   - gemini-3.5-flash: rápido y potente, buen equilibrio (señales y mesa).
#   - gemini-2.5-pro:   razonamiento más fuerte, ideal para la mesa.
#   - gemini-2.5-flash / gemini-2.0-flash: rápidos y económicos para señales.
_GEMINI_RECOMMENDED = [
    "gemini-3.5-flash",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
]

# Subcadenas que descartan modelos NO aptos para análisis de texto/trading:
# generación de imágenes, TTS/audio, embeddings, etc.
_GEMINI_EXCLUDE = (
    "image", "imagen", "tts", "audio", "embedding", "embed",
    "vision", "live", "aqa", "veo", "learnlm",
)

# Caché del listado en vivo: la API de modelos no cambia entre ciclos, así que
# evitamos una llamada de red cada vez que el dashboard pide /providers.
_gemini_live_cache: list[str] | None = None


def _gemini_models(default: str) -> list[str]:
    """Modelos Gemini RECOMENDADOS, con el `default` (del .env) en primer lugar.

    Consulta los modelos reales de la cuenta (los que soportan generateContent) y
    los INTERSECTA con la allowlist `_GEMINI_RECOMMENDED`: así el selector solo
    ofrece modelos curados que además existen para la API key. Si la consulta
    falla, usa la lista recomendada tal cual. `GEMINI_MODELS` (coma) en el .env
    fuerza una lista manual y se salta el filtro. El listado en vivo se cachea."""
    # Override manual: si GEMINI_MODELS está puesto, manda esa lista tal cual.
    override = _split(os.getenv("GEMINI_MODELS"))
    if override:
        return _dedup([default] + override)

    global _gemini_live_cache

    if _gemini_live_cache is None:
        try:
            from google import genai
            client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
            live: list[str] = []
            for m in client.models.list():
                actions = getattr(m, "supported_actions", None) or []
                if "generateContent" not in actions:
                    continue
                name = (getattr(m, "name", "") or "").split("/")[-1]
                if not name.startswith("gemini"):
                    continue
                if any(bad in name.lower() for bad in _GEMINI_EXCLUDE):
                    continue  # descarta imagen/TTS/embeddings/etc.
                live.append(name)
            # Más recientes primero (orden inverso por nombre).
            _gemini_live_cache = sorted(set(live), reverse=True)
        except Exception as e:
            print(f"⚠️  Gemini: no se pudieron listar modelos en vivo. Error: {e}",
                  file=sys.stderr)
            print(f"   Usando lista estática de respaldo. Comprueba:",
                  file=sys.stderr)
            print(f"   - Conexión a internet y firewall",
                  file=sys.stderr)
            print(f"   - API key válida (GEMINI_API_KEY en .env)",
                  file=sys.stderr)
            _gemini_live_cache = None  # fuerza usar la lista recomendada

    # Filtra a los recomendados: si la API devolvió modelos, intersecta con la
    # allowlist (manteniendo el orden de preferencia de _GEMINI_RECOMMENDED); si
    # la consulta falló o ninguno coincide, usa la lista recomendada completa.
    live = _gemini_live_cache or []
    models = [m for m in _GEMINI_RECOMMENDED if m in live] or _GEMINI_RECOMMENDED
    return _dedup([default] + models)


def available_providers() -> dict[str, list[str]]:
    """Devuelve {provider: [modelos]} solo para los proveedores utilizables.

    - ollama: local; modelos de OLLAMA_MODELS (o MODEL). Se puede desactivar con
      OLLAMA_ENABLED=false (útil en un VPS que trabaja solo con APIs en la nube).
    - openai: solo si OPENAI_API_KEY; OPENAI_MODEL + sugeridos.
    - deepseek: solo si DEEPSEEK_API_KEY; DEEPSEEK_MODEL + DEEPSEEK_MODELS (extra) +
      deepseek-v4-flash/pro y deepseek-chat/reasoner.
    - gemini: solo si GEMINI_API_KEY; modelos recomendados (allowlist curada
      intersectada con los reales de la cuenta), o GEMINI_MODELS si se fuerza.

    El primer modelo de cada lista es el por defecto leído del .env.
    """
    providers: dict[str, list[str]] = {}

    if _env_bool("OLLAMA_ENABLED", default=False):
        ollama_models = _split(os.getenv("OLLAMA_MODELS")) or [os.getenv("MODEL", "qwen3:8b")]
        providers["ollama"] = _dedup(ollama_models)

    if os.getenv("OPENAI_API_KEY", "").strip():
        default = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        providers["openai"] = _dedup([default, "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o1", "o1-mini"])

    if os.getenv("DEEPSEEK_API_KEY", "").strip():
        # Catálogo estático (como OpenAI). deepseek-chat/reasoner quedan obsoletos
        # el 2026/07/24; deepseek-v4-flash/pro son los sucesores. Extra modelos
        # vía DEEPSEEK_MODELS (coma) para no tocar código si DeepSeek añade más.
        default = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        extra = _split(os.getenv("DEEPSEEK_MODELS"))
        providers["deepseek"] = _dedup([default] + extra + [
            "deepseek-v4-flash", "deepseek-v4-pro",
            "deepseek-chat", "deepseek-reasoner",
        ])

    if os.getenv("GEMINI_API_KEY", "").strip():
        default = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        providers["gemini"] = _gemini_models(default)

    return providers
