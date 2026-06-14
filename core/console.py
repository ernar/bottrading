"""Helpers de presentación para la terminal del bot.

Centraliza el estilo de la salida en consola para que el flujo se lea bien:
color semántico (verde = bien/profit, rojo = mal/pérdida/veto, amarillo =
aviso/conflicto, atenuado = secundario), reglas y cabeceras, tablas alineadas y
formato de números (dinero y P/L con signo/color).

Es *fail-safe*: si ``colorama`` no está instalado, la salida no es una terminal
(salida redirigida) o se fija ``NO_COLOR`` / ``BOT_NO_COLOR``, degrada a texto
plano sin romper nada. ``BOT_FORCE_COLOR`` fuerza el color (útil al redirigir).

Idioma del proyecto: comentarios y textos en español.
"""
import os
import sys

# Ancho de referencia de reglas/cabeceras (coincide con el del reporte por email).
WIDTH = 56

# Asegura UTF-8 en la salida para que los glifos (─ ═ → ✓ y emojis) no revienten
# con UnicodeEncodeError en consolas cp1252; los no representables se sustituyen
# en vez de tumbar el proceso. Es idempotente y fail-safe.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — flujo ya envuelto / sin reconfigure
        pass

# --- Inicialización de color (Windows-safe vía colorama, opcional) ---
try:  # colorama habilita los códigos ANSI en consolas Windows antiguas.
    import colorama
    if hasattr(colorama, "just_fix_windows_console"):
        colorama.just_fix_windows_console()
    else:  # colorama < 0.4.6
        colorama.init()
    _HAS_COLORAMA = True
except Exception:  # noqa: BLE001 — sin colorama seguimos en texto plano
    _HAS_COLORAMA = False


def _color_enabled() -> bool:
    if os.getenv("BOT_FORCE_COLOR"):
        return True
    if os.getenv("NO_COLOR") is not None or os.getenv("BOT_NO_COLOR"):
        return False
    if not _HAS_COLORAMA:
        return False
    try:
        return bool(sys.stdout.isatty())
    except Exception:  # noqa: BLE001
        return False


_ENABLED = _color_enabled()

# Códigos ANSI (SGR).
_CODES = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
}


def _style(text: str, *names: str) -> str:
    """Envuelve `text` con los estilos indicados (no-op si el color está off)."""
    if not _ENABLED or not names:
        return str(text)
    prefix = "".join(_CODES[n] for n in names if n in _CODES)
    return f"{prefix}{text}{_CODES['reset']}"


# --- Estilos con nombre (semánticos) ---
def ok(t):      return _style(t, "green")        # noqa: E704
def err(t):     return _style(t, "red")          # noqa: E704
def warn(t):    return _style(t, "yellow")       # noqa: E704
def info(t):    return _style(t, "cyan")         # noqa: E704
def accent(t):  return _style(t, "magenta")      # noqa: E704
def dim(t):     return _style(t, "dim")          # noqa: E704
def bold(t):    return _style(t, "bold")         # noqa: E704


# --- Estructura: reglas y cabeceras ---
def rule(title: str = None, char: str = "─", width: int = WIDTH,
         style=dim) -> str:
    """Una línea divisoria. Con `title`, lo incrusta a la izquierda
    (``── Título ──────``). Devuelve la cadena ya estilizada."""
    if not title:
        return style(char * width)
    head = f"{char * 2} {title} "
    tail = char * max(0, width - _vlen(head))
    return style(head + tail)


def header(title: str, char: str = "═", width: int = WIDTH, style=bold) -> str:
    """Cabecera de sección a tres líneas (regla / título / regla)."""
    line = dim(char * width)
    return f"{line}\n  {style(title)}\n{line}"


def kv(label: str, value, label_w: int = 16, label_style=dim) -> str:
    """Par etiqueta/valor alineado: ``  Etiqueta     valor``."""
    return f"  {label_style(f'{label}:'.ljust(label_w))} {value}"


# --- Tablas alineadas (con color por celda, manteniendo el ancho) ---
def _vlen(text: str) -> int:
    """Longitud *visible* (ignora los códigos ANSI)."""
    out, i = 0, 0
    while i < len(text):
        if text[i] == "\033":
            j = text.find("m", i)
            if j == -1:
                break
            i = j + 1
            continue
        out += 1
        i += 1
    return out


def _cell_plain(cell) -> str:
    """Texto plano de una celda (acepta ``str`` o ``(texto, estilo)``)."""
    return str(cell[0]) if isinstance(cell, tuple) else str(cell)


def _cell_render(cell, width: int, align: str) -> str:
    """Rellena al ancho de columna y luego aplica el estilo, para no romper la
    alineación (el padding queda dentro del color)."""
    text = _cell_plain(cell)
    padded = f"{text:{align}{width}}"
    if isinstance(cell, tuple) and cell[1]:
        return cell[1](padded)
    return padded


def table(headers: list, rows: list, aligns: list = None,
          indent: str = "  ", gap: str = "  ") -> list:
    """Devuelve las líneas de una tabla alineada (cabecera + separador + filas).

    Cada celda puede ser un ``str`` o una tupla ``(texto, fn_estilo)`` para
    colorear sin descuadrar columnas. `aligns` usa '<' / '>' / '^' por columna.
    """
    cols = len(headers)
    aligns = aligns or ["<"] * cols
    widths = [_vlen(str(headers[c])) for c in range(cols)]
    for row in rows:
        for c in range(cols):
            widths[c] = max(widths[c], _vlen(_cell_plain(row[c])))

    lines = [indent + gap.join(
        bold(f"{str(headers[c]):{aligns[c]}{widths[c]}}") for c in range(cols))]
    lines.append(indent + gap.join(dim("─" * widths[c]) for c in range(cols)))
    for row in rows:
        lines.append(indent + gap.join(
            _cell_render(row[c], widths[c], aligns[c]) for c in range(cols)))
    return lines


# --- Formato de números ---
def money(value) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def pnl(value, with_money: bool = True) -> str:
    """Dinero con signo y color (verde >= 0, rojo < 0)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    s = f"${v:+,.2f}" if with_money else f"{v:+.2f}"
    return ok(s) if v >= 0 else err(s)


def pct(value, dp: int = 0) -> str:
    try:
        return f"{float(value):.{dp}%}"
    except (TypeError, ValueError):
        return "n/a"


def side(action: str) -> str:
    """Colorea una dirección/acción de trading: BUY verde, SELL rojo, HOLD/otro
    atenuado. Compara ignorando el relleno (`strip`) para poder usarse como
    estilizador de celdas de tabla, donde el texto llega ya rellenado al ancho."""
    a = str(action or "")
    key = a.upper().strip()
    if key == "BUY":
        return ok(a)
    if key == "SELL":
        return err(a)
    return dim(a if a.strip() else "—")
