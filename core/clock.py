"""Reloj unificado en HORARIO DEL BRÓKER.

Todo el backend sella sus marcas temporales (señales, trades, equity, memoria,
aperturas/cierres) en la hora de pared del SERVIDOR del bróker, NO en la del
equipo donde corre el bot. Así el dashboard recibe una única referencia
coherente y solo necesita aplicar un desplazamiento FIJO de visualización
(ver `DISPLAY_OFFSET_HOURS` en el front), evitando los desfases que aparecían al
mezclar `datetime.now()` local con epochs del bróker (otra zona horaria).

La hora del bróker se deriva del reloj UTC del equipo (fiable vía NTP) más el
offset GMT del servidor (`MT_SERVER_GMT_OFFSET`, en horas; p. ej. 3 para GMT+3).
Las marcas son *naive* (sin zona) y representan la hora de pared del bróker.
"""
import os
from datetime import datetime, timedelta


def broker_offset_hours() -> float:
    """Offset GMT del servidor del bróker, en horas (`MT_SERVER_GMT_OFFSET`)."""
    try:
        return float(os.getenv("MT_SERVER_GMT_OFFSET", "0") or 0)
    except (TypeError, ValueError):
        return 0.0


def broker_now() -> datetime:
    """Hora de pared ACTUAL del bróker (UTC del equipo + offset GMT). Naive."""
    return datetime.utcnow() + timedelta(hours=broker_offset_hours())


def broker_dt_from_mt_epoch(epoch) -> datetime:
    """Convierte un epoch de MetaTrader a la hora de pared del bróker.

    MT codifica la hora del servidor como si los segundos fuesen UTC, así que
    `utcfromtimestamp` recupera DIRECTAMENTE esa hora de pared (no hay que sumar
    ni restar el offset)."""
    return datetime.utcfromtimestamp(int(epoch))


def broker_dt_from_posix(ts) -> datetime:
    """Convierte un epoch POSIX real (p. ej. de `time.time()`) a la hora de pared
    del bróker (UTC real + offset GMT)."""
    return datetime.utcfromtimestamp(float(ts)) + timedelta(hours=broker_offset_hours())
