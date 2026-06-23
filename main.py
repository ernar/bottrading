import os
import time
import threading
import socket as _socket
from dotenv import load_dotenv

# Tee de stdout/stderr -> buffer en memoria para la pestaña "Terminal" del
# dashboard. Se instala ANTES que el resto de imports del proyecto (console
# reconfigura stdout; api.server fija el logging de stderr al importarse), para
# que la captura abarque también los primeros mensajes del arranque.
from core.console_capture import install_capture
install_capture()

from core import console
from core.state import bot_state
from clients.mt4_client import MT4Client
from clients.base_client import BaseMTClient
from api.server import socketio, app, set_mt_client, set_orchestrator
from agents.registry import list_agents, build_agent
from agents.orchestrator import AgentOrchestrator
from agents.coordinator import RiskBook, CoordinatorAgent, DeterministicCoordinator
from core.llm_config import available_providers
from core.config import get_coordinator_config, get_schedule_config, get_active_agents
from agents.registry import AGENT_BLUEPRINTS
from core.mt4_launcher import relogin_terminal

load_dotenv()


def select_llm(default_provider: str, default_model: str) -> tuple[str, str]:
    """Pregunta el provider/modelo LLM para un agente.

    Solo lista proveedores con clave configurada (gemini por defecto). Enter
    mantiene el modelo por defecto del blueprint."""
    providers = available_providers()
    options: list[tuple[str, str]] = [
        (prov, m) for prov, models in providers.items() for m in models
    ]
    if not options:
        raise RuntimeError(
            "No hay ningún proveedor LLM disponible. Configura una API key "
            "(GEMINI_API_KEY/OPENAI_API_KEY) o activa Ollama (OLLAMA_ENABLED=true)."
        )

    # Si el default del blueprint no está disponible (p. ej. Ollama desactivado
    # en el VPS), usa la primera opción disponible como nuevo default.
    if (default_provider, default_model) not in options:
        default_provider, default_model = options[0]

    print("  Modelo LLM (proveedores con clave configurada):")
    for i, (prov, m) in enumerate(options, 1):
        tag = "  <- por defecto" if (prov == default_provider and m == default_model) else ""
        print(f"    {i:2d}. {prov.upper():7} / {m}{tag}")
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


def _build_saved_agents() -> list:
    """Reconstruye los agentes desde la selección guardada (ACTIVE_AGENTS en .env).
    Omite nombres que ya no existan en el catálogo. Devuelve [] si no hay nada
    válido guardado."""
    saved = get_active_agents()
    agents = []
    for item in saved:
        name = item["name"]
        if name not in AGENT_BLUEPRINTS:
            print("  " + console.warn(f"⚠ '{name}' guardado pero ya no está en el catálogo; omitido."))
            continue
        agent = build_agent(name, provider=item.get("provider"), model=item.get("model"),
                            thinking=item.get("thinking"),
                            reasoning_effort=item.get("reasoning_effort"))
        agent.enabled = item.get("enabled", True)
        agents.append(agent)
    return agents


def select_agents(ask_llm: bool = True) -> list:
    """Lista los agentes especializados disponibles y, para cada uno elegido,
    pregunta el provider/modelo LLM. Devuelve los agentes instanciados.

    Si hay una selección guardada (ACTIVE_AGENTS en .env, desde el botón
    "Guardar selección" del dashboard), la ofrece como opción por defecto para no
    tener que reelegir en cada arranque.

    `ask_llm=False` (perfil DETERMINISTA, SIGNAL_MODE=deterministic): la señal no usa
    LLM, así que NO se pregunta el modelo y NO se requiere ninguna API key — los
    agentes se construyen con el default del blueprint (provider/modelo irrelevantes)."""
    saved_agents = _build_saved_agents()
    if saved_agents:
        print("\n" + console.header("SELECCIÓN DE AGENTES GUARDADA"))
        for a in saved_agents:
            estado = console.ok("ON") if getattr(a, "enabled", True) else console.dim("OFF")
            print(f"  {console.ok('✓')} {console.bold(f'{a.name:<12}')} [{console.info(a.symbol)}] "
                  f"{a.params.provider.upper()}/{a.params.model} · {estado}")
        ans = input("\n¿Usar esta selección guardada? [S/n] (n = elegir manualmente): ").strip().lower()
        if ans in ("", "s", "si", "sí", "y", "yes"):
            return saved_agents
        print(console.dim("  Selección manual..."))

    blueprints = list_agents()

    print("\n" + console.header("SELECCIONAR AGENTES"))
    for i, bp in enumerate(blueprints, 1):
        print(f"  {console.bold(f'{i:2d}.')} {console.bold(f'{bp.name:<12}')} "
              f"[{console.info(bp.symbol)}]")
        print(console.dim(f"      {bp.description}"))
        print(console.dim(f"      Modelo: {bp.params.provider.upper()}/{bp.params.model} | "
                          f"conf>={bp.params.min_confidence:.0%} R:R>=1:{bp.params.min_rr}"))
    print(console.dim("  Ejemplo: 1  |  1,2  |  'all' para todos"))

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

    # 2) Elegir provider/modelo. Se pregunta para el PRIMER agente y, si hay más,
    #    se ofrece reutilizar ese mismo LLM para todos (Enter = sí) y no repetir
    #    la elección agente por agente. Quien quiera modelos distintos responde
    #    'n' y se le pregunta uno a uno como antes.
    agents = []
    shared_provider = shared_model = None
    for i, bp in enumerate(chosen):
        if not ask_llm:
            # Determinista: la señal no usa LLM, no se pregunta el modelo.
            agents.append(build_agent(bp.name))
            print(f"  {console.ok('✓')} {bp.name} [{bp.symbol}] "
                  f"{console.dim('(señal determinista, sin LLM)')}")
            continue
        if shared_provider is not None:
            provider, model = shared_provider, shared_model
        else:
            print(console.accent(f"\n--- LLM para {bp.name} [{bp.symbol}] ---"))
            provider, model = select_llm(bp.params.provider, bp.params.model)
            # Tras elegir el del primer agente, ofrecer aplicarlo a los demás.
            if i == 0 and len(chosen) > 1:
                ans = input(f"  ¿Usar {provider.upper()}/{model} para los {len(chosen)} "
                            f"agentes? [S/n]: ").strip().lower()
                if ans in ("", "s", "si", "sí", "y", "yes"):
                    shared_provider, shared_model = provider, model
        agents.append(build_agent(bp.name, provider=provider, model=model))
        print(f"  {console.ok('✓')} {bp.name} usará {console.bold(f'{provider.upper()}/{model}')}")
    return agents


# LLM por defecto del coordinador si no hay preferencia guardada en .env.
DEFAULT_COORDINATOR_PROVIDER = "gemini"
DEFAULT_COORDINATOR_MODEL = "gemini-3.5-flash"


def select_coordinator_llm(agents: list, cfg: dict) -> tuple:
    """LLM del coordinador (mesa de dirección).

    El director se elige y se cambia EN CALIENTE desde el dashboard (pestaña
    "Mesa" -> POST /api/coordinator/model), que persiste la elección en .env como
    COORDINATOR_PROVIDER/COORDINATOR_MODEL. Aquí se lee esa preferencia para
    arrancar con el mismo director; si no está configurada, cae al default
    gemini-3.5-flash. La mesa está siempre activa (no se pregunta por consola)."""
    provider = (cfg.get("provider") or "").strip().lower()
    model = (cfg.get("model") or "").strip()
    if not provider or not model:
        provider, model = DEFAULT_COORDINATOR_PROVIDER, DEFAULT_COORDINATOR_MODEL
    print("\n" + console.header("LLM DEL COORDINADOR (MESA DE DIRECCIÓN)"))
    print(f"  {console.ok('✓')} Coordinador usará "
          f"{console.bold(f'{provider.upper()}/{model}')}")
    print(console.dim("  (cámbialo en caliente desde el dashboard -> pestaña Mesa)"))
    return provider, model


def apply_trading_profile(agents: list) -> None:
    """Aplica el perfil de trading desde .env a los agentes seleccionados: motor de
    señal (SIGNAL_MODE=llm|deterministic), timeframe (AGENT_TIMEFRAME / AGENT_HIGHER_TIMEFRAME)
    y, en determinista, selectividad (DET_MIN_SCORE) y R:R (ATR_TP_MULT). No-op si no hay
    nada configurado → el comportamiento por defecto (LLM/H1) no cambia.

    Perfil D1 de bajo coste recomendado en .env:
      SIGNAL_MODE=deterministic · AGENT_TIMEFRAME=D1 · AGENT_HIGHER_TIMEFRAME=W1
      COORDINATOR_MODE=deterministic · ATR_TP_MULT=4 · ACTIVE_AGENTS=btc-agent,eth-agent"""
    mode = os.getenv("SIGNAL_MODE", "").strip().lower()
    tf = os.getenv("AGENT_TIMEFRAME", "").strip().upper()
    htf = os.getenv("AGENT_HIGHER_TIMEFRAME", "").strip().upper()
    if not (mode or tf or htf):
        return
    for a in agents:
        upd = {}
        if mode:
            upd["signal_mode"] = mode
        if tf:
            upd["timeframe"] = tf
        if htf:
            upd["higher_timeframe"] = htf
        if mode == "deterministic":
            for env_key, field, cast in (("DET_MIN_SCORE", "det_min_score", int),
                                          ("ATR_TP_MULT", "atr_tp_mult", float),
                                          ("ATR_SL_MULT", "atr_sl_mult", float)):
                raw = os.getenv(env_key, "").strip()
                if raw:
                    try:
                        upd[field] = cast(raw)
                    except ValueError:
                        pass
        try:
            new = a.params.model_copy(update=upd)       # pydantic v2
        except AttributeError:
            new = a.params.copy(update=upd)              # pydantic v1
        a.apply_params(new)
        print(f"  {console.ok('✓')} {a.name}[{a.symbol}] → señal "
              f"{console.bold(new.signal_mode)} · timeframe {console.bold(new.timeframe)}")


def _is_port_in_use(port: int) -> bool:
    with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def build_client() -> BaseMTClient:
    return MT4Client()


def connect_platform(client: BaseMTClient) -> bool:
    print("Conectando a MT4 via EA bridge...")
    print(console.dim("(Asegúrate de que PythonBridge.mq4 esté adjunto a un gráfico en MT4)"))
    for attempt in range(1, 4):
        print(console.dim(f"  Intento {attempt}/3..."))
        if client.connect():
            expected_login = int(os.getenv("MT4_LOGIN", "0"))
            account = client.get_account_info()
            if account and expected_login and account["login"] != expected_login:
                print("  " + console.warn(f"⚠ Advertencia: cuenta conectada ({account['login']}) "
                                          f"distinta a la configurada ({expected_login})"))
            return True
        if attempt < 3:
            print(console.dim("  Sin respuesta del EA, reintentando en 5 segundos..."))
            time.sleep(5)
    print(console.err("✗ Error: No se pudo conectar al EA de MT4 tras 3 intentos."))
    print(console.dim("Verifica que MT4 esté abierto, el EA PythonBridge adjunto a un gráfico"))
    print(console.dim("y que 'Permitir trading automático' esté activado."))
    return False


def main():
    # Base de datos (SQLite): crea el esquema antes de levantar orquestador y API,
    # que comparten el mismo archivo (logs/bot.db por defecto, DB_PATH lo cambia).
    from core.db import init_db
    init_db()

    # ¿Señales DETERMINISTAS? (SIGNAL_MODE). Si lo son, la señal NO usa LLM, así que
    # no se pregunta el modelo de los agentes al arrancar ni se exige API key.
    deterministic_signals = os.getenv("SIGNAL_MODE", "").strip().lower() == "deterministic"
    agents = select_agents(ask_llm=not deterministic_signals)
    # Perfil de trading (.env): motor de señal + timeframe (p. ej. D1 determinista).
    apply_trading_profile(agents)

    # Coordinador: mesa LLM (default) o determinista (sin coste de LLM) según
    # COORDINATOR_MODE. En determinista no se pregunta el modelo por consola.
    coordinator_cfg = get_coordinator_config()
    coord_deterministic = coordinator_cfg.get("mode") == "deterministic"
    coord_provider = coord_model = None
    if not coord_deterministic:
        coord_provider, coord_model = select_coordinator_llm(agents, coordinator_cfg)

    # Relogin de la cuenta MT4: cierra y relanza el terminal con auto-login
    # (credenciales del .env). Se omite si MT4_TERMINAL_PATH no está configurado.
    relogin_terminal()

    client = build_client()
    print(console.accent("\nPlataforma: MT4"))
    if not connect_platform(client):
        return

    print(console.ok("✓ Conectado exitosamente."))
    bot_state.set_connected(True)
    set_mt_client(client)

    account_info = client.get_account_info()
    if account_info:
        plat = account_info.get("platform", "MT4")
        print(console.kv("Cuenta",
                         f"{account_info['login']} {console.dim('|')} "
                         f"Balance {console.money(account_info['balance'])} {console.dim('|')} {plat}"))
        bot_state.update_account(account_info)

    # Aviso si algún agente opera un símbolo que el broker no expone.
    available = set(client.get_symbols() or [])
    if available:
        for agent in agents:
            if agent.symbol not in available:
                print("  " + console.warn(f"⚠ Advertencia: el símbolo {agent.symbol} del agente "
                                          f"'{agent.name}' no aparece en la lista del broker."))

    if _is_port_in_use(5000):
        print("\n" + console.err("[ERROR] Puerto 5000 ya en uso. Cierra la instancia anterior del bot."))
        try:
            import urllib.request
            urllib.request.urlopen("http://localhost:5000/api/notify-duplicate", data=b"", timeout=2)
        except Exception:
            pass
        input("\nPresiona Enter para salir...")
        return

    # 127.0.0.1 por defecto: el API controla operaciones reales (abrir/cerrar),
    # así que no debe quedar expuesto a la red sin querer. Para acceso remoto,
    # pon API_HOST=0.0.0.0 en el .env Y configura API_TOKEN.
    api_host = os.getenv("API_HOST", "127.0.0.1")
    if api_host != "127.0.0.1" and not os.getenv("API_TOKEN", "").strip():
        print("  " + console.warn(f"[SEGURIDAD] API_HOST={api_host} sin API_TOKEN: el bot quedaría "
                                  "controlable por cualquiera en la red. Define API_TOKEN en el .env."))
    api_thread = threading.Thread(
        target=lambda: socketio.run(app, host=api_host, port=5000, debug=False, allow_unsafe_werkzeug=True),
        daemon=True,
    )
    api_thread.start()
    print(f"{console.ok('✓')} API server iniciado en {console.info(f'http://{api_host}:5000')}")

    activos = ", ".join(f"{a.name}[{a.symbol}]" for a in agents)
    print("\n" + console.header("BOT EN MARCHA"))
    print(console.kv("Agentes activos", console.bold(activos)))
    # Resumen claro de USO DE LLM: en el perfil determinista, el trading (señales +
    # mesa) NO usa LLM (coste $0); el LLM solo intervendría en el chat del asistente.
    señal_llm = any(getattr(a.params, "signal_mode", "llm") != "deterministic" for a in agents)
    mesa_llm = not coord_deterministic
    if not señal_llm and not mesa_llm:
        print(console.kv("Uso de LLM", console.ok("NINGUNO en el trading")
                         + console.dim(" (señales y mesa deterministas; solo el chat del asistente usaría LLM)")))
    else:
        partes = []
        if señal_llm:
            partes.append("señales")
        if mesa_llm:
            partes.append("mesa")
        print(console.kv("Uso de LLM", console.warn("activo en " + " y ".join(partes))))
    print(console.dim("  Presiona Ctrl+C para detener"))

    # Mesa de dirección (SIEMPRE activa): el RiskBook (topes duros) es la
    # tesorería; el CoordinatorAgent (LLM, con fail-safe determinista) reparte
    # capital y decide go/no-go por símbolo. Todo el flujo es coordinado.
    risk_book = RiskBook(coordinator_cfg)
    if coord_deterministic:
        coordinator = DeterministicCoordinator(risk_book)
        print(console.kv("Mesa de dirección",
                         f"{console.ok('activa')} {console.bold('DETERMINISTA')} "
                         f"{console.dim('(sin LLM, solo topes/guardias)')}"))
    else:
        coordinator = CoordinatorAgent(
            provider=coord_provider, model=coord_model,
            risk_book=risk_book, temperature=coordinator_cfg["temperature"])
        print(console.kv("Mesa de dirección",
                         f"{console.ok('activa')} con {console.bold(f'{coord_provider.upper()}/{coord_model}')}"))

    # Planificador de cadencias: rotación (tick base), sonda de noticias RED,
    # junta horaria y reporte periódico. Ver get_schedule_config().
    schedule_cfg = get_schedule_config()
    email_tag = console.ok("ON") if schedule_cfg["smtp_enabled"] else console.dim("OFF")
    print(console.dim(f"  Cadencias: rotación {schedule_cfg['rotation_seconds']}s · "
                      f"noticias {schedule_cfg['news_poll_seconds'] // 60}min · "
                      f"junta {schedule_cfg['junta_interval_seconds'] // 60}min · "
                      f"reporte {schedule_cfg['report_interval_seconds'] // 60}min")
          + f" {console.dim('(email')} {email_tag}{console.dim(')')}.")

    # optimize_every_cycles=20 -> ~cada 20 ciclos el orquestador revisa el
    # rendimiento de cada agente y ajusta sus parámetros (0 para desactivar).
    orchestrator = AgentOrchestrator(agents, client, platform="mt4",
                                     optimize_every_cycles=20,
                                     coordinator=coordinator, risk_book=risk_book,
                                     schedule_cfg=schedule_cfg)
    set_orchestrator(orchestrator)
    try:
        orchestrator.run_forever(poll_seconds=schedule_cfg["rotation_seconds"])
    finally:
        bot_state.set_bot_running(False)
        bot_state.set_connected(False)
        client.disconnect()
        print(console.dim("Desconectado."))


if __name__ == "__main__":
    main()
