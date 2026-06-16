"""Captura de la salida de consola para replicarla en el dashboard.

Envuelve ``sys.stdout``/``sys.stderr`` con un "tee": cada escritura va a la
consola real (sin cambiar NADA de lo que ve el operador en el VPS) **y** se
acumula en un buffer circular en memoria, línea a línea, con número de
secuencia. El API lo expone en ``GET /api/console?since=<seq>`` para que la
pestaña *Terminal* del dashboard reproduzca por polling incremental lo que
imprime ``python main.py`` — sin tocar la base de datos.

Conserva los códigos de color ANSI (el front los renderiza). Colapsa los ``\\r``
de la animación del spinner para no ensuciar el buffer. Es *fail-safe*: si algo
falla al capturar, la consola real sigue funcionando igual.

Idioma del proyecto: comentarios en español.
"""
import sys
import threading
import time
from collections import deque

# Tamaño del buffer circular (nº de líneas conservadas para el backlog).
_MAX_LINES = 2000


def _broker_clock() -> str:
    """``HH:MM:SS`` en hora del bróker (import perezoso para no acoplar de más;
    cae a la hora local si ``core.clock`` no está listo)."""
    try:
        from core.clock import broker_now
        return broker_now().strftime("%H:%M:%S")
    except Exception:  # noqa: BLE001
        return time.strftime("%H:%M:%S")


class LineBuffer:
    """Buffer circular de líneas finalizadas, seguro entre hilos.

    Acumula escrituras parciales por origen (``out``/``err``) y emite una entrada
    por cada línea completa (terminada en ``\\n``). Colapsa los ``\\r`` (animación
    del spinner) quedándose con el último estado de la línea."""

    def __init__(self, maxlen: int = _MAX_LINES):
        self._lines: deque = deque(maxlen=maxlen)
        self._pending = {"out": "", "err": ""}
        self._seq = 0
        self._lock = threading.Lock()

    def feed(self, source: str, text: str) -> None:
        if not text:
            return
        with self._lock:
            buf = self._pending.get(source, "") + text
            while True:
                nl = buf.find("\n")
                if nl < 0:
                    break
                raw = buf[:nl]
                buf = buf[nl + 1:]
                # Colapsa retrocesos de carro: nos quedamos con lo último escrito.
                if "\r" in raw:
                    raw = raw.rsplit("\r", 1)[-1]
                self._seq += 1
                self._lines.append({
                    "seq": self._seq,
                    "ts": _broker_clock(),
                    "src": source,
                    "text": raw,
                })
            self._pending[source] = buf

    def snapshot(self, since: int = -1, limit: int = 1000) -> dict:
        """Líneas nuevas desde ``since`` (exclusivo). ``since < 0`` devuelve el
        backlog completo del buffer (con ``reset=True`` para que el cliente
        reemplace). ``reset`` también se activa si ``since`` apunta a líneas ya
        purgadas (el buffer giró), señal de que el cliente debe re-sincronizar."""
        with self._lock:
            latest = self._seq
            if not self._lines:
                return {"lines": [], "latest_seq": latest,
                        "oldest_seq": latest, "reset": since < 0}
            oldest = self._lines[0]["seq"]
            if since < 0:
                sel = list(self._lines)
                reset = True
            else:
                sel = [ln for ln in self._lines if ln["seq"] > since]
                reset = since < oldest - 1
            if limit and len(sel) > limit:
                sel = sel[-limit:]
            return {"lines": sel, "latest_seq": latest,
                    "oldest_seq": oldest, "reset": reset}


class _Tee:
    """``stdout``/``stderr`` que escribe a la consola real y al ``LineBuffer``.

    Delega cualquier otro atributo (``encoding``, ``fileno``, ``reconfigure``...)
    en el stream real, así nada aguas arriba nota el envoltorio."""

    def __init__(self, real, sink: LineBuffer, source: str):
        self._real = real
        self._sink = sink
        self._source = source

    def write(self, s):
        try:
            n = self._real.write(s)
        except Exception:  # noqa: BLE001 — nunca tumbar al que imprime
            n = len(s) if isinstance(s, str) else 0
        try:
            self._sink.feed(self._source, s if isinstance(s, str) else str(s))
        except Exception:  # noqa: BLE001 — la captura es secundaria
            pass
        return n

    def flush(self):
        try:
            self._real.flush()
        except Exception:  # noqa: BLE001
            pass

    def isatty(self):
        try:
            return self._real.isatty()
        except Exception:  # noqa: BLE001
            return False

    def __getattr__(self, name):
        return getattr(self._real, name)


_buffer: "LineBuffer | None" = None
_installed = False


def get_capture() -> LineBuffer:
    """Buffer global (lo crea si aún no existe)."""
    global _buffer
    if _buffer is None:
        _buffer = LineBuffer()
    return _buffer


def install_capture() -> LineBuffer:
    """Instala el tee sobre ``sys.stdout``/``sys.stderr`` (idempotente) y
    devuelve el ``LineBuffer`` global. Conviene llamarlo lo antes posible en el
    arranque para no perder los primeros mensajes (selección de agentes,
    conexión a MT, arranque del API)."""
    global _installed
    sink = get_capture()
    if _installed:
        return sink
    sys.stdout = _Tee(sys.stdout, sink, "out")
    sys.stderr = _Tee(sys.stderr, sink, "err")
    _installed = True
    return sink
