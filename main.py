import os
import time
import threading
import socket as _socket
from dotenv import load_dotenv

from core.models import BotConfig
from core.state import bot_state
from core.strategy import StrategyEngine
from core.logger import log_signal, log_trade
from core.memory import SignalMemory
from core.market_context import build_market_context
from core.news import news_provider
from clients.mt5_client import MT5Client
from clients.mt4_client import MT4Client
from clients.base_client import BaseMTClient
from api.server import socketio, app, set_mt_client

load_dotenv()


def load_config() -> BotConfig:
    model = os.getenv("MODEL", "qwen3:8b")
    symbols_str = os.getenv("SYMBOLS", "WTI,BTCUSD")

    symbol_map = {
        "WTI": "USOIL",
        "BTCUSD": "BTCUSD",
        "EURUSD": "EURUSD",
        "GBPUSD": "GBPUSD",
        "XAUUSD": "XAUUSD",
    }

    symbols = [symbol_map.get(s.strip(), s.strip()) for s in symbols_str.split(",")]

    return BotConfig(
        model=model,
        symbols=symbols,
        default_lot_size=0.01,
        max_spread_filter=2.0,
        risk_per_trade=0.02,
        max_open_positions=5,
        debug_mode=True,
    )


def calc_trade_metrics(client: BaseMTClient, symbol: str, action: str,
                       entry: float, stop_loss: float, take_profit: float,
                       volume: float, commission_per_lot: float = 7.0) -> dict:
    sym = client.get_symbol_info(symbol)
    if not sym or not entry or not stop_loss or not take_profit:
        return {}

    point = sym.point
    tick_value = getattr(sym, "trade_tick_value", 1.0)

    direction = 1 if action == "BUY" else -1
    pips_tp = direction * (take_profit - entry) / point
    pips_sl = direction * (entry - stop_loss) / point

    potential_profit = pips_tp * tick_value * volume
    potential_loss = pips_sl * tick_value * volume
    commission = commission_per_lot * volume

    rr = round(pips_tp / pips_sl, 2) if pips_sl else 0

    return {
        "potential_profit": round(potential_profit, 2),
        "potential_loss": round(potential_loss, 2),
        "commission": round(commission, 2),
        "net_profit": round(potential_profit - commission, 2),
        "net_loss": round(potential_loss + commission, 2),
        "rr": rr,
        "pips_tp": round(pips_tp, 1),
        "pips_sl": round(pips_sl, 1),
    }


def show_loading(message: str):
    for i in range(4):
        time.sleep(0.5)
        print(f"\r{message} {'.' * i}", end="", flush=True)
    print()


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


def select_model() -> tuple[str, str]:
    """Devuelve (provider, model_name)."""
    ollama_models = [m.strip() for m in os.getenv("OLLAMA_MODELS", "qwen3:8b").split(",") if m.strip()]
    openai_key = os.getenv("OPENAI_API_KEY", "")
    gemini_key = os.getenv("GEMINI_API_KEY", "")

    options = [("ollama", m) for m in ollama_models]
    if openai_key:
        options.append(("openai", os.getenv("OPENAI_MODEL", "gpt-4o-mini")))
    if gemini_key:
        options.append(("gemini", os.getenv("GEMINI_MODEL", "gemini-2.0-flash")))

    print("\n" + "=" * 50)
    print("       SELECCIONAR MODELO DE IA")
    print("=" * 50)
    for i, (prov, name) in enumerate(options, 1):
        print(f"  {i:2d}. [{prov.upper():<6}] {name}")
    print("=" * 50)

    default_model = os.getenv("MODEL", "qwen3:8b")
    default_idx = next((i for i, (_, m) in enumerate(options) if m == default_model), 0)

    while True:
        choice = input(f"Tu elección [1-{len(options)}] (Enter = {options[default_idx][1]}): ").strip()
        if choice == "":
            return options[default_idx]
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(options):
                return options[idx]
        print(f"  Opción inválida. Escribe un número entre 1 y {len(options)}.")


def select_symbols(client: BaseMTClient, available_symbols: list) -> list:
    print("\n" + "=" * 50)
    print("       SELECTOR DE SÍMBOLOS")
    print("=" * 50)

    for i, symbol in enumerate(available_symbols, 1):
        info = client.get_symbol_info(symbol)
        if info:
            print(f"  {i:2d}. {symbol:<15} | Spread: {info.spread}")

    print("\n  Ejemplo: 1,3,5  |  'all' para todos  |  'custom' para escribir nombres")
    choice = input("\nTu elección: ").strip().lower()

    if choice == "all":
        return available_symbols
    if choice == "custom":
        raw = input("Símbolos (separados por coma): ").strip()
        return [s.strip() for s in raw.split(",")]

    try:
        indices = [int(x.strip()) for x in choice.split(",")]
        selected = []
        for idx in indices:
            if 1 <= idx <= len(available_symbols):
                selected.append(available_symbols[idx - 1])
            else:
                print(f"  Índice {idx} fuera de rango, omitido")
        return selected
    except ValueError:
        print("  Entrada inválida, usando configuración por defecto")
        return load_config().symbols


def analyze_symbol(client: BaseMTClient, strategy: StrategyEngine, symbol: str,
                   memory: SignalMemory, platform: str = "mt5") -> dict:
    print(f"\n{'=' * 50}")
    print(f"  Analizando {symbol}...")

    tick = client.get_tick(symbol)
    if tick:
        print(f"  Precio: Ask={tick.ask} | Bid={tick.bid}")
        # Evaluar señales anteriores contra el precio actual (feedback para la IA)
        memory.evaluate_pending(symbol, (tick.ask + tick.bid) / 2)

    positions = client.get_positions(symbol)
    market_data = build_market_context(
        client, symbol,
        positions=positions,
        memory_summary=memory.get_summary(symbol),
        news_context=news_provider.get_news_context(symbol),
    )

    show_loading("  Generando análisis")

    signal = strategy.generate_signal(symbol, positions, market_data=market_data)
    if not signal:
        print("  No se generó señal.")
        return None

    if signal["action"] != "HOLD" and (not signal.get("stop_loss") or signal["stop_loss"] == 0):
        atr = client.get_atr(symbol)
        sym_info = client.get_symbol_info(symbol)
        digits = sym_info.digits if sym_info else 5
        if atr > 0 and tick:
            entry = tick.ask if signal["action"] == "BUY" else tick.bid
            if signal["action"] == "BUY":
                signal["stop_loss"] = round(entry - 1.5 * atr, digits)
                if not signal.get("take_profit") or signal["take_profit"] == 0:
                    signal["take_profit"] = round(entry + 2.0 * atr, digits)
            else:
                signal["stop_loss"] = round(entry + 1.5 * atr, digits)
                if not signal.get("take_profit") or signal["take_profit"] == 0:
                    signal["take_profit"] = round(entry - 2.0 * atr, digits)
            print(f"  [ATR={atr:.5f}] SL/TP calculados automáticamente")

    signal["platform"] = platform.upper()
    bot_state.update_signal(signal)
    log_signal(signal, platform=platform)
    if tick:
        ref_price = tick.ask if signal["action"] == "BUY" else tick.bid
        memory.record_signal(symbol, signal, ref_price)
    return signal


def _is_port_in_use(port: int) -> bool:
    with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def build_client(version: str) -> BaseMTClient:
    if version == "mt4":
        return MT4Client()
    return MT5Client()


def main():
    config = load_config()

    version = select_platform()
    provider, model_name = select_model()

    config.model = model_name
    print(f"\n  Proveedor: {provider.upper()} | Modelo: {model_name}")

    strategy = StrategyEngine(config, provider=provider)
    client = build_client(version)
    memory = SignalMemory()

    print(f"\nPlataforma: {version.upper()}")

    if version == "mt4":
        print("Conectando a MT4 via EA bridge...")
        print("(Asegúrate de que PythonBridge.mq4 esté adjunto a un gráfico en MT4)")
        connected = False
        for attempt in range(1, 4):
            print(f"  Intento {attempt}/3...")
            if client.connect():
                connected = True
                break
            if attempt < 3:
                print("  Sin respuesta del EA, reintentando en 5 segundos...")
                time.sleep(5)
        if not connected:
            print("Error: No se pudo conectar al EA de MT4 tras 3 intentos.")
            print("Verifica que MT4 esté abierto, el EA PythonBridge adjunto a un gráfico")
            print("y que 'Permitir trading automático' esté activado.")
            return
        expected_login = int(os.getenv("MT4_LOGIN", "0"))
        account = client.get_account_info()
        if account and expected_login and account["login"] != expected_login:
            print(f"  Advertencia: cuenta conectada ({account['login']}) distinta a la configurada ({expected_login})")
    else:
        login = int(os.getenv("MT5_LOGIN", "0"))
        password = os.getenv("MT5_PASSWORD", "")
        server = os.getenv("MT5_SERVER", "")
        print("Conectando a MT5...")
        if not client.connect(login=login, password=password, server=server):
            print("Error: No se pudo conectar a MT5")
            return

    print("Conectado exitosamente.")
    bot_state.set_connected(True)
    set_mt_client(client)

    account_info = client.get_account_info()
    if account_info:
        plat = account_info.get("platform", version.upper())
        print(f"Cuenta: {account_info['login']} | Balance: ${account_info['balance']:.2f} | {plat}")
        bot_state.update_account(account_info)

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

    available_symbols = client.get_symbols()
    if not available_symbols:
        print("No se pudieron obtener símbolos disponibles")
        return

    selected_symbols = select_symbols(client, available_symbols)
    if not selected_symbols:
        print("No se seleccionaron símbolos. Usando configuración por defecto.")
        selected_symbols = config.symbols

    print("\n" + "=" * 50)
    print(f"  Símbolos activos: {', '.join(selected_symbols)}")
    print("  Presiona Ctrl+C para detener")
    print("=" * 50)

    bot_state.set_bot_running(True)

    try:
        while True:
            account_info = client.get_account_info()
            if account_info:
                bot_state.update_account(account_info)

            if not bot_state.bot_running:
                time.sleep(5)
                continue

            for symbol in selected_symbols:
                signal = analyze_symbol(client, strategy, symbol, memory, platform=version)
                if not signal:
                    continue

                print(f"\n  Señal: {signal['action']} | Confianza: {signal['confidence']:.0%}")
                print(f"  Tendencia: {signal.get('trend', 'N/A')} | Riesgo: {signal.get('risk_level', 'N/A')}")
                if signal.get("entry"):
                    print(f"  Entry: {signal['entry']} | SL: {signal['stop_loss']} | TP: {signal['take_profit']}")
                    metrics = calc_trade_metrics(
                        client, symbol, signal["action"],
                        signal["entry"], signal["stop_loss"], signal["take_profit"],
                        config.default_lot_size,
                    )
                    if metrics:
                        print(f"  Profit potencial: +${metrics['net_profit']:.2f}  ({metrics['pips_tp']:.0f} pips)")
                        print(f"  Pérdida potencial: -${metrics['net_loss']:.2f}  ({metrics['pips_sl']:.0f} pips)")
                        print(f"  Comisión estimada: ${metrics['commission']:.2f} | R:R = 1:{metrics['rr']}")
                print(f"  Razón: {signal['reason']}")

                positions = client.get_positions(symbol)
                for position in positions:
                    bot_state.update_position(symbol, position)

                if strategy.validate_trade(signal, positions, tick=client.get_tick(symbol)):
                    if signal["action"] != "HOLD":
                        result = client.place_order(
                            symbol=symbol,
                            volume=config.default_lot_size,
                            order_type=signal["action"],
                            stop_loss=signal.get("stop_loss") or None,
                            take_profit=signal.get("take_profit") or None,
                            comment=f"Bot: {signal['reason'][:20]}",
                        )
                        if result and result.get("success"):
                            print(f"  Orden ejecutada: ticket {result.get('order')} @ {result.get('price')}")
                            log_trade(
                                symbol=symbol,
                                action=signal["action"],
                                volume=config.default_lot_size,
                                price=result.get("price") or signal.get("entry", 0),
                                stop_loss=signal.get("stop_loss", 0),
                                take_profit=signal.get("take_profit", 0),
                                result=result,
                                platform=version,
                            )
                        elif result and result.get("timeout"):
                            print("  [!] TIMEOUT esperando al EA: la orden NO se confirmó.")
                            print("      La orden PUEDE haberse ejecutado igualmente. Revisa MT4")
                            print("      antes de que el bot reintente en el próximo ciclo.")
                        else:
                            err = (result or {}).get("error") or (result or {}).get("comment") or "sin respuesta"
                            print(f"  Error al ejecutar orden: {err}")
                else:
                    print("  Señal no validada para ejecución.")

            time.sleep(60)

    except KeyboardInterrupt:
        print("\n\nBot detenido por el usuario.")
    finally:
        bot_state.set_bot_running(False)
        bot_state.set_connected(False)
        client.disconnect()
        print("Desconectado.")


if __name__ == "__main__":
    main()
