"""Catálogo de proveedores y modelos LLM disponibles, leído del .env.

Permite que el menú de selección de agentes ofrezca elegir provider/modelo
(ollama, openai, gemini) según las claves realmente configuradas. Sin clave,
un proveedor de pago no aparece en el menú (así no se elige algo que fallaría).
"""
import os


def _split(value: str) -> list[str]:
    return [m.strip() for m in (value or "").split(",") if m.strip()]


def _dedup(items: list[str]) -> list[str]:
    seen: list[str] = []
    for it in items:
        if it and it not in seen:
            seen.append(it)
    return seen


def available_providers() -> dict[str, list[str]]:
    """Devuelve {provider: [modelos]} solo para los proveedores utilizables.

    - ollama: siempre disponible (local); modelos de OLLAMA_MODELS (o MODEL).
    - openai: solo si OPENAI_API_KEY; OPENAI_MODEL + sugeridos.
    - gemini: solo si GEMINI_API_KEY; GEMINI_MODEL + sugeridos.

    El primer modelo de cada lista es el por defecto leído del .env.
    """
    providers: dict[str, list[str]] = {}

    ollama_models = _split(os.getenv("OLLAMA_MODELS")) or [os.getenv("MODEL", "qwen3:8b")]
    providers["ollama"] = _dedup(ollama_models)

    if os.getenv("OPENAI_API_KEY", "").strip():
        default = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        providers["openai"] = _dedup([default, "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o1", "o1-mini"])

    if os.getenv("GEMINI_API_KEY", "").strip():
        default = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        providers["gemini"] = _dedup([default, "gemini-2.0-flash", "gemini-2.5-pro", "gemini-1.5-pro"])

    return providers
