"""Reinicio + auto-login del terminal MT4 al arrancar el bot.

MT4/MQL4 NO permite cambiar de cuenta desde el EA: el login lo gestiona el propio
terminal. Para "reloguear" la cuenta de forma fiable, cerramos `terminal.exe` y
lo relanzamos con un fichero de configuración de arranque (.ini) que contiene las
credenciales (Login/Password/Server del .env); MetaTrader inicia sesión solo.

Requiere en .env:
  - MT4_TERMINAL_PATH: ruta completa a terminal.exe.
  - MT4_LOGIN / MT4_PASSWORD / MT4_SERVER: credenciales de la cuenta.
Opcional:
  - MT4_RELOGIN_WAIT: segundos a esperar tras relanzar (default 12).

Si MT4_TERMINAL_PATH no está configurado, se OMITE el relogin (el bot sigue con
el terminal ya abierto). Es específico de Windows (terminal.exe / PowerShell).
"""
import os
import time
import subprocess

from core import console


def _kill_terminal(exe_path: str) -> None:
    """Cierra SOLO la instancia de terminal.exe cuyo ejecutable es `exe_path`
    (no toca otros terminales MT4 que pudiera haber abiertos)."""
    # PowerShell: filtra por ruta exacta del ejecutable y mata ese proceso.
    safe = exe_path.replace("'", "''")
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='terminal.exe'\" | "
        f"Where-Object {{ $_.ExecutablePath -eq '{safe}' }} | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       check=False, capture_output=True, timeout=30)
    except Exception as e:  # noqa: BLE001 — un fallo al cerrar no debe abortar el bot
        print("  " + console.warn(f"No se pudo cerrar terminal.exe automáticamente: {e}"))


def _write_startup_ini(path: str, login: str, password: str, server: str) -> None:
    """Escribe el .ini de arranque que MT4 lee al lanzarse para auto-login."""
    lines = [
        "[Common]",
        f"Login={login}",
        f"Password={password}",
        f"Server={server}",
        # Mantener el trading automático activo tras el arranque (para el EA bridge).
        "[Experts]",
        "AllowLiveTrading=true",
        "Enabled=true",
        "AllowDllImport=true",
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def relogin_terminal() -> bool:
    """Cierra y relanza el terminal MT4 con auto-login (credenciales del .env).

    Devuelve True si se intentó el relogin, False si se omitió (no configurado).
    Borra el .ini con la contraseña tras el arranque para no dejarla en disco."""
    exe = os.getenv("MT4_TERMINAL_PATH", "").strip()
    if not exe:
        # Sin ruta del terminal: no hacemos relogin (comportamiento clásico).
        return False
    if not os.path.isfile(exe):
        print("  " + console.warn(f"MT4_TERMINAL_PATH no apunta a un terminal.exe válido: {exe}. "
                                  "Se omite el relogin."))
        return False

    login = os.getenv("MT4_LOGIN", "").strip()
    password = os.getenv("MT4_PASSWORD", "")
    server = os.getenv("MT4_SERVER", "").strip()
    if not (login and password and server):
        print("  " + console.warn("Faltan MT4_LOGIN / MT4_PASSWORD / MT4_SERVER en el .env: "
                                  "se omite el relogin de la cuenta."))
        return False

    wait = float(os.getenv("MT4_RELOGIN_WAIT", "12") or 12)

    print("\n" + console.header("RELOGIN MT4 — reinicio del terminal"))
    print(console.kv("Terminal", console.dim(exe)))
    print(console.kv("Cuenta", f"{login} @ {server}"))

    # 1) Cerrar la instancia actual del terminal.
    print(console.dim("  Cerrando terminal.exe..."))
    _kill_terminal(exe)
    time.sleep(2)

    # 2) Relanzar con el .ini de auto-login (junto al terminal). Se borra después
    #    para no dejar la contraseña en disco.
    ini_path = os.path.join(os.path.dirname(exe), "pb_autologin.ini")
    try:
        _write_startup_ini(ini_path, login, password, server)
        print(console.dim("  Relanzando terminal con auto-login..."))
        subprocess.Popen([exe, ini_path], close_fds=True)
        # Esperar a que el terminal inicie sesión y cargue el EA bridge.
        print(console.dim(f"  Esperando {wait:.0f}s a que MT4 inicie sesión y cargue el EA..."))
        time.sleep(wait)
    except Exception as e:  # noqa: BLE001
        print("  " + console.err(f"✗ Error relanzando el terminal: {e}"))
        return False
    finally:
        # Borrar el .ini con la contraseña en cuanto MT4 lo ha leído.
        try:
            if os.path.exists(ini_path):
                os.remove(ini_path)
        except OSError:
            pass

    print("  " + console.ok("✓ Terminal relanzado. Continuando con la conexión al EA bridge."))
    return True
