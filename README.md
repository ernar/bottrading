# MT4 Ollama Bot

Bot de trading automático que integra **MetaTrader 4** con un **LLM** para generar y ejecutar
señales basadas en análisis técnico. Soporta varios proveedores (**Gemini**, OpenAI, DeepSeek y
**Ollama** local) — los agentes incluidos vienen configurados con Gemini por defecto, pero el
modelo se elige por agente al arrancar o en caliente desde el dashboard.

El núcleo es un sistema **agéntico**: un agente especializado por símbolo, cada uno con su propio
modelo, parámetros, persona y memoria, coordinados por una **mesa de dirección** (coordinador) que
reparte presupuesto e impone topes de riesgo, y un orquestador que además auto-optimiza los
parámetros de cada agente según su rendimiento.

## Estructura del proyecto

```
mt4_ollama_bot/
├── main.py                  # Punto de entrada: selección de agentes + LLM de la mesa + orquestador + API
├── agents/                  # Sistema agéntico (agente por símbolo + coordinador)
│   ├── base_agent.py        # SymbolAgent: símbolo + modelo + AgentParams + persona + memoria aislada
│   ├── registry.py          # Blueprints declarativos de agentes (btc, wti, eurusd, eth, gold, sp500) + build_agent()
│   ├── orchestrator.py      # AgentOrchestrator: loop por cadencias, ejecuta señales y optimize()
│   ├── coordinator.py       # Mesa de dirección: RiskBook (determinista) + CoordinatorAgent (LLM)
│   └── positions.py         # Helpers de posiciones compartidos (orquestador/coordinador)
├── core/
│   ├── strategy.py          # Motor de estrategia (LLM multi-proveedor + validación de señales)
│   ├── indicators.py        # Indicadores técnicos (RSI, EMA, MACD, Bollinger, ATR, S/R)
│   ├── market_context.py    # Contexto estructurado para el LLM (indicadores + velas + memoria)
│   ├── memory.py            # SignalMemory: registro de señales con evaluación de resultados
│   ├── news.py              # Noticias multi-fuente por clase de activo + calendario económico (ForexFactory)
│   ├── assistant.py         # OrgAssistant: chatbot del dashboard + nota de dirección para la mesa
│   ├── reporting.py         # build_report(): informe periódico de cuenta/sesgo/decisiones
│   ├── mailer.py            # Envío del informe por SMTP (gated por SMTP_ENABLED)
│   ├── config.py            # Lectura de config (cadencias, coordinador, overrides por agente) desde .env
│   ├── profiles.py          # Perfiles de riesgo/horizonte (topes de posiciones, exposición)
│   ├── settings_schema.py   # Esquema de ajustes editables desde el dashboard
│   ├── llm_config.py        # Catálogo de proveedores/modelos LLM + modo pensamiento DeepSeek
│   ├── clock.py             # Hora del bróker (broker_now) — única referencia temporal del backend
│   ├── console.py           # Salida de terminal centralizada (color, tablas, helpers)
│   ├── console_capture.py   # Captura del stdout para el endpoint /api/console
│   ├── state.py             # bot_state: singleton compartido entre main y api server
│   ├── bot_state.py         # Definición del contenedor de estado thread-safe
│   ├── mt4_launcher.py      # Relogin opcional del terminal MT4 al arrancar (auto-login)
│   ├── trade_metrics.py     # Cálculo de SL/TP, tamaño de lote y métricas de trade
│   ├── db.py                # Persistencia SQLite vía SQLAlchemy (logs/bot.db, WAL)
│   ├── logger.py            # Logging de señales/trades/equity/cierres a la DB
│   └── models.py            # Modelos Pydantic (BotConfig, Position, etc.)
├── clients/
│   ├── base_client.py       # Interfaz común
│   └── mt4_client.py        # Bridge con MT4 vía EA (PythonBridge.mq4)
├── api/server.py            # Flask REST API + WebSocket para el dashboard
├── mt4_ea/PythonBridge.mq4  # EA puente para MT4
├── tests/                   # Tests con pytest (funciones puras)
├── requirements.txt
├── requirements-dev.txt     # Dependencias de test (pytest)
├── conftest.py              # Hace que pytest encuentre los paquetes del proyecto
├── examples_config.py       # Demo de resolución de límites por símbolo/modelo
├── start.bat                # Arranque en Windows (solo main.py: bot + API en :5000)
├── .env                     # Credenciales + config (NO subir a git; gitignored)
├── .env.example             # Plantilla sin secretos
├── .env.example.advanced    # Plantilla con todos los parámetros por agente
├── scripts/                 # Utilidades (migrate_csv_to_db.py: importa CSV/JSON antiguos a la DB)
└── logs/                    # Generado automáticamente (gitignored)
    ├── bot.db               # Base de datos SQLite: señales, trades, equity, cierres y memoria
    └── archive/             # Respaldo de los CSV/JSON antiguos tras migrar
```

> El **dashboard** (React + TypeScript + Tailwind) vive en un repo aparte:
> [ernar/bottrading-dashboard](https://github.com/ernar/bottrading-dashboard). Se conecta a este
> backend por su API (`VITE_API_URL` o desde la pestaña *Ajustes*).

## Requisitos

- Python 3.8+
- MetaTrader 4 instalado y abierto con el EA `PythonBridge.mq4` adjunto
- Una clave de LLM (al menos un proveedor):
  - **Gemini** (`GEMINI_API_KEY`) — proveedor por defecto de los agentes incluidos.
  - OpenAI (`OPENAI_API_KEY`), DeepSeek (`DEEPSEEK_API_KEY`) — opcionales.
  - **Ollama** local (sin clave) si prefieres un modelo local, p. ej. `qwen3:8b`:
    ```
    ollama pull qwen3:8b
    ```
- Node.js 18+ (solo para el dashboard)

## Instalación (VPS Windows)

Pasos pensados para una instalación limpia en un VPS Windows. Ejecuta los comandos en **PowerShell** o **CMD**.

### 1. Requisitos previos

Instala estos programas en el VPS (puedes usar [winget](https://learn.microsoft.com/windows/package-manager/winget/), ya incluido en Windows Server 2022+):

```bat
winget install Python.Python.3.12
winget install OpenJS.NodeJS.LTS
winget install Ollama.Ollama   # solo si vas a usar un modelo local
```

> Si `winget` no está disponible, descarga e instala manualmente: [Python 3.8+](https://www.python.org/downloads/) (marca **"Add python.exe to PATH"**), [Node.js 18+](https://nodejs.org/) y, opcionalmente, [Ollama](https://ollama.ai). Cierra y reabre la terminal tras instalar para refrescar el PATH.

### 2. Plataforma de trading (MT4)

El VPS debe tener instalado y abierto el terminal MT4 del broker.

**MT4:**
```bat
winget install MetaQuotes.MetaTrader4
```
O descarga el MT4 de tu broker. Abre MT4, inicia sesión con tu cuenta y deja el terminal **abierto** mientras el bot corre.

Además del terminal hay que instalar el **EA puente**:

1. En MT4: *Archivo → Abrir carpeta de datos*.
2. Copia [`mt4_ea/PythonBridge.mq4`](mt4_ea/PythonBridge.mq4) a `MQL4\Experts\`.
3. En MetaEditor compila `PythonBridge.mq4` (F7) o reinicia MT4 para que aparezca en el *Navegador*.
4. Arrastra `PythonBridge` a cualquier gráfico.
5. Activa **"Permitir trading automático"** (botón AutoTrading en verde) y, en *Herramientas → Opciones → Expert Advisors*, marca *"Permitir WebRequest/DLL"* si tu configuración lo requiere.

> Opcionalmente, define `MT4_TERMINAL_PATH` (+ `MT4_SERVER`) en el `.env` para que `main.py`
> cierre y relance el terminal con **auto-login** al arrancar. Déjalo vacío para no tocar el
> terminal ya abierto.

### 3. Backend (Python)

Desde la raíz del proyecto, actualiza `pip` e instala las dependencias:

```bat
python -m pip install --upgrade pip
pip install -r requirements.txt
```

> **No instales `eventlet`**: rompe el WebSocket del API (corre en hilo plano sin monkey-patch). El proyecto usa `simple-websocket`.

### 4. Frontend (dashboard)

El dashboard está en un **repo aparte**:
[ernar/bottrading-dashboard](https://github.com/ernar/bottrading-dashboard). Clónalo donde
quieras e instala sus dependencias allí:

```bat
git clone https://github.com/ernar/bottrading-dashboard.git
cd bottrading-dashboard
npm install
```

### 5. Modelo LLM

- **Gemini / OpenAI / DeepSeek (nube)**: solo necesitas la API key en el `.env` (ver Configuración).
- **Ollama (local, opcional)**:
  ```bat
  ollama pull qwen3:8b
  ```
  `ollama serve` se ejecuta como servicio en segundo plano tras instalar. Verifica los modelos
  disponibles con `ollama list`. (`qwen3:8b` ~5 GB de RAM.)

## Configuración

Crea `.env` en la raíz partiendo de la plantilla sin secretos: copia [`.env.example`](.env.example)
a `.env` y rellena tus valores.

```env
MT4_LOGIN=tu_numero_cuenta
MT4_PASSWORD=tu_contraseña
MT4_HOST=127.0.0.1
MT4_PORT=8765
MT4_SERVER=                      # nombre del servidor del broker (p. ej. ICMarketsSC-Demo)
MT_SERVER_GMT_OFFSET=3           # offset GMT del servidor del bróker (toda marca temporal usa hora del bróker)
NEWS_ENABLED=true                # noticias/calendario económico en el contexto del LLM

# --- Proveedores LLM: rellena la clave del que quieras usar ---
# Solo aparecen en el selector de modelo los proveedores con clave configurada.
GEMINI_API_KEY=                  # proveedor por defecto de los agentes incluidos
GEMINI_MODEL=gemini-2.0-flash
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
DEEPSEEK_API_KEY=
DEEPSEEK_MODEL=deepseek-chat
# Ollama no necesita clave; lista los modelos locales disponibles:
OLLAMA_MODELS=qwen3:8b,deepseek-r1:8b,llama3.1:latest

# --- Seguridad del API / dashboard ---
API_HOST=127.0.0.1   # solo local (recomendado). 0.0.0.0 = acceso remoto: ÚSALO solo con API_TOKEN
API_TOKEN=           # si lo defines, las rutas POST que mutan estado exigen la cabecera X-API-Token

# --- Gestión de riesgo ---
MAX_DAILY_LOSS_PCT=0.05   # cooldown si la pérdida del día supera el 5% (0 = desactivado)
```

> ⚠️ **`.env` contiene credenciales reales y está en `.gitignore`** — nunca lo subas a git. Si alguna vez se filtró (apareció en un commit), rota las claves y la contraseña del bróker.

> Los umbrales y símbolos **no se eligen en el menú**: cada agente trae los suyos en su blueprint (`agents/registry.py`). Se pueden **sobreescribir por `.env`** con precedencia símbolo > modelo > default (p. ej. `MAX_OPEN_POSITIONS_BTCUSD`, `MIN_CONFIDENCE_BTCUSD`, `COMMISSION_PER_LOT`…); ver [`.env.example`](.env.example) y [`.env.example.advanced`](.env.example.advanced) para la lista completa. El **modelo** se elige al arrancar (menú) o en caliente desde el dashboard.

> Si activas `API_TOKEN`, el dashboard debe enviarlo: define `VITE_API_TOKEN` (con el mismo valor) en el `.env` del repo del dashboard ([ernar/bottrading-dashboard](https://github.com/ernar/bottrading-dashboard)) o en su pestaña *Ajustes*.

> La **URL del backend** y el **token** también se configuran en caliente desde la pestaña **Ajustes** del dashboard (se guardan en el navegador y tienen prioridad sobre `VITE_API_URL`/`VITE_API_TOKEN`). Útil para apuntar el dashboard a un backend en otra máquina sin recompilar. El botón **Probar conexión** valida la URL contra `/api/state`.

### Elegir el modelo de cada agente

Cada agente trae un modelo por defecto en su blueprint (Gemini en los incluidos), pero puedes cambiarlo:

- **Al arrancar (`python main.py`)**: tras elegir los agentes, el menú pregunta el provider/modelo de cada uno **y el LLM de la mesa**. Solo lista proveedores con clave configurada (Ollama siempre disponible; Gemini/OpenAI/DeepSeek solo si pusiste su API key). Pulsa Enter para mantener el del blueprint.
- **En caliente desde el dashboard**: en la pestaña **Agentes**, el desplegable "Modelo LLM" de cada tarjeta cambia el modelo sin reiniciar el bot (efectivo en el siguiente ciclo de análisis). El LLM de la mesa se cambia en la pestaña **Mesa** y el del asistente en **Ajustes → Asistente**.

> **DeepSeek con modo pensamiento**: los modelos `deepseek-v4-*` son híbridos con *thinking* ON por
> defecto; se gobierna globalmente (`DEEPSEEK_THINKING`/`DEEPSEEK_REASONING_EFFORT`) o **por agente**
> desde la pestaña Agentes (override que persiste).

### Límites de operaciones por símbolo/modelo

El **nº máximo de posiciones por símbolo** lo gobierna la **mesa** (perfil de riesgo × horizonte,
configurables desde el dashboard en Ajustes → Riesgo). Aun así, puedes fijar topes y parámetros por
agente con variables de entorno en `.env`:

```env
# Límites por símbolo (precedencia 1 — máxima prioridad)
MAX_OPEN_POSITIONS_BTCUSD=2        # Bitcoin: máximo 2 operaciones
MAX_OPEN_POSITIONS_EURUSD=3        # Euro: máximo 3 operaciones

# Límites por modelo (precedencia 2)
MAX_OPEN_POSITIONS_QWEN3_8B=3      # Qwen: máximo 3 operaciones

# Fallback global (precedencia 3)
MAX_OPEN_POSITIONS_DEFAULT=5       # Otros símbolos/modelos: máximo 5
```

**Precedencia**: símbolo > modelo > default > blueprint. La primera coincidencia gana.

También puedes configurar otros parámetros del agente: `MIN_CONFIDENCE_*`, `MIN_RR_*`, `ATR_SL_MULT_*`, `TEMPERATURE_*`, `MAX_SPREAD_FILTER_*`, etc. Ver [`.env.example.advanced`](.env.example.advanced) para una configuración completa.

Para ver cómo se resuelven los límites en tiempo real:
```bash
python examples_config.py
```

## Arranque

```bat
start.bat
```

Esto lanza `main.py` (orquestador + API en el mismo proceso, puerto 5000). **El dashboard se
ejecuta aparte** y se conecta por API; `start.bat` no lo arranca.

Equivale a arrancar el bot directamente:
```bash
python main.py
```

El dashboard se levanta en su propio entorno (repo
[ernar/bottrading-dashboard](https://github.com/ernar/bottrading-dashboard)):
```bash
npm install && npm run dev
```

## Flujo de ejecución

1. Selección interactiva de **agentes** (cada agente ya trae su símbolo, modelo y configuración) y del **LLM de la mesa de dirección**.
2. Conecta a MT4 con las credenciales del `.env` vía EA bridge `PythonBridge.mq4` (relogin opcional con `MT4_TERMINAL_PATH`).
3. Inicia el API server en hilo de fondo (puerto 5000).
4. `_startup_review()` convoca a la mesa: snapshot de cuenta + disponibilidad de cada agente antes de la primera rotación.
5. El `AgentOrchestrator` corre un loop **por cadencias** (tick base = rotación, 60 s). Cada rotación es **coordinada por la mesa** y parte en tres fases:
   - **Recolectar** (`_gather_signal`, en paralelo si `PARALLEL_ANALYSIS`): por cada agente, evalúa señales anteriores contra el precio (memoria aislada por agente), construye contexto estructurado (indicadores H1/H4, velas, posiciones, noticias, rendimiento), inyecta su **persona** y pide al LLM una señal JSON.
   - **Coordinar** (`RiskBook.snapshot` → `CoordinatorAgent.decide` → `clamp`): la mesa recibe el snapshot de riesgo (equity, exposición, sesgo neto, spreads, P/L por lado) y las señales, y devuelve por símbolo `approve`/`priority`/`allocation_pct`/`position_action` + `tp_rr`/`size_mult`/`max_spread` opcionales. El `clamp` impone **topes duros** deterministas (exposición total/por símbolo, anti-apilamiento, reversión, hard-stop, coherencia entre correlacionados, período de gracia).
   - **Ejecutar** por prioridad (`_execute_decision`): abre entradas aprobadas con lote escalado por asignación y `size_mult`, recortado al margen libre; cierra/reduce/cubre solo por guardias deterministas o por fuerza mayor.
6. **Gestión dinámica de posición** (determinista, al inicio de cada rotación, sin LLM): **trailing stop** (SL a breakeven + seguimiento por ATR) y **cierre parcial** para los agentes que lo activan.
7. **Cadencias adicionales** (configurables en `.env`): sonda de **noticias de alto impacto** (30 min, fuerza análisis del símbolo afectado), **Junta** horaria (revisión global del libro, gestiona sin abrir), **Reporte** cada 2 h (`build_report` + email SMTP, apagado por defecto).
8. **Cooldown por pérdida diaria**: si la pérdida del día supera `MAX_DAILY_LOSS_PCT`, el bot **no se detiene**; deja de abrir nuevas operaciones y espacia el análisis hasta que cambie de día.
9. Cada 20 ciclos el orquestador llama a `optimize()`: ajuste **basado en reglas** (no ML) de los `AgentParams` de cada agente según su rendimiento, con límites en `PARAM_BOUNDS`.

## Mesa de dirección (coordinador)

La mesa está **siempre activa** — todo el flujo es coordinado. Dos partes:

- **`RiskBook`** (determinista, la "tesorería"): calcula equity/exposición/sesgo neto e impone los topes duros (`MAX_TOTAL_EXPOSURE_PCT`, `MAX_SYMBOL_ALLOCATION_PCT`, `MAX_NET_DIRECTION_PCT`, hard-stops, cooldown, período de gracia `MIN_HOLD_SECONDS`). No depende del LLM.
- **`CoordinatorAgent`** (LLM, el "director"): reparte presupuesto y prioridades, puede afinar TP (`tp_rr`), tamaño (`size_mult`) y filtro de spread (`max_spread`) por entrada. **Fail-safe**: si el LLM falla, cae a una decisión determinista. Por defecto **solo cierra por fuerza mayor** (`COORDINATOR_LLM_CAN_CLOSE=false`): la gestión discrecional del LLM se ignora salvo las guardias deterministas.

Estado expuesto en `GET /api/coordinator`; pestaña **Mesa** del dashboard.

## Asistente (chatbot del dashboard)

`core/assistant.py` → `OrgAssistant`: chatbot que actúa como director de la mesa y responde
consultando el estado en vivo. Su proveedor/modelo se eligen en **Ajustes → Asistente**. Por
defecto solo informa, **pero puede dejar una NOTA DE DIRECCIÓN para la mesa** cuando se lo pides
("dile a la mesa que…"): se persiste en `.env` (`DIRECTOR_NOTE`) y el director la pondera cada
rotación hasta que se cambie o se retire. No ejecuta órdenes ni cambia ajustes: las guardias de
riesgo deterministas mandan por encima de la nota.

## Agentes

Los agentes se definen como *blueprints* declarativos en [`agents/registry.py`](agents/registry.py).
**Añadir un símbolo = añadir un blueprint.** Agentes incluidos:

| Agente | Símbolo | Modelo por defecto | Notas |
|---|---|---|---|
| `btc-agent`    | BTCUSD     | gemini/gemini-2.0-flash  | Cripto 24/7, alta volatilidad: R:R ≥ 1:1.3, ATR SL 1.8× / TP 2.7×, spread alto |
| `wti-agent`    | WTI        | gemini/gemini-3.5-flash  | Petróleo: sesiones, inventarios y OPEP |
| `eurusd-agent` | EURUSD     | gemini/gemini-3.5-flash  | Forex mayor: sesiones Londres/NY, ECB vs Fed, spread ajustado |
| `eth-agent`    | ETHUSD     | gemini/gemini-3.5-flash  | Cripto 24/7, beta alta vs BTC, ATR SL 2.0× / TP 3.0× |
| `gold-agent`   | XAUUSD     | gemini/gemini-3.5-flash  | Oro refugio: tipos reales y dólar |
| `sp500-agent`  | .US500Cash | gemini/gemini-3.5-flash  | Índice USA: sesgo alcista, sesión NY |

Todos los agentes incluidos activan **trailing stop** y **cierre parcial**. Cada agente expone
parámetros (`AgentParams`): `provider`, `model`, `min_confidence`, `min_rr`, `atr_sl_mult`,
`atr_tp_mult`, `lot_size`, `risk_per_trade`, `max_open_positions`, `max_spread_filter`,
`temperature`, `use_risk_sizing`, `max_pos_override_confidence`, `use_trailing_stop` y los de cierre
parcial. Estos valores se pueden sobreescribir por `.env` (precedencia símbolo > modelo > default);
ver [`.env.example.advanced`](.env.example.advanced).

## API endpoints

```
GET  /health                        Estado del servidor
GET  /api/state                     Estado completo del bot
GET  /api/account                   Info de cuenta
GET  /api/signals                   Última señal por símbolo (estado en memoria)
GET  /api/positions                 Posiciones abiertas
GET  /api/history                   Trades cerrados
GET  /api/equity                    Serie de equity (gráfico de portfolio)
GET  /api/candles/<symbol>          Velas recientes del símbolo
GET  /api/news                      Titulares y calendario económico (teletipo)
GET  /api/console                   Salida reciente de la terminal del bot
GET  /api/stats                     Estadísticas agregadas (señales, win rate de memoria)
GET  /api/agents                    Resumen de agentes: config, stats de sesión y última optimización
POST /api/agents/optimize           Lanza optimización (dry-run; {"apply": true} para aplicar en caliente)
POST /api/agents/<name>/model       Cambia el modelo de un agente en caliente
POST /api/agents/<name>/params      Edita parámetros del agente en caliente
POST /api/agents/<name>/enabled     Activa/desactiva un agente
POST /api/agents/<name>/activate    Activa un agente nuevo desde su blueprint
POST /api/agents/save               Persiste la selección de agentes activos en .env
GET  /api/models                    Proveedores/modelos LLM disponibles (según claves del .env)
GET  /api/coordinator               Estado de la mesa (snapshot, decisiones, nota de dirección)
POST /api/coordinator/decide        Fuerza una decisión de la mesa (dry-run, no ejecuta)
POST /api/coordinator/model         Cambia el LLM de la mesa en caliente
GET  /api/settings                  Ajustes editables del bot
POST /api/settings                  Guarda ajustes (aplica en caliente / persiste en .env)
POST /api/risk-profile              Cambia el perfil de riesgo
POST /api/horizon                   Cambia el horizonte temporal
GET  /api/risk/spread               Filtro de spread por símbolo
POST /api/risk/spread               Edita el filtro de spread por símbolo
GET  /api/assistant/info            Info del asistente (proveedor/modelo)
POST /api/assistant/chat            Chat con el asistente
GET  /api/assistant/history         Historial del chat
POST /api/assistant/reset           Reinicia la conversación del asistente
GET  /api/db/signals                Histórico de señales desde la DB (?limit=&platform=)
GET  /api/db/trades                 Histórico de órdenes desde la DB (?limit=&platform=)
GET  /api/db/closed-trades          Histórico de cierres desde la DB
POST /api/bot/start                 Iniciar bot
POST /api/bot/stop                  Pausar bot
POST /api/positions/<symbol>/close  Cerrar posición
POST /api/positions/close-all       Cerrar todas las posiciones
```

> Las rutas **POST que mutan estado** exigen la cabecera `X-API-Token` **si** `API_TOKEN` está
> configurado en el `.env`. Sin `API_TOKEN` no se pide (uso puramente local).

> `/api/signals` devuelve la **última señal viva por símbolo** (estado en memoria); `/api/db/signals`
> devuelve el **histórico persistido** en la DB. Las rutas `/api/csv/*` se mantienen como **alias
> obsoletos** de `/api/db/*` por compatibilidad (ya no existe ningún CSV: todo es SQLite).

## Parámetros de riesgo por defecto (`AgentParams`)

| Parámetro | Valor por defecto |
|---|---|
| Lot size | 0.01 |
| Volumen por riesgo (`use_risk_sizing`) | desactivado → usa lote fijo |
| Riesgo por trade | 2% del balance |
| Confianza mínima | 60% |
| R:R mínimo | 1:1.3 (en los blueprints incluidos) |
| Máx. posiciones por símbolo (mesa) | perfil de riesgo × horizonte (p. ej. moderate+medio = 3) |
| SL automático | 1.5–2.0 × ATR H1 según agente |
| TP automático | 2.2–3.0 × ATR H1 según agente |
| Filtro de spread | por símbolo (2.0 forex … 50 cripto) |
| Comisión por lote | $7.0 |
| Cooldown pérdida diaria | `MAX_DAILY_LOSS_PCT` en `.env` (0 = off) |

> Cada agente puede sobreescribir estos valores en su blueprint o por `.env` (precedencia
> símbolo > modelo > default), y el orquestador los ajusta en caliente vía `optimize()`.

## Tests

Funciones puras (indicadores, validación de señales, sizing, optimización, memoria y cooldown):

```bat
pip install -r requirements-dev.txt
python -m pytest -q
```

## Notas

- Usar siempre cuenta **Demo** antes de real.
- MT4 debe estar abierto durante la ejecución con el EA `PythonBridge.mq4` adjunto a un gráfico y el trading automático activado.
- `qwen3:8b` (si usas Ollama) requiere ~5 GB de RAM.
- La base de datos `logs/bot.db` (SQLite) y la memoria de cada agente persisten entre sesiones.
- ¿Vienes de una versión con CSV/JSON en `logs/`? Migra el histórico con `python scripts/migrate_csv_to_db.py` (archiva los originales en `logs/archive/`).

## Troubleshooting

**"No se pudo conectar al EA de MT4"** → MT4 abierto, EA `PythonBridge` adjunto a un gráfico y "Permitir trading automático" activado.

**"Connection refused" en Ollama** → Ejecuta `ollama serve` y verifica con `ollama list` que el modelo está instalado (solo si usas Ollama).

**El selector de modelo no muestra mi proveedor** → Falta su API key en el `.env` (`GEMINI_API_KEY`/`OPENAI_API_KEY`/`DEEPSEEK_API_KEY`). Solo se listan los proveedores con clave configurada (Ollama siempre).

**Puerto 5000 ya en uso** → Un proceso del bot anterior quedó vivo: `netstat -ano | findstr :5000` y ciérralo.

**Señal no validada** → No alcanza la confianza o el R:R mínimo del agente, spread por encima del filtro, entry lejos del precio, o la mesa la vetó por topes de riesgo/exposición.

**`401 unauthorized` desde el dashboard** → Tienes `API_TOKEN` en el `.env` pero el dashboard no lo envía: define `VITE_API_TOKEN` (mismo valor) en el `.env` del dashboard o en su pestaña *Ajustes*.

**El bot dejó de abrir operaciones pero sigue corriendo** → Se alcanzó el límite de pérdida diaria (`MAX_DAILY_LOSS_PCT`): está en *cooldown*, esperando a que se cierren las posiciones abiertas. Se rearma solo al cambiar de día.

---

> **AVISO**: Este bot ejecuta órdenes reales. El trading conlleva riesgo de pérdida. Úsalo bajo tu propia responsabilidad.
