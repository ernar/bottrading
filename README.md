# MT5 Ollama Bot

Bot de trading automático que integra **MetaTrader 5/4** con un **LLM** (Ollama local por defecto, `qwen3:8b`) para generar y ejecutar señales basadas en análisis técnico. El núcleo es un sistema **agéntico**: un agente especializado por símbolo, cada uno con su propio modelo, parámetros, persona y memoria, coordinados por un orquestador que además auto-optimiza sus parámetros según el rendimiento.

## Estructura del proyecto

```
mt5_ollama_bot/
├── main.py                  # Punto de entrada: selección de agentes + orquestador + API en hilo interno
├── agents/
│   ├── base_agent.py        # SymbolAgent: símbolo + modelo + AgentParams + persona + memoria aislada
│   ├── registry.py          # Blueprints declarativos de agentes (btc-agent…) + build_agent()
│   └── orchestrator.py      # AgentOrchestrator: corre el loop, ejecuta señales y optimize()
├── core/
│   ├── strategy.py          # Motor de estrategia (LLM + validación de señales)
│   ├── indicators.py        # Indicadores técnicos (RSI, EMA, MACD, Bollinger, ATR, S/R)
│   ├── market_context.py    # Contexto estructurado para el LLM (indicadores + velas + memoria)
│   ├── memory.py            # SignalMemory: registro de señales con evaluación de resultados
│   ├── news.py              # Noticias (RSS Yahoo) + calendario económico (ForexFactory)
│   ├── bot_state.py         # Contenedor de estado thread-safe
│   ├── state.py             # Singleton compartido entre main y api server
│   ├── trade_metrics.py     # Cálculo de SL/TP, tamaño de lote y métricas de trade
│   ├── logger.py            # Logging de señales y trades a CSV
│   └── models.py            # Modelos Pydantic (BotConfig, Position, etc.)
├── clients/
│   ├── base_client.py       # Interfaz común MT4/MT5
│   ├── mt5_client.py        # Wrapper de MetaTrader 5 (datos, órdenes, cuenta)
│   └── mt4_client.py        # Bridge con MT4 vía EA (PythonBridge.mq4)
├── api/server.py            # Flask REST API + WebSocket para el dashboard
├── mt4_ea/PythonBridge.mq4  # EA puente para MT4
├── tests/                   # Tests con pytest (funciones puras)
├── requirements.txt
├── requirements-dev.txt     # Dependencias de test (pytest)
├── conftest.py              # Hace que pytest encuentre los paquetes del proyecto
├── start.bat                # Arranque en Windows (main.py + frontend)
├── .env                     # Credenciales + config (NO subir a git; gitignored)
├── .env.example             # Plantilla sin secretos
├── logs/                    # Generado automáticamente (gitignored)
│   ├── memory.json          # Memoria de señales y resultados evaluados
│   ├── agents/              # Memoria aislada por agente (<name>_memory.json)
│   └── {mt5|mt4}/
│       ├── signals.csv      # Historial de señales generadas
│       └── trades.csv       # Historial de órdenes ejecutadas
└── frontend/                # Dashboard React + TypeScript + Tailwind
```

## Requisitos

- Python 3.8+
- MetaTrader 5 instalado y abierto (o MT4 con el EA `PythonBridge.mq4` adjunto)
- [Ollama](https://ollama.ai) corriendo localmente con `qwen3:8b`:
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
winget install Ollama.Ollama
```

> Si `winget` no está disponible, descarga e instala manualmente: [Python 3.8+](https://www.python.org/downloads/) (marca **"Add python.exe to PATH"**), [Node.js 18+](https://nodejs.org/) y [Ollama](https://ollama.ai). Cierra y reabre la terminal tras instalar para refrescar el PATH.

### 2. Plataforma de trading (MT5 y/o MT4)

El VPS debe tener instalado y abierto el terminal del broker.

**MT5:**
```bat
winget install MetaQuotes.MetaTrader5
```
O descarga el instalador de tu broker. Abre MT5, inicia sesión con tu cuenta y deja el terminal **abierto** mientras el bot corre.

**MT4** (solo si vas a operar en MT4):
```bat
winget install MetaQuotes.MetaTrader4
```
O descarga el MT4 de tu broker. Además del terminal hay que instalar el **EA puente**:

1. En MT4: *Archivo → Abrir carpeta de datos*.
2. Copia [`mt4_ea/PythonBridge.mq4`](mt4_ea/PythonBridge.mq4) a `MQL4\Experts\`.
3. En MetaEditor compila `PythonBridge.mq4` (F7) o reinicia MT4 para que aparezca en el *Navegador*.
4. Arrastra `PythonBridge` a cualquier gráfico.
5. Activa **"Permitir trading automático"** (botón AutoTrading en verde) y, en *Herramientas → Opciones → Expert Advisors*, marca *"Permitir WebRequest/DLL"* si tu configuración lo requiere.

### 3. Backend (Python)

Desde la raíz del proyecto, actualiza `pip` e instala las dependencias:

```bat
python -m pip install --upgrade pip
pip install -r requirements.txt
```

> **No instales `eventlet`**: rompe el WebSocket del API (corre en hilo plano sin monkey-patch). El proyecto usa `simple-websocket`.

### 4. Frontend (dashboard)

```bat
cd frontend
npm install
cd ..
```

### 5. Ollama (modelo LLM local por defecto)

```bat
ollama pull qwen3:8b
```

`ollama serve` se ejecuta como servicio en segundo plano tras instalar. Verifica los modelos disponibles con `ollama list`. (~5 GB de RAM por el modelo `qwen3:8b`.)

## Configuración

Crea `.env` en la raíz:

```env
MT5_LOGIN=tu_numero_cuenta
MT5_PASSWORD=tu_contraseña
MT5_SERVER=nombre_servidor
NEWS_ENABLED=true   # noticias/calendario económico en el contexto del LLM (por defecto: true)

# Catálogo de modelos Ollama disponibles (los que muestra `ollama list`)
OLLAMA_MODELS=qwen3:8b,deepseek-r1:8b,llama3.1:latest

# Proveedores LLM en la nube (opcionales): rellena la clave para activarlos.
# Solo aparecen en el selector de modelo si su clave está configurada.
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
GEMINI_API_KEY=
GEMINI_MODEL=gemini-2.0-flash
```

> Los umbrales y símbolos **no se configuran en `.env`**: cada agente trae los suyos en su blueprint (`agents/registry.py`). El **modelo** se elige al arrancar (menú interactivo) o en caliente desde el dashboard.

### Elegir el modelo de cada agente

Cada agente trae un modelo por defecto en su blueprint, pero puedes cambiarlo:

- **Al arrancar (`python main.py`)**: tras elegir los agentes, el menú pregunta el provider/modelo de cada uno. Solo lista proveedores con clave configurada (Ollama siempre disponible; OpenAI/Gemini solo si pusiste su API key). Pulsa Enter para mantener el del blueprint.
- **En caliente desde el dashboard**: en la pestaña **Agentes**, el desplegable "Modelo LLM" de cada tarjeta cambia el modelo sin reiniciar el bot (efectivo en el siguiente ciclo de análisis).

### Límites de operaciones por símbolo/modelo

Configura cuántas operaciones máximas puede tener abiertas cada agente usando variables de entorno en `.env`:

```env
# Límites por símbolo (precedencia 1 — máxima prioridad)
MAX_OPEN_POSITIONS_BTCUSD=2        # Bitcoin: máximo 2 operaciones
MAX_OPEN_POSITIONS_EURUSD=3        # Euro: máximo 3 operaciones

# Límites por modelo (precedencia 2)
MAX_OPEN_POSITIONS_QWEN3_8B=3      # Qwen: máximo 3 operaciones
MAX_OPEN_POSITIONS_GPT4=4          # GPT-4: máximo 4 operaciones

# Fallback global (precedencia 3)
MAX_OPEN_POSITIONS_DEFAULT=5       # Otros símbolos/modelos: máximo 5
```

**Precedencia**: símbolo > modelo > default > blueprint. La primera coincidencia gana.

También puedes configurar otros parámetros del agente: `MIN_CONFIDENCE_*`, `MIN_RR_*`, `ATR_SL_MULT_*`, `TEMPERATURE_*`, etc. Ver [`.env.example.advanced`](.env.example.advanced) para una configuración completa.

Para ver cómo se resuelven los límites en tiempo real:
```bash
python examples_config.py
```

## Arranque

```bat
start.bat
```

Esto lanza `main.py` (orquestador + API en el mismo proceso) y el dashboard React en `http://localhost:5173`.

Puedes también arrancar solo el bot:
```bash
python main.py
```

## Flujo de ejecución

1. Selección interactiva de **plataforma** (MT5 / MT4) y de **agentes** (cada agente ya trae su símbolo, modelo y configuración — ya no se eligen modelo ni símbolos por separado).
2. Conecta a MT5/MT4 con las credenciales del `.env`.
3. Inicia el API server en hilo de fondo (puerto 5000).
4. El `AgentOrchestrator` corre un loop cada 60 segundos. En cada ciclo, por cada agente activo:
   - Evalúa señales anteriores contra el precio actual (memoria de resultados, aislada por agente).
   - Construye contexto estructurado: indicadores H1 y H4 calculados en Python
     (RSI, EMA20/50, MACD, Bollinger, ATR, soportes/resistencias), últimas velas,
     posiciones abiertas, noticias y eventos económicos próximos y rendimiento reciente.
   - Inyecta la **persona** del agente al prompt del sistema.
   - Envía el contexto al LLM (formato JSON forzado) → recibe señal.
   - Si SL=0 y acción ≠ HOLD, calcula SL/TP de respaldo con los múltiplos de ATR del agente.
   - Valida contra los umbrales del agente: confianza mínima, R:R mínimo, niveles SL/TP
     coherentes por lado, entry cercano al precio real, sin posición duplicada en la misma
     dirección, máximo de posiciones y filtro de spread.
   - Ejecuta orden si pasa validación.
   - Registra señal en `logs/` y en la memoria del agente (`logs/agents/<name>_memory.json`).
5. Cada 20 ciclos el orquestador llama a `optimize()`: ajuste **basado en reglas** (no ML) de
   los `AgentParams` de cada agente según su rendimiento (win rate, SL/TP tocados, holds…),
   con límites en `PARAM_BOUNDS`.

## Agentes

Los agentes se definen como *blueprints* declarativos en [`agents/registry.py`](agents/registry.py).
**Añadir un símbolo = añadir un blueprint.** Agente incluido:

| Agente | Símbolo | Modelo | Notas |
|---|---|---|---|
| `btc-agent` | BTCUSD | ollama/qwen3:8b | Cripto 24/7, alta volatilidad: R:R ≥ 1:1.5, ATR SL 1.8× / TP 2.7×, máx. 2 posiciones, filtro de spread alto |

Cada agente expone parámetros (`AgentParams`): `provider`, `model`, `min_confidence`, `min_rr`,
`atr_sl_mult`, `atr_tp_mult`, `lot_size`, `risk_per_trade`, `max_open_positions`, `max_spread_filter`.

## API endpoints

```
GET  /health                        Estado del servidor
GET  /api/state                     Estado completo del bot
GET  /api/account                   Info de cuenta
GET  /api/signals                   Última señal por símbolo
GET  /api/positions                 Posiciones abiertas
GET  /api/history                   Trades cerrados
GET  /api/stats                     Estadísticas agregadas (señales, win rate de memoria)
GET  /api/agents                    Resumen de agentes: config, stats de sesión y última optimización
POST /api/agents/optimize           Lanza optimización (dry-run; {"apply": true} para aplicar en caliente)
GET  /api/models                    Proveedores/modelos LLM disponibles (según claves del .env)
POST /api/agents/{name}/model       Cambia el modelo de un agente en caliente ({"provider","model"})
GET  /api/csv/signals               Últimas señales del CSV (?limit=&platform=)
GET  /api/csv/trades                Últimos trades del CSV (?limit=&platform=)
POST /api/bot/start                 Iniciar bot
POST /api/bot/stop                  Pausar bot
POST /api/positions/{symbol}/close  Cerrar posición
```

## Parámetros de riesgo por defecto (`AgentParams`)

| Parámetro | Valor por defecto |
|---|---|
| Lot size | 0.01 |
| Riesgo por trade | 2% del balance |
| Confianza mínima | 60% |
| R:R mínimo | 1:1 |
| Máx. posiciones abiertas | 5 |
| SL automático | 1.5 × ATR H1 |
| TP automático | 2.0 × ATR H1 |
| Filtro de spread | 2.0 puntos |

> Cada agente puede sobreescribir estos valores en su blueprint (ver `btc-agent` arriba), y el
> orquestador los ajusta en caliente vía `optimize()`.

## Notas

- Usar siempre cuenta **Demo** antes de real.
- MT5 debe estar abierto durante la ejecución (o MT4 con el EA `PythonBridge.mq4` adjunto a un gráfico y el trading automático activado).
- `qwen3:8b` requiere ~5 GB de RAM.
- Los CSV en `logs/` y la memoria de cada agente persisten entre sesiones.

## Troubleshooting

**"No se pudo conectar a MT5"** → MT5 debe estar abierto y las credenciales en `.env` deben ser correctas.

**"No se pudo conectar al EA de MT4"** → MT4 abierto, EA `PythonBridge` adjunto a un gráfico y "Permitir trading automático" activado.

**"Connection refused" en Ollama** → Ejecuta `ollama serve` y verifica con `ollama list` que `qwen3:8b` está instalado.

**Puerto 5000 ya en uso** → Un proceso del bot anterior quedó vivo: `netstat -ano | findstr :5000` y ciérralo.

**Señal no validada** → No alcanza la confianza o el R:R mínimo del agente, demasiadas posiciones abiertas, spread por encima del filtro o entry lejos del precio.

---

> **AVISO**: Este bot ejecuta órdenes reales. El trading conlleva riesgo de pérdida. Úsalo bajo tu propia responsabilidad.
