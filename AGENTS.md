# AGENTS.md

> Guía de contexto para agentes de IA (Cursor, Aider, Claude Code, Copilot, etc.).
> Estándar agnóstico de herramienta. Si usas **Claude Code**, `CLAUDE.md` tiene el mismo
> contenido en más detalle; este archivo es la versión canónica y autocontenida.
> **Idioma del proyecto: español** — comentarios, prompts, mensajes de CLI y docs en español.

---

## 1. Qué es este proyecto

Bot de trading automático que integra **MetaTrader 4** con un **LLM** (Ollama local por
defecto, `qwen3:8b`) para generar y ejecutar señales basadas en análisis técnico.

- **Backend:** Python (Flask + SocketIO + bridge MT4 + Ollama).
- **Frontend:** dashboard React + TypeScript + Tailwind + Vite.
- **Núcleo:** sistema **agéntico** — un agente especializado por símbolo (modelo, parámetros,
  persona y memoria propios), coordinados por un orquestador que auto-optimiza parámetros, con
  una capa de **coordinador** ("mesa de dirección") que gestiona riesgo y exposición global.

> ⚠️ Ejecuta órdenes reales. Usar siempre cuenta **Demo** antes que real.

---

## 2. Arranque y comandos

| Acción | Comando |
|---|---|
| Bot + API (mismo proceso, puerto 5000) | `python main.py` |
| Bot + dashboard (Windows) | `start.bat` |
| Frontend dev | `cd frontend && npm install && npm run dev` (→ `http://localhost:3000`) |
| Tests (funciones puras) | `python -m pytest -q` |
| Comprobar sintaxis de un archivo | `python -m py_compile <archivo>` |
| Type-check del frontend | `cd frontend && npx tsc --noEmit` |
| Modelos Ollama disponibles | `ollama list` |
| Resolver config de límites en vivo | `python examples_config.py` |

- **`python main.py` arranca el bot Y el API server** en un hilo interno del mismo proceso.
  NO arranques el API por separado: el estado se comparte por proceso.
- Dependencias: `requirements.txt` (runtime) y `requirements-dev.txt` (pytest).
- Requisitos: Python 3.8+, MT4 abierto con el EA `PythonBridge.mq4` adjunto, Ollama corriendo
  con `qwen3:8b` (`ollama pull qwen3:8b`, ~5 GB RAM), Node.js 18+ (solo dashboard).

### Configuración (`.env`)

Copia `.env.example` a `.env` y rellena valores. Variables clave:

- **MT4:** `MT4_LOGIN`, `MT4_PASSWORD`, `MT4_HOST` (default `127.0.0.1`), `MT4_PORT` (default `8765`).
- **LLM:** `OLLAMA_MODELS` (catálogo), `OPENAI_API_KEY`/`OPENAI_MODEL`, `GEMINI_API_KEY`/`GEMINI_MODEL`
  (los proveedores en la nube solo aparecen en el selector si su clave está configurada).
- **Noticias:** `NEWS_ENABLED` (default `true`).
- **Seguridad del API:** `API_HOST` (default `127.0.0.1`; usar `0.0.0.0` SOLO con `API_TOKEN`),
  `API_TOKEN` (protege las rutas POST que mutan estado vía cabecera `X-API-Token`).
- **Riesgo:** `MAX_DAILY_LOSS_PCT` (cooldown de pérdida diaria; 0 = off), y muchos parámetros por
  agente con precedencia **símbolo > modelo > default** (p. ej. `MAX_OPEN_POSITIONS_BTCUSD`,
  `MIN_CONFIDENCE_BTCUSD`). Lista completa en `.env.example` y `.env.example.advanced`.
- **Coordinador** (siempre activo): `MAX_TOTAL_EXPOSURE_PCT`,
  `MAX_SYMBOL_ALLOCATION_PCT`, `MAX_NET_DIRECTION_PCT`, `REVERSAL_DRAWDOWN_PCT`,
  `MAX_SYMBOL_LOSS_PCT`, `MIN_HOLD_SECONDS` (default 300), y el nº máximo de
  posiciones por símbolo = `MAX_OPEN_POSITIONS_DEFAULT` (riesgo) ×
  `MAX_POSITIONS_HORIZON_MULT` (horizonte).
- **Cadencias (segundos):** `ROTATION_SECONDS` (60), `NEWS_POLL_SECONDS` (1800),
  `JUNTA_INTERVAL_SECONDS` (3600), `REPORT_INTERVAL_SECONDS` (7200).
- **Reporte/email:** `SMTP_ENABLED` (default false), `REPORT_EMAIL_TO`, config SMTP.

> ⚠️ **`.env` contiene credenciales reales y está en `.gitignore`** — nunca lo subas ni lo
> imprimas. Si se filtró en un commit, rota claves y contraseña del bróker. Plantilla sin
> secretos en `.env.example`.
>
> Si activas `API_TOKEN`, el dashboard debe enviarlo: define `VITE_API_TOKEN` (mismo valor) en
> `frontend/.env`.

---

## 3. Arquitectura

**Punto de entrada único:** `main.py` — flujo: plataforma → **selección de agentes** →
orquestador + API en hilo daemon. El loop del bot es el `AgentOrchestrator`.

El **estado compartido** vive en `core/state.py` (`bot_state`, singleton). `main` y
`api/server.py` DEBEN usar el mismo `bot_state`, el mismo cliente MT (`set_mt_client` /
`_mt_client`) y el mismo orquestador (`set_orchestrator`). Instancias separadas → el dashboard
se queda sin datos.

### Módulos `core/`

- **`strategy.py`** — `StrategyEngine`: llama al LLM (ollama/openai/gemini), parsea JSON y valida
  señales (`validate_trade`). Acepta `system_suffix` y umbrales (`min_confidence`, `min_rr`) por
  instancia. Expone `chat_json()` (reutilizado por el coordinador).
- **`market_context.py`** — construye el contexto estructurado para el prompt (indicadores H1/H4
  calculados en Python, últimas velas, posiciones, noticias, memoria). NO vuelca velas crudas.
- **`indicators.py`** — indicadores técnicos (RSI, EMA20/50, MACD, Bollinger, ATR, S/R).
- **`memory.py`** — `SignalMemory`: registra señales y evalúa su resultado contra el precio en
  ciclos posteriores; el resumen se inyecta al prompt como feedback.
- **`news.py`** — `news_provider` (singleton, caché): calendario económico ForexFactory +
  titulares RSS Yahoo. Fail-safe: devuelve `""` ante errores de red.
- **`bot_state.py`** / **`state.py`** — contenedor de estado thread-safe + singleton compartido.
- **`trade_metrics.py`** — SL/TP, tamaño de lote (`calculate_lot_size`) y métricas de trade.
- **`reporting.py`** / **`mailer.py`** — informe (`build_report()`) + envío SMTP (`send_report()`).
- **`console.py`** — salida de terminal centralizada (ver §6).
- **`logger.py`** — logging de señales/trades a CSV. **`models.py`** — modelos Pydantic.
- **`config.py`** / **`llm_config.py`** — resolución de configuración y proveedores LLM.

### Otros paquetes

- **`clients/`** — `base_client.py` (interfaz común); `mt4_client.py` (bridge vía EA
  `PythonBridge.mq4`).
- **`api/server.py`** — Flask REST + WebSocket. `async_mode="threading"` forzado, `ping_timeout=60`.
- **`mt4_ea/PythonBridge.mq4`** — EA puente para MT4.
- **`frontend/`** — dashboard React. Páginas en `frontend/src/pages/` (Dashboard, Agents,
  Coordinator/"Mesa", Positions, Signals, History). Hooks `useApi`/`useWebSocket`. Tipos en
  `frontend/src/types/bot.ts`.

---

## 4. Sistema agéntico

### Agente por símbolo (`agents/`)

- `base_agent.py` → `SymbolAgent` (símbolo + provider/modelo LLM + `AgentParams` + persona
  inyectada al prompt + `SignalMemory` aislada en `logs/agents/<name>_memory.json`).
- `registry.py` → blueprints declarativos; `build_agent(name)`. Primer agente: `btc-agent`
  (BTCUSD). **Añadir un símbolo = añadir un blueprint.**
- `orchestrator.py` → `AgentOrchestrator` corre el loop y ejecuta señales válidas. `optimize()`
  (basado en reglas, no ML) ajusta los `AgentParams` según rendimiento, con límites en
  `PARAM_BOUNDS`; se llama cada 20 ciclos (`optimize_every_cycles`). También: cooldown de pérdida
  diaria (`_check_daily_loss_guard`) y registro de cierres (`_detect_closed_trades`). El
  especialista se analiza CADA rotación (sin análisis no hay confianza con la que decidir): el
  nº máximo de posiciones lo gobierna la mesa (`RiskBook.max_open_positions`, ver coordinador),
  no un throttle de análisis. Único espaciado: el cooldown por pérdida diaria.
- `positions.py` → helpers `_pos_*` compartidos entre orquestador y coordinador.

**Gestión de riesgo en señales** (`validate_trade` / `AgentParams`): filtro de spread
(`max_spread_filter`), límite global de posiciones (`max_open_positions`), volumen por riesgo
opcional (`use_risk_sizing` + `calculate_lot_size`) y override por confianza alta
(`max_pos_override_confidence`, default 0.90) que se salta el límite del símbolo.

### Coordinador / "mesa de dirección" (`agents/coordinator.py`)

Capa por encima de los especialistas, **SIEMPRE activa** (todo el flujo es coordinado; no existe
ruta clásica — `AgentOrchestrator` exige `coordinator` y `risk_book`). Dos partes:

- **`RiskBook`** (determinista, "tesorería"): `snapshot()` calcula equity/exposición por símbolo
  (nocional `volume×precio×contract_size`) y total (`used_margin/equity`); `clamp()` impone topes
  duros (`MAX_TOTAL_EXPOSURE_PCT`, `MAX_SYMBOL_ALLOCATION_PCT`, **nº máx. de posiciones por
  símbolo** = riesgo×horizonte, cooldown). No depende del LLM.
- **`CoordinatorAgent`** (LLM, "director"): `decide()` recibe snapshot + señales de especialistas +
  rendimiento + noticias y devuelve por símbolo `approve`/`priority`/`allocation_pct`/
  `position_action` (hold/reduce/close/hedge). **Fail-safe**: si el LLM falla, cae a una decisión
  determinista.

El ciclo es: **recolectar** (`_gather_signal`) → **coordinar**
(`RiskBook.snapshot` → `decide` → `clamp`) → **ejecutar** por prioridad (`_execute_decision`).
Estado en `coordinator_overview()` →
`GET /api/coordinator`; `POST /api/coordinator/decide` fuerza decisión **dry-run**; evento WS
`coordinator_decision`. Frontend: pestaña **"Mesa"** (`frontend/src/pages/Coordinator.tsx`).

**Control de concentración direccional / reversión:** `snapshot()` calcula el sesgo neto por
símbolo (`net_direction` LONG/SHORT/FLAT, `net_exposure_pct`, `net_volume`) y si la cuenta soporta
hedging. `clamp()` añade guardias deterministas (configurables): **anti-apilamiento**
(`MAX_NET_DIRECTION_PCT`), **guardia de reversión** (`REVERSAL_DRAWDOWN_PCT`) y **hard-stop por
símbolo** (`MAX_SYMBOL_LOSS_PCT`). **Coherencia entre símbolos correlacionados** (grupo FIJO
`CORRELATED_GROUPS = [("BTCUSD", "ETHUSD")]`, match por prefijo): `_apply_correlated_group_guard`
(post-pass del `clamp`) **veta abrir la pata opuesta** del par — no se va largo de uno y corto del
otro a la vez (pairs trade involuntario). La dirección dominante la fija la exposición abierta
combinada del grupo; si está plano, gana la entrada de mayor confianza. Nunca toca posiciones
abiertas (tienen su Stop Loss): solo bloquea entradas. **Período de gracia** (`MIN_HOLD_SECONDS`, default 300): la edad
se mide con `time.monotonic` local (`RiskBook._first_seen`, inmune al desfase de zona del bróker);
mientras la posición más reciente sea más joven que el umbral, la guardia de reversión se pausa y
un `reduce`/`close` del LLM se aplaza a `hold` — solo el hard-stop catastrófico rompe la gracia.
`hedge` se degrada a `reduce` si la cuenta no es hedging o la exposición está al tope.

### Planificación por cadencias

`run_forever` corre en **un único proceso/hilo** (sin eventlet) con **tick base = rotación**.
Tareas lentas se "abren" por tiempo (`time.monotonic`, helper `_due`). Config en
`get_schedule_config()`. Al arrancar, `_startup_review()` convoca la mesa antes de la 1ª rotación.

- **Rotación** (`ROTATION_SECONDS`, 60): `_run_rotation` → ciclo coordinado o clásico.
- **Sonda de noticias RED** (`NEWS_POLL_SECONDS`, 30 min): un evento de alto impacto nuevo añade el
  símbolo a `force_symbols`, analizado saltándose el throttle. La ejecución sigue gobernada por la
  mesa (no se salta el riesgo).
- **Junta** (`JUNTA_INTERVAL_SECONDS`, 1 h): `_run_junta()` convoca la mesa SIEMPRE con
  `manage_only=True` (gestiona close/reduce/hedge, no abre entradas nuevas).
- **Reporte** (`REPORT_INTERVAL_SECONDS`, 2 h): `_run_report()` arma el informe y lo envía con
  `send_report()` — **apagado por defecto** (`SMTP_ENABLED=false`). El email lo manda el propio
  proceso vía `smtplib` (stdlib), no herramientas MCP.

---

## 5. API y datos

REST + WebSocket en `api/server.py`. Endpoints principales:

```
GET  /health                        Estado del servidor
GET  /api/state                     Estado completo del bot
GET  /api/account                   Info de cuenta
GET  /api/signals                   Última señal por símbolo
GET  /api/positions                 Posiciones abiertas
GET  /api/history                   Trades cerrados
GET  /api/stats                     Estadísticas agregadas
GET  /api/agents                    Resumen de agentes (config, stats, última optimización)
POST /api/agents/optimize           Optimización (dry-run; {"apply": true} aplica en caliente)
GET  /api/models                    Proveedores/modelos LLM disponibles
POST /api/agents/{name}/model       Cambia el modelo de un agente en caliente
GET  /api/coordinator               Estado de la mesa (coordinator_overview)
POST /api/coordinator/decide        Fuerza una decisión dry-run (no ejecuta)
GET  /api/csv/signals               Últimas señales del CSV (?limit=&platform=)
GET  /api/csv/trades                Últimos trades del CSV
POST /api/bot/start                 Iniciar bot
POST /api/bot/stop                  Pausar bot
POST /api/positions/{symbol}/close  Cerrar posición
```

Las rutas **POST que mutan estado** exigen `X-API-Token` **si** `API_TOKEN` está en `.env`.
Eventos WS incluyen `coordinator_decision`.

**Logs (generados, gitignored):** `logs/memory.json`, `logs/agents/<name>_memory.json`,
`logs/mt4/signals.csv`, `logs/mt4/trades.csv`.

---

## 6. Convenciones y gotchas (IMPORTANTE)

- **Idioma:** comentarios, prompts y mensajes de CLI en **español**.
- **Salida de terminal:** centralizada en `core/console.py` (color semántico vía `colorama`,
  reglas/cabeceras, `kv`, tablas, helpers `money`/`pnl`/`side`). Reconfigura stdout a UTF-8
  (`errors="replace"`) para consolas cp1252. `NO_COLOR`/`BOT_NO_COLOR` desactivan color,
  `BOT_FORCE_COLOR` lo fuerza. No uses `print` crudo para salida de usuario.
- **NUNCA añadir `eventlet`** a requirements: rompe el WebSocket (el API corre en hilo plano sin
  monkey_patch). Usar `simple-websocket`.
- Un proceso zombi del bot puede retener el puerto 5000 y servir código viejo. Al depurar
  comportamiento "raro": `netstat -ano | findstr :5000`.
- Redondeo de precios de órdenes: `round(price, sym_info.digits)` (no `point*10`).
- `order_type` debe normalizarse a **mayúsculas** en `place_order`.
- Cerrar posiciones debe llamar a `client.close_position()`, no solo quitarla del estado.
- El bucle respeta `bot_state.bot_running`; el dashboard puede pausar/reanudar vía API.
- `main` y `api/server.py` deben compartir el MISMO `bot_state`, cliente MT y orquestador.

---

## 7. Tests

`python -m pytest -q` (deps en `requirements-dev.txt`). Cubren funciones puras: indicadores,
memoria, noticias, orquestador, reporting, riskbook, schedule, strategy. `conftest.py` hace que
pytest encuentre los paquetes. Añade/actualiza tests al tocar lógica pura (validación, sizing,
optimización, snapshot/clamp, cadencias).

---

## 8. Reglas de trabajo para el agente de IA

- Cambios mínimos y coherentes con el estilo del archivo que tocas.
- No subas ni imprimas `.env`. No añadas `eventlet`. No rompas el contrato de estado compartido.
- Tras tocar lógica pura, corre `python -m pytest -q`. Tras tocar frontend, `npx tsc --noEmit`.
- Mantén esta doc y `CLAUDE.md`/`README.md` sincronizados si cambias arquitectura, cadencias,
  endpoints o variables de `.env`.
```
