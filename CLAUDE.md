# CLAUDE.md

Bot de trading que integra **MetaTrader 5/4** con un **LLM** (Ollama local por defecto, `qwen3:8b`) para generar y ejecutar señales basadas en análisis técnico. Backend Python (Flask + SocketIO + MetaTrader5 + ollama); dashboard React + TypeScript + Tailwind + Vite.

## Arranque

- `python main.py` — arranca el bot **y** el API server (puerto 5000) en un hilo interno del mismo proceso. NO arranques el API por separado: el estado se comparte por proceso.
- `start.bat` — lanza `main.py` + el dashboard React (`http://localhost:5173`).
- Frontend: `cd frontend && npm install`, luego `npm run dev`.
- Credenciales y config en `.env` (MT5_LOGIN/PASSWORD/SERVER, MODEL, SYMBOLS, NEWS_ENABLED). `.env` está en `.gitignore` y contiene credenciales reales — nunca lo subas ni lo imprimas. Plantilla sin secretos en `.env.example`.
- Variables de seguridad/riesgo en `.env`: `API_HOST` (default `127.0.0.1`; usar `0.0.0.0` solo con `API_TOKEN`), `API_TOKEN` (protege las rutas POST que mutan estado), `MAX_DAILY_LOSS_PCT` (cooldown de pérdida diaria: deja de abrir operaciones y espacia el análisis sin detener el bot; 0 = desactivado).

## Arquitectura

- **Punto de entrada único:** `main.py` — flujo: plataforma → **selección de agentes** → orquestador + API en hilo daemon. El loop del bot es el `AgentOrchestrator` (ver "Sistema agéntico"). El estado compartido vive en `core/state.py` (`bot_state`, singleton). `main` y `api/server.py` DEBEN usar el mismo `bot_state`, el mismo cliente MT (`set_mt_client` / `_mt_client`) y el mismo orquestador (`set_orchestrator`); instancias separadas → el dashboard se queda sin datos.
- **`core/strategy.py`** — `StrategyEngine`: llama al LLM (ollama/openai/gemini), parsea JSON y valida señales (`validate_trade`). Acepta `system_suffix` y umbrales (`min_confidence`, `min_rr`) por instancia.
- **`core/market_context.py`** — construye el contexto estructurado para el prompt (indicadores H1/H4 calculados en Python, últimas velas, posiciones, noticias, memoria). NO se vuelcan velas crudas.
- **`core/memory.py`** — `SignalMemory`: registra señales y evalúa su resultado contra el precio en ciclos posteriores; el resumen se inyecta al prompt como feedback.
- **`core/news.py`** — `news_provider` (singleton, caché): calendario económico ForexFactory + titulares RSS Yahoo, fail-safe (devuelve "" ante errores de red).
- **`clients/`** — `base_client.py` define la interfaz común; `mt5_client.py` (MetaTrader5 nativo) y `mt4_client.py` (bridge vía EA `PythonBridge.mq4`).
- **`api/server.py`** — Flask REST + WebSocket. `async_mode="threading"` forzado, `ping_timeout=60`.

## Sistema agéntico

El núcleo del bot es un **agente especializado por símbolo** (carpeta `agents/`):

- `agents/base_agent.py` → `SymbolAgent` (símbolo + provider/modelo LLM + `AgentParams` + persona inyectada al prompt + `SignalMemory` aislada en `logs/agents/<name>_memory.json`).
- `agents/registry.py` → blueprints declarativos; `build_agent(name)`. Primer agente: `btc-agent` (BTCUSD). Añadir un símbolo = añadir un blueprint.
- `agents/orchestrator.py` → `AgentOrchestrator` corre el loop y ejecuta señales válidas; `optimize()` (rule-based) ajusta los `AgentParams` de cada agente según su rendimiento (memoria), con límites en `PARAM_BOUNDS`. Se llama cada 20 ciclos (`optimize_every_cycles`). También: cooldown de pérdida diaria (`_check_daily_loss_guard`): al superar `MAX_DAILY_LOSS_PCT` deja de abrir operaciones y espacia el análisis a `RISK_COOLDOWN_ANALYSIS_INTERVAL` esperando que se cierren las posiciones, **sin detener el bot** (`_risk_cooldown_active`); registro de cierres para el historial (`_detect_closed_trades`); y, con el símbolo en su máximo de posiciones, espacia el análisis a `AT_MAX_ANALYSIS_INTERVAL` (15 min).
- **Gestión de riesgo en señales** (`validate_trade` / `AgentParams`): filtro de spread (`max_spread_filter`), límite global de posiciones (`max_open_positions`), volumen por riesgo opcional (`use_risk_sizing` + `calculate_lot_size`), y override por confianza alta (`max_pos_override_confidence`, default 0.90) que se salta el límite de posiciones del símbolo.

## Convenciones y gotchas

- **Nunca añadir `eventlet`** a requirements: rompe el WebSocket (el API corre en hilo plano sin monkey_patch). Usar `simple-websocket`.
- Un proceso del bot zombi puede retener el puerto 5000 y servir código viejo. Al depurar comportamiento "raro" del server: `netstat -ano | findstr :5000`.
- Redondeo de precios de órdenes: usar `round(price, sym_info.digits)` (no `point*10`).
- `order_type` debe normalizarse a mayúsculas en `place_order`.
- Cerrar posiciones debe llamar a `client.close_position()`, no solo quitarla del estado.
- El bucle respeta `bot_state.bot_running`; el dashboard puede pausar/reanudar vía API.
- Idioma del proyecto: comentarios, prompts y mensajes de CLI en **español**.

## Comandos útiles

- Sintaxis Python: `python -m py_compile <archivo>`
- Tests (funciones puras): `python -m pytest -q` (deps de test en `requirements-dev.txt`)
- Modelos Ollama disponibles: `ollama list`
- Frontend type-check: `cd frontend && npx tsc --noEmit`
