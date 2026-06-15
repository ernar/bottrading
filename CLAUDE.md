# CLAUDE.md

Bot de trading que integra **MetaTrader 4** con un **LLM** (Ollama local por defecto, `qwen3:8b`) para generar y ejecutar señales basadas en análisis técnico. Backend Python (Flask + SocketIO + MT4 bridge + ollama); dashboard React + TypeScript + Tailwind + Vite.

## Arranque

- `python main.py` — arranca el bot **y** el API server (puerto 5000) en un hilo interno del mismo proceso. NO arranques el API por separado: el estado se comparte por proceso.
- `start.bat` — lanza `main.py` + el dashboard React (`http://localhost:3000`).
- Frontend: `cd frontend && npm install`, luego `npm run dev`.
- Credenciales y config en `.env` (MT4_LOGIN/PASSWORD/HOST/PORT, MODEL, SYMBOLS, NEWS_ENABLED). `.env` está en `.gitignore` y contiene credenciales reales — nunca lo subas ni lo imprimas. Plantilla sin secretos en `.env.example`.
- Variables de seguridad/riesgo en `.env`: `API_HOST` (default `127.0.0.1`; usar `0.0.0.0` solo con `API_TOKEN`), `API_TOKEN` (protege las rutas POST que mutan estado), `MAX_DAILY_LOSS_PCT` (cooldown de pérdida diaria: deja de abrir operaciones y espacia el análisis sin detener el bot; 0 = desactivado).

## Arquitectura

- **Punto de entrada único:** `main.py` — flujo: plataforma → **selección de agentes** → orquestador + API en hilo daemon. El loop del bot es el `AgentOrchestrator` (ver "Sistema agéntico"). El estado compartido vive en `core/state.py` (`bot_state`, singleton). `main` y `api/server.py` DEBEN usar el mismo `bot_state`, el mismo cliente MT (`set_mt_client` / `_mt_client`) y el mismo orquestador (`set_orchestrator`); instancias separadas → el dashboard se queda sin datos.
- **`core/strategy.py`** — `StrategyEngine`: llama al LLM (ollama/openai/gemini), parsea JSON y valida señales (`validate_trade`). Acepta `system_suffix` y umbrales (`min_confidence`, `min_rr`) por instancia.
- **`core/market_context.py`** — construye el contexto estructurado para el prompt (indicadores H1/H4 calculados en Python, últimas velas, posiciones, noticias, memoria). NO se vuelcan velas crudas.
- **`core/memory.py`** — `SignalMemory`: registra señales y evalúa su resultado contra el precio en ciclos posteriores; el resumen se inyecta al prompt como feedback.
- **`core/news.py`** — `news_provider` (singleton, caché): calendario económico ForexFactory + titulares RSS Yahoo, fail-safe (devuelve "" ante errores de red).
- **`clients/`** — `base_client.py` define la interfaz común; `mt4_client.py` (bridge vía EA `PythonBridge.mq4`).
- **`api/server.py`** — Flask REST + WebSocket. `async_mode="threading"` forzado, `ping_timeout=60`.

## Sistema agéntico

El núcleo del bot es un **agente especializado por símbolo** (carpeta `agents/`):

- `agents/base_agent.py` → `SymbolAgent` (símbolo + provider/modelo LLM + `AgentParams` + persona inyectada al prompt + `SignalMemory` aislada en `logs/agents/<name>_memory.json`).
- `agents/registry.py` → blueprints declarativos; `build_agent(name)`. Primer agente: `btc-agent` (BTCUSD). Añadir un símbolo = añadir un blueprint.
- `agents/orchestrator.py` → `AgentOrchestrator` corre el loop y ejecuta señales válidas; `optimize()` (rule-based) ajusta los `AgentParams` de cada agente según su rendimiento (memoria), con límites en `PARAM_BOUNDS`. Se llama cada 20 ciclos (`optimize_every_cycles`). También: cooldown de pérdida diaria (`_check_daily_loss_guard`): al superar `MAX_DAILY_LOSS_PCT` deja de abrir operaciones y espacia el análisis a `RISK_COOLDOWN_ANALYSIS_INTERVAL` esperando que se cierren las posiciones, **sin detener el bot** (`_risk_cooldown_active`); registro de cierres para el historial (`_detect_closed_trades`); y, con el símbolo en su máximo de posiciones, espacia el análisis a `AT_MAX_ANALYSIS_INTERVAL` (15 min).
- **Gestión de riesgo en señales** (`validate_trade` / `AgentParams`): filtro de spread (`max_spread_filter`), volumen por riesgo opcional (`use_risk_sizing` + `calculate_lot_size`), override por confianza alta (`max_pos_override_confidence`, default 0.90), y **clamp de margen libre** antes de `place_order` (`_fit_volume_to_margin`: recorta el lote al margen disponible o salta la entrada, evita el error 134 "fondos insuficientes"; usa el margen exacto del bróker `margin_required`/`MODE_MARGINREQUIRED` vía EA, con fallback a nocional/leverage). El **límite global de número de posiciones** (`max_open_positions`) NO bloquea las entradas que aprueba la mesa: en la ruta coordinada `_open_from_signal(..., enforce_max_positions=False)` delega esa decisión a la mesa (que gobierna la exposición real vía RiskBook y puede abrir más si lo considera necesario).

### Coordinador (mesa de dirección)

`agents/coordinator.py` añade una capa por encima de los especialistas. La mesa está **SIEMPRE activa** — todo el flujo es coordinado, **no existe ruta clásica** (`AgentOrchestrator` exige `coordinator` y `risk_book`). Dos partes:

- **`RiskBook`** (determinista, la "tesorería"): `snapshot()` calcula equity/exposición por símbolo (nocional `volume×precio×contract_size`) y total (`used_margin/equity`); `clamp()` impone **topes duros** (`MAX_TOTAL_EXPOSURE_PCT`, `MAX_SYMBOL_ALLOCATION_PCT`, cooldown) sobre lo que proponga el LLM. No depende del LLM.
- **`CoordinatorAgent`** (LLM, el "director"): `decide()` recibe el snapshot + las señales de los especialistas + rendimiento + noticias y devuelve por símbolo `approve`/`priority`/`allocation_pct`/`position_action` (hold/reduce/close). Reutiliza `StrategyEngine.chat_json()`. **Fail-safe**: si el LLM falla, cae a una decisión determinista.

El orquestador parte cada ciclo en **recolectar** (`_gather_signal`) → **coordinar** (`RiskBook.snapshot` → `decide` → `clamp`) → **ejecutar** por prioridad (`_execute_decision`: abre entradas aprobadas con lote escalado por asignación, y cierra/reduce/cubre vía `client.close_position`/`place_order`).

**Recolección en paralelo** (`PARALLEL_ANALYSIS`, default true; solo ruta coordinada y con >1 agente): la fase de recolección lanza `_gather_signal` de cada agente en un `ThreadPoolExecutor` (`_gather_signals_parallel`), solapando las llamadas al LLM (la parte lenta). **Solo acelera si el backend LLM atiende peticiones concurrentes** (nube, u Ollama con `OLLAMA_NUM_PARALLEL`). Los accesos al EA se serializan en `MT4Client._send` (lock; canal único de archivos `pb_cmd`/`pb_resp` — NO thread-safe sin él). La salida de cada agente se captura por hilo (`_ThreadRoutedStdout` + `_worker_local`, que silencia el `_Spinner`) y se vuelca **en orden de agente** al terminar, sin entremezclar reportes. La coordinación y ejecución siguen secuenciales. Estado expuesto en `coordinator_overview()` → `GET /api/coordinator`; `POST /api/coordinator/decide` fuerza una decisión **dry-run** (no ejecuta); evento WS `coordinator_decision`. Frontend: pestaña **"Mesa"** (`frontend/src/pages/Coordinator.tsx`). Los helpers `_pos_*` viven en `agents/positions.py` (compartidos orquestador/coordinador).

**Control de concentración direccional / reversión.** El `snapshot()` calcula el **sesgo neto** por símbolo (largos vs cortos: `net_direction` LONG/SHORT/FLAT, `net_exposure_pct`, `net_volume`) y si la cuenta soporta **hedging** (MT5 por `account.margin_mode`; MT4 siempre). El `clamp()` añade guardias deterministas (configurables en `.env`): **anti-apilamiento** (`MAX_NET_DIRECTION_PCT`: no aprueba entradas que aumenten un neto ya saturado; la entrada opuesta sí, porque reduce), **guardia de reversión** (`REVERSAL_DRAWDOWN_PCT`: si el sesgo abierto choca con la tendencia nueva del especialista y hay pérdida flotante, fuerza `reduce`, o `close` si la pérdida ≥ 2× el umbral, fijando `manage_direction` al lado a cerrar) y **hard-stop por símbolo** (`MAX_SYMBOL_LOSS_PCT`, 0 = off). **Fuerza mayor** (`COORDINATOR_LLM_CAN_CLOSE`, default **false**): por defecto la mesa **solo cierra por fuerza mayor** — la gestión DISCRECIONAL del LLM (`reduce`/`close`/`hedge` "por criterio", p. ej. por exposición) se **ignora → `hold`** en el `clamp` (las posiciones tienen su propio Stop Loss y se respeta); solo las guardias deterministas (hard-stop / reversión, `forced=True`) tocan lo abierto. `COORDINATOR_CAN_CLOSE` sigue siendo el kill-switch global (si es false, ni las guardias cierran). **Período de gracia** (`MIN_HOLD_SECONDS`, default 300): el `snapshot()` registra la edad de cada posición vista por la mesa (`newest_position_age`, medida con `time.monotonic` local — inmune al desfase de zona horaria del bróker, vía `RiskBook._first_seen`); mientras la posición más reciente de un símbolo sea más joven que el umbral, la guardia de reversión **se pausa** y un `reduce`/`close` que proponga el LLM **se aplaza a `hold`** (se le da tiempo a evolucionar) — **solo el hard-stop catastrófico rompe la gracia**. El prompt del coordinador recibe la antigüedad de las posiciones (marca ⏳ EN PERÍODO DE GRACIA) y el rendimiento del agente siempre. El LLM puede proponer `position_action: "hedge"` (cubrir abriendo en sentido contrario para neutralizar el neto sin cerrar): el `clamp` lo **degrada a `reduce`** si la cuenta no es hedging o si la exposición total está en el tope, y a `hold` sin `can_close`. `_execute_decision` ejecuta el lado indicado por `manage_direction`: `_manage_open_positions(..., direction=)` cierra solo ese lado (MT5 filtra, MT4 best-effort) y `_hedge_position()` abre la orden opuesta por el volumen neto. `decide()` sintetiza un `hold` para cada símbolo con posiciones abiertas que el LLM omita, para que las guardias actúen aunque el LLM falle.

### Planificación por cadencias

El bucle (`run_forever`) corre en **un único proceso/hilo** (sin eventlet) con un **tick base = rotación**. Tareas más lentas se "abren" por tiempo (`time.monotonic`, helper `_due`). Config en `get_schedule_config()` (`core/config.py`). Al arrancar, `_startup_review()` convoca la mesa: snapshot de cuenta + disponibilidad de cada agente (símbolo presente en el broker) antes de la primera rotación. Cadencias (todas en segundos vía `.env`):
- **Rotación** (`ROTATION_SECONDS`, 60): `_run_rotation` → ciclo coordinado o clásico.
- **Sonda de noticias RED** (`NEWS_POLL_SECONDS`, 30 min): `_poll_red_news()` usa `news_provider.get_high_impact_events(symbol)` (eventos `impact == "High"` con clave estable, deduplicados en `_reacted_news_keys`); un evento nuevo añade el símbolo a `force_symbols`, que `_gather_signal(..., force=True)` analiza **saltándose el throttle** (al-máximo/cooldown). La **ejecución sigue gobernada por la mesa** (snapshot+clamp), no se salta el riesgo.
- **Junta** (`JUNTA_INTERVAL_SECONDS`, 1 h): `_run_junta()` convoca la mesa SIEMPRE (revisión global del libro aunque la rotación no tenga actividad); ejecuta con `manage_only=True` (gestiona close/reduce/hedge, **no abre entradas** desde señales viejas). Sin coordinador, imprime un resumen determinista.
- **Reporte** (`REPORT_INTERVAL_SECONDS`, 2 h): `_run_report()` arma el informe con `core/reporting.build_report()` (cuenta, sesgo neto/exposición por símbolo, decisiones de la mesa, rendimiento por agente, cierres) y lo envía con `core/mailer.send_report()` — **apagado por defecto** (`SMTP_ENABLED=false`: se genera y se muestra, no se envía hasta configurar SMTP). Destinatario `REPORT_EMAIL_TO`. `coordinator_overview()` expone `last_junta_at`/`last_report_at`/intervalos; el front "Mesa" los muestra en la cabecera. **El email lo manda el propio proceso del bot vía `smtplib` (stdlib), no las herramientas MCP de Gmail.**

## Convenciones y gotchas

- **Salida de terminal:** centralizada en `core/console.py` (color semántico vía `colorama`, reglas/cabeceras, `kv`, tablas alineadas con color por celda, helpers `money`/`pnl`/`side`). Reconfigura stdout a UTF‑8 (`errors="replace"`) para que los glifos/emojis no revienten en consolas cp1252. Color **on** por defecto en TTY; desactivar con `NO_COLOR`/`BOT_NO_COLOR`, forzar con `BOT_FORCE_COLOR` (al redirigir); degrada a texto plano si falta `colorama`. El ciclo coordinado imprime 3 fases: `[1/3]` recolección con el **reporte de cada especialista a la mesa** (`_print_signal_brief`) → `[2/3]` mesa como **tabla** propuesta↔veredicto (`_print_coordination`) → `[3/3]` ejecución.
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
