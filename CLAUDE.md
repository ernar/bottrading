# CLAUDE.md

Bot de trading que integra **MetaTrader 5/4** con un **LLM** (Ollama local por defecto, `qwen3:8b`) para generar y ejecutar señales basadas en análisis técnico. Backend Python (Flask + SocketIO + MetaTrader5 + ollama); dashboard React + TypeScript + Tailwind + Vite.

## Arranque

- `python main.py` — arranca el bot **y** el API server (puerto 5000) en un hilo interno del mismo proceso. NO arranques el API por separado: el estado se comparte por proceso.
- `start.bat` — lanza `main.py` + el dashboard React (`http://localhost:5173`).
- Frontend: `cd frontend && npm install`, luego `npm run dev`.
- Credenciales y config en `.env` (MT5_LOGIN/PASSWORD/SERVER, MODEL, SYMBOLS, NEWS_ENABLED). `.env` está en `.gitignore` y contiene credenciales reales — nunca lo subas ni lo imprimas.

## Arquitectura

- **Punto de entrada único:** `main.py` (loop del bot + API en hilo daemon). El estado compartido vive en `core/state.py` (`bot_state`, singleton). `main` y `api/server.py` DEBEN usar el mismo `bot_state` y el mismo cliente MT (`set_mt_client` / `_mt_client`); instancias separadas → el dashboard se queda sin datos.
- **`core/strategy.py`** — `StrategyEngine`: llama al LLM (ollama/openai/gemini), parsea JSON y valida señales (`validate_trade`). Acepta `system_suffix` y umbrales (`min_confidence`, `min_rr`) por instancia.
- **`core/market_context.py`** — construye el contexto estructurado para el prompt (indicadores H1/H4 calculados en Python, últimas velas, posiciones, noticias, memoria). NO se vuelcan velas crudas.
- **`core/memory.py`** — `SignalMemory`: registra señales y evalúa su resultado contra el precio en ciclos posteriores; el resumen se inyecta al prompt como feedback.
- **`core/news.py`** — `news_provider` (singleton, caché): calendario económico ForexFactory + titulares RSS Yahoo, fail-safe (devuelve "" ante errores de red).
- **`clients/`** — `base_client.py` define la interfaz común; `mt5_client.py` (MetaTrader5 nativo) y `mt4_client.py` (bridge vía EA `PythonBridge.mq4`).
- **`api/server.py`** — Flask REST + WebSocket. `async_mode="threading"` forzado, `ping_timeout=60`.

## Sistema agéntico (en desarrollo, carpeta `.dev/`)

`.dev/` es la **rama de desarrollo** del sistema agéntico (copia de trabajo aislada; el repo no usa git). Un **agente especializado por símbolo**:

- `.dev/agents/base_agent.py` → `SymbolAgent` (símbolo + provider/modelo LLM + `AgentParams` + persona inyectada al prompt + `SignalMemory` aislada en `logs/agents/<name>_memory.json`).
- `.dev/agents/registry.py` → blueprints declarativos; `build_agent(name)`. Primer agente: `btc-agent` (BTCUSD). Añadir un símbolo = añadir un blueprint.
- `.dev/agents/orchestrator.py` → `AgentOrchestrator` corre el loop y ejecuta señales válidas; `optimize()` (rule-based) ajusta los `AgentParams` de cada agente según su rendimiento (memoria), con límites en `PARAM_BOUNDS`. Se llama cada 20 ciclos (`optimize_every_cycles`).
- En `.dev/main.py` el flujo es: plataforma → **selección de agentes** (no de símbolos) → orquestador.

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
- Modelos Ollama disponibles: `ollama list`
- Frontend type-check: `cd frontend && npx tsc --noEmit`
