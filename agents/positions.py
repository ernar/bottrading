"""Helpers tolerantes para leer posiciones de cualquier plataforma.

MT5 devuelve las posiciones como modelos pydantic (`Position`) y MT4 como
`dict` (parseado del bridge). Estos helpers acceden a los campos de forma
uniforme para que el resto del código (orquestador, coordinador, RiskBook) no
tenga que saber de qué plataforma viene la posición.
"""


def _pos_get(pos, *fields, default=None):
    """Lee el primer campo presente de una posición, sea Position (pydantic/MT5)
    o dict (MT4)."""
    for f in fields:
        if isinstance(pos, dict):
            if pos.get(f) is not None:
                return pos[f]
        else:
            v = getattr(pos, f, None)
            if v is not None:
                return v
    return default


def _pos_to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _pos_direction(pos) -> str:
    """Normaliza la dirección. MT5 da 'BUY'/'SELL'; MT4 da type entero (0=BUY,1=SELL)."""
    d = _pos_get(pos, "direction", "type")
    if d is None:
        return "?"
    s = str(d).upper()
    if s in ("BUY", "SELL"):
        return s
    if s in ("0", "0.0"):
        return "BUY"
    if s in ("1", "1.0"):
        return "SELL"
    return s
