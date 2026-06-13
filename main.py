import os
import time
import threading
import socket as _socket
from dotenv import load_dotenv

from core.state import bot_state
from clients.mt5_client import MT5Client
from clients.mt4_client import MT4Client
from clients.base_client import BaseMTClient
from api.server import socketio, app, set_mt_client, set_orchestrator
from agents.registry import list_agents, build_agent
from agents.orchestrator import AgentOrchestrator
from core.llm_config import available_providers

load_dotenv()


def select_platform() -> str:
    print("\n" + "=" * 50)
    print("       SELECCIONAR PLATAFORMA")
    print("=" * 50)
    print("  1. MT5  (RoboForex ECN)")
    print("  2. MT4  (RoboForex MT4)")
    print("=" * 50)
    while True:
        choice = input("Tu elección [1/2]: ").strip()
        if choice == "1":
            return "mt5"
        if choice == "2":
            return "mt4"
        print("  Opción inválida. Escribe 1 o 2.")


def select_llm(default_provider: str, default_model: str) -> tuple[str, str]:
    """Pregunta el provider/modelo LLM para un agente.

    Solo lista proveedores con clave configurada (ollama siempre). Enter
    mantiene el modelo por defecto del blueprint."""
    providers = available_providers()
    options: list[tuple[str, str]] = []
    print("  Modelo LLM (proveedores con clave configurada):")
    for prov, models in providers.items():
        for m in models:
            options.append((prov, m))
            tag = "  <- por defecto" if (prov == default_provider and m == default_model) else ""
            print(f"    {len(options):2d}. {prov.upper():7} / {m}{tag}")
    print(f"    Enter = mantener {default_provider.upper()}/{default_model}")

    while True:
        choice = input("  Elige modelo: ").strip()
        if not choice:
            return default_provider, default_model
        try:
            idx = int(choice)
            if 1 <= idx <= len(options):
                return options[idx - 1]
        except ValueError:
            pass
        print("    Opción inválida.")


def select_agents() -> list:
    """Lista los agentes especializados disponibles y, para cada uno elegido,
    pregunta el provider/modelo LLM. Devuelve los agentes instanciados."""
    blueprints = list_agents()

    print("\n" + "=" * 50)
    print("       SELECCIONAR AGENTES")
    print("=" * 50)
    for i, bp in enumerate(blueprints, 1):
        print(f"  {i:2d}. {bp.name:<12} [{bp.symbol}]")
        print(f"      {bp.description}")
        print(f"      Modelo: {bp.params.provider.upper()}/{bp.params.model} | "
              f"conf>={bp.params.min_confidence:.0%} R:R>=1:{bp.params.min_rr}")
    print("=" * 50)
    print("  Ejemplo: 1  |  1,2  |  'all' para todos")

    # 1) Elegir qué agentes
    chosen: list = []
    while True:
        choice = input("\nTu elección: ").strip().lower()
        if choice == "all":
            chosen = list(blueprints)
            break
        try:
            indices = [int(x.strip()) for x in choice.split(",")]
        except ValueError:
            print("  Entrada inválida. Escribe números separados por coma o 'all'.")
            continue
        for idx in indices:
            if 1 <= idx <= len(blueprints):
                chosen.append(blueprints[idx - 1])
            else:
                print(f"  Índice {idx} fuera de rango, omitido")
        if chosen:
            break
        print("  No seleccionaste ningún agente válido.")

    # 2) Elegir provider/modelo para cada agente seleccionado
    agents = []
    for bp in chosen:
        print(f"\n--- LLM para {bp.name} [{bp.symbol}] ---")
        provider, model = select_llm(bp.params.provider, bp.params.model)
        agents.append(build_agent(bp.name, provider=provider, model=model))
        print(f"  {bp.name} usará {provider.upper()}/{model}")
    return agents


def _is_port_in_use(port: int) -> bool:
    with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def build_client(version: str) -> BaseMTClient:
    if version == "mt4":
        return MT4Client()
    return MT5Client()


def connect_platform(client: BaseMTClient, version: str) -> bool:
    if version == "mt4":
        print("Conectando a MT4 via EA bridge...")
        print("(Asegúrate de que PythonBridge.mq4 esté adjunto a un gráfico en MT4)")
        for attempt in range(1, 4):
            print(f"  Intento {attempt}/3...")
            if client.connect():
                expected_login = int(os.getenv("MT4_LOGIN", "0"))
                account = client.get_account_info()
                if account and expected_login and account["login"] != expected_login:
                    print(f"  Advertencia: cuenta conectada ({account['login']}) "
                          f"distinta a la configurada ({expected_login})")
                return True
            if attempt < 3:
                print("  Sin respuesta del EA, reintentando en 5 segundos...")
                time.sleep(5)
        print("Error: No se pudo conectar al EA de MT4 tras 3 intentos.")
        print("Verifica que MT4 esté abierto, el EA PythonBridge adjunto a un gráfico")
        print("y que 'Permitir trading automático' esté activado.")
        return False

    login = int(os.getenv("MT5_LOGIN", "0"))
    password = os.getenv("MT5_PASSWORD", "")
    server = os.getenv("MT5_SERVER", "")
    print("Conectando a MT5...")
    if not client.connect(login=login, password=password, server=server):
        print("Error: No se pudo conectar a MT5")
        return False
    return True


def main():
    version = select_platform()
    agents = select_agents()

    client = build_client(version)
    print(f"\nPlataforma: {version.upper()}")
    if not connect_platform(client, version):
        return

    print("Conectado exitosamente.")
    bot_state.set_connected(True)
    set_mt_client(client)

    account_info = client.get_account_info()
    if account_info:
        plat = account_info.get("platform", version.upper())
        print(f"Cuenta: {account_info['login']} | Balance: ${account_info['balance']:.2f} | {plat}")
        bot_state.update_account(account_info)

    # Aviso si algún agente opera un símbolo que el broker no expone.
    available = set(client.get_symbols() or [])
    if available:
        for agent in agents:
            if agent.symbol not in available:
                print(f"  Advertencia: el símbolo {agent.symbol} del agente "
                      f"'{agent.name}' no aparece en la lista del broker.")

    if _is_port_in_use(5000):
        print("\n[ERROR] Puerto 5000 ya en uso. Cierra la instancia anterior del bot.")
        try:
            import urllib.request
            urllib.request.urlopen("http://localhost:5000/api/notify-duplicate", data=b"", timeout=2)
        except Exception:
            pass
        input("\nPresiona Enter para salir...")
        return

    api_thread = threading.Thread(
        target=lambda: socketio.run(app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True),
        daemon=True,
    )
    api_thread.start()
    print("API server iniciado en http://localhost:5000")

    print("\n" + "=" * 50)
    print(f"  Agentes activos: {', '.join(f'{a.name}[{a.symbol}]' for a in agents)}")
    print("  Presiona Ctrl+C para detener")
    print("=" * 50)

    # optimize_every_cycles=20 -> ~cada 20 ciclos el orquestador revisa el
    # rendimiento de cada agente y ajusta sus parámetros (0 para desactivar).
    orchestrator = AgentOrchestrator(agents, client, platform=version,
                                     optimize_every_cycles=20)
    set_orchestrator(orchestrator)
    try:
        orchestrator.run_forever(poll_seconds=60)
    finally:
        bot_state.set_bot_running(False)
        bot_state.set_connected(False)
        client.disconnect()
        print("Desconectado.")


if __name__ == "__main__":
    main()
