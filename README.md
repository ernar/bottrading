# MT5 Ollama Bot

Bot de trading automático que integra **MetaTrader 5** con **Ollama** (IA local) para generar y ejecutar señales de trading basadas en análisis técnico.

## Estructura del proyecto

```
mt5_ollama_bot/
├── main.py                  # Punto de entrada: bot loop + API server en hilo interno
├── core/
│   ├── strategy.py          # Motor de estrategia (IA + validación de señales)
│   ├── indicators.py        # Indicadores técnicos (RSI, EMA, MACD, Bollinger, ATR, S/R)
│   ├── market_context.py    # Contexto estructurado para la IA (indicadores + velas + memoria)
│   ├── memory.py            # Memoria persistente de señales con evaluación de resultados
│   ├── news.py              # Noticias (RSS Yahoo) + calendario económico (ForexFactory)
│   ├── bot_state.py         # Contenedor de estado thread-safe
│   ├── state.py             # Singleton compartido entre main y api server
│   ├── logger.py            # Logging de señales y trades a CSV
│   └── models.py            # Modelos Pydantic (BotConfig, Position, etc.)
├── clients/
│   ├── base_client.py       # Interfaz común MT4/MT5
│   ├── mt5_client.py        # Wrapper de MetaTrader 5 (datos, órdenes, cuenta)
│   └── mt4_client.py        # Bridge con MT4 vía EA (PythonBridge.mq4)
├── api/server.py            # Flask REST API + WebSocket para el dashboard
├── requirements.txt
├── start.bat                # Arranque en Windows (main.py + frontend)
├── .env                     # Credenciales MT5 (no subir a git)
├── logs/                    # Generado automáticamente
│   ├── memory.json          # Memoria de señales y resultados evaluados
│   └── {mt5|mt4}/
│       ├── signals.csv      # Historial de señales generadas
│       └── trades.csv       # Historial de órdenes ejecutadas
└── frontend/                # Dashboard React + TypeScript + Tailwind
```

## Requisitos

- Python 3.8+
- MetaTrader 5 instalado y abierto
- [Ollama](https://ollama.ai) corriendo localmente con `qwen3:8b`:
  ```
  ollama pull qwen3:8b
  ```
- Node.js 18+ (solo para el dashboard)

## Instalación

```bash
pip install -r requirements.txt
cd frontend && npm install
```

## Configuración

Crea `.env` en la raíz:

```env
MT5_LOGIN=tu_numero_cuenta
MT5_PASSWORD=tu_contraseña
MT5_SERVER=nombre_servidor
MODEL=qwen3:8b
SYMBOLS=EURUSD,XAUUSD,BTCUSD
NEWS_ENABLED=true   # noticias/calendario económico en el contexto de la IA (por defecto: true)
```

## Arranque

```bat
start.bat
```

Esto lanza `main.py` (bot + API en el mismo proceso) y el dashboard React en `http://localhost:5173`.

Puedes también arrancar solo el bot:
```bash
python main.py
```

## Flujo de ejecución

1. Conecta a MT5/MT4 con credenciales del `.env`
2. Inicia el API server en hilo de fondo (puerto 5000)
3. Muestra selectores interactivos de plataforma, modelo IA y símbolos
4. Loop cada 60 segundos por símbolo:
   - Evalúa señales anteriores contra el precio actual (memoria de resultados)
   - Construye contexto estructurado: indicadores H1 y H4 calculados en Python
     (RSI, EMA20/50, MACD, Bollinger, ATR, soportes/resistencias), últimas 24 velas,
     posiciones abiertas, noticias y eventos económicos próximos (impacto medio/alto,
     ventana de 24h) y rendimiento reciente de las señales en ese símbolo
   - Si hay un evento de impacto alto a <2h, la IA tiene instrucción de devolver hold
   - Envía el contexto a la IA (formato JSON forzado) → recibe señal
   - Si SL=0 y acción ≠ HOLD, calcula SL=1.5×ATR, TP=2×ATR de respaldo
   - Valida: confianza ≥ 60%, niveles SL/TP coherentes por lado, R:R ≥ 1:1,
     entry a menos de 0.5% del precio real, sin posición duplicada en la misma
     dirección, máximo de posiciones y riesgo
   - Ejecuta orden si pasa validación
   - Registra señal en `logs/` y en la memoria (`logs/memory.json`)

## API endpoints

```
GET  /health                        Estado del servidor
GET  /api/state                     Estado completo del bot
GET  /api/account                   Info de cuenta
GET  /api/signals                   Última señal por símbolo
GET  /api/positions                 Posiciones abiertas
GET  /api/history                   Trades cerrados
GET  /api/stats                     Estadísticas agregadas (señales, win rate de memoria)
GET  /api/csv/signals               Últimas señales del CSV (?limit=&platform=)
GET  /api/csv/trades                Últimos trades del CSV (?limit=&platform=)
POST /api/bot/start                 Iniciar bot
POST /api/bot/stop                  Pausar bot
POST /api/positions/{symbol}/close  Cerrar posición
```

## Parámetros de riesgo

| Parámetro | Valor por defecto |
|---|---|
| Lot size | 0.01 |
| Riesgo por trade | 2% del balance |
| Confianza mínima | 60% |
| Máx. posiciones abiertas | 5 |
| SL automático | 1.5 × ATR H1 |
| TP automático | 2.0 × ATR H1 |

## Notas

- Usar siempre cuenta **Demo** antes de real
- MT5 debe estar abierto durante la ejecución
- `qwen3:8b` requiere ~5 GB de RAM
- Los CSV en `logs/` persisten entre sesiones

## Troubleshooting

**"No se pudo conectar a MT5"** → MT5 debe estar abierto y las credenciales en `.env` deben ser correctas.

**"Connection refused" en Ollama** → Ejecuta `ollama serve` y verifica con `ollama list` que `qwen3:8b` está instalado.

**Señal no validada** → Confianza < 60%, demasiadas posiciones abiertas, o riesgo alto con posiciones existentes.

---

> **AVISO**: Este bot ejecuta órdenes reales. El trading conlleva riesgo de pérdida. Úsalo bajo tu propia responsabilidad.
