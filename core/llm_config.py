"""Catálogo de proveedores y modelos LLM disponibles, leído del .env.

Permite que el menú de selección de agentes ofrezca elegir provider/modelo
(ollama, openai, gemini) según las claves realmente configuradas. Sin clave,
un proveedor de pago no aparece en el menú (así no se elige algo que fallaría).
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


# Lista estática de respaldo si no se puede consultar la API en vivo (sin red,
# error de SSL, etc.). Incluye los modelos Gemini de uso habitual.
_GEMINI_FALLBACK = [
    "gemini-3.5-flash",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
    "gemini-1.5-pro",
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
    """Modelos Gemini disponibles, con el `default` (del .env) en primer lugar.

    Intenta consultar TODOS los modelos que soportan generateContent en la API
    real. Si la consulta falla, usa la lista estática de respaldo. El resultado
    en vivo se cachea para no repetir la llamada de red."""
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
            _gemini_live_cache = None  # fuerza usar fallback

    # Si la consulta falló o devolvió lista vacía, usa el fallback.
    models = _gemini_live_cache if _gemini_live_cache else _GEMINI_FALLBACK
    return _dedup([default] + list(models))


def available_providers() -> dict[str, list[str]]:
    """Devuelve {provider: [modelos]} solo para los proveedores utilizables.

    - ollama: local; modelos de OLLAMA_MODELS (o MODEL). Se puede desactivar con
      OLLAMA_ENABLED=false (útil en un VPS que trabaja solo con APIs en la nube).
    - openai: solo si OPENAI_API_KEY; OPENAI_MODEL + sugeridos.
    - gemini: solo si GEMINI_API_KEY; modelos consultados en vivo a la API.

    El primer modelo de cada lista es el por defecto leído del .env.
    """
    providers: dict[str, list[str]] = {}

    if _env_bool("OLLAMA_ENABLED", default=False):
        ollama_models = _split(os.getenv("OLLAMA_MODELS")) or [os.getenv("MODEL", "qwen3:8b")]
        providers["ollama"] = _dedup(ollama_models)

    if os.getenv("OPENAI_API_KEY", "").strip():
        default = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        providers["openai"] = _dedup([default, "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o1", "o1-mini"])

    if os.getenv("GEMINI_API_KEY", "").strip():
        default = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        providers["gemini"] = _gemini_models(default)

    return providers
