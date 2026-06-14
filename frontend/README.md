# Dashboard (frontend)

Dashboard del **MT4 Ollama Bot**: panel React + TypeScript + Tailwind (build con Vite) que
consume el API REST + WebSocket del backend (Flask, puerto `5000`). Muestra estado del bot,
agentes, mesa de dirección (coordinador), señales, posiciones e historial en tiempo real.

> Este README cubre **solo el frontend**. Para el backend, los agentes y la configuración
> global del bot, ver el [README de la raíz](../README.md).

## Requisitos

- **Node.js 18+** y **npm** (incluido con Node).
- El **backend corriendo** (`python main.py` desde la raíz). Sin él, el dashboard arranca
  pero no recibe datos ("Failed to fetch" / "No se pudo conectar").

## Instalación

Desde la carpeta `frontend/`:

```bash
npm install
```

Esto instala las dependencias declaradas en `package.json` (React 18, React Router,
socket.io-client, axios, zustand, TanStack Table; Vite + Tailwind como dev deps).

## Configuración

La conexión al backend se resuelve **en runtime** con esta prioridad (ver
[`src/config.ts`](src/config.ts)):

```
localStorage (editable desde la UI, pestaña "Ajustes")
  > variable de build (VITE_API_URL / VITE_API_TOKEN)
    > default (http://localhost:5000)
```

### Opción A — desde la UI (recomendado, sin recompilar)

En la pestaña **Ajustes** del dashboard puedes fijar la **URL del backend** y el **token**.
Se guardan en el navegador (`localStorage`) y tienen prioridad sobre las variables de build,
útil para apuntar a un backend en otra máquina/VPS sin reconstruir. El botón **Probar
conexión** valida la URL contra `/api/state`.

### Opción B — variables de build (`.env`)

Copia la plantilla y rellena los valores (opcional; los defaults apuntan a `localhost:5000`):

```bash
cp .env.example .env
```

| Variable | Descripción |
|---|---|
| `VITE_API_URL` | URL del backend (Flask + WebSocket). Default `http://localhost:5000`. |
| `VITE_API_TOKEN` | Debe coincidir con `API_TOKEN` del `.env` de la raíz. Déjalo vacío si el backend no usa token (uso local). |

> Las variables `VITE_*` se inyectan **en tiempo de build**: si las cambias, reinicia
> `npm run dev` o vuelve a `npm run build`. El override guardado desde la UI no requiere
> rebuild.

> Si el backend tiene `API_TOKEN` definido, las rutas POST exigen la cabecera `X-API-Token`.
> Sin el token, el dashboard recibirá `401`. Configúralo por `VITE_API_TOKEN` o en **Ajustes**.

## Arranque

### Modo desarrollo

```bash
npm run dev
```

Disponible en **`http://localhost:3000`** (puerto fijado en [`vite.config.ts`](vite.config.ts)).
El dev server además **proxya `/api`** hacia `http://localhost:5000`, de modo que en local no
hace falta tocar CORS.

### Build de producción

```bash
npm run build     # type-check (tsc -b) + bundle de Vite → dist/
npm run preview   # sirve el build de dist/ para verificarlo
```

El resultado queda en `dist/` (gitignored). Sírvelo con cualquier servidor estático.

### Type-check sin compilar

```bash
npx tsc --noEmit
```

## Estructura

```
frontend/
├── index.html              # Punto de entrada HTML (monta /src/main.tsx)
├── src/
│   ├── main.tsx            # Bootstrap de React
│   ├── App.tsx             # Router + layout principal
│   ├── config.ts           # Resolución de URL/token del backend + cabeceras comunes
│   ├── pages/              # Vistas: Dashboard, Agents, Coordinator (Mesa),
│   │   │                   #         Signals, Positions, History, Settings (Ajustes)
│   ├── components/         # UI reutilizable (Header, Navigation, charts, modales…)
│   ├── hooks/              # useApi (REST), useWebSocket (streaming en vivo)
│   ├── types/              # Tipos TypeScript (bot.ts)
│   └── index.css           # Estilos globales (Tailwind)
├── public/                 # Assets estáticos (favicon, logo)
├── scripts/                # Utilidades (make_favicon.py)
├── package.json
├── vite.config.ts          # Puerto 3000 + proxy /api → :5000
├── tailwind.config.js
├── tsconfig.json
└── .env.example            # Plantilla de VITE_API_URL / VITE_API_TOKEN
```

## Funcionalidades

- **Dashboard**: estado general del bot, cuenta y gráfico de portfolio.
- **Agentes**: configuración, stats de sesión y cambio de modelo LLM en caliente.
- **Mesa** (coordinador): snapshot de riesgo, propuestas de los especialistas y veredictos.
- **Señales / Posiciones / Historial**: señales recientes, posiciones abiertas con P&L en
  vivo y trades cerrados.
- **Ajustes**: URL/token del backend (override por navegador) y "Probar conexión".
- **Tiempo real** vía WebSocket (socket.io).

## Stack

- **React 18** + **TypeScript**
- **Vite 5** (dev server y build)
- **Tailwind CSS 3**
- **React Router 6**
- **socket.io-client** (WebSocket) · **axios** (HTTP) · **zustand** (estado) ·
  **@tanstack/react-table** (tablas)

## Troubleshooting

**No conecta al API ("Failed to fetch" / "No se pudo conectar")**
- Asegúrate de que el backend corre: `python main.py` (puerto 5000) desde la raíz. Es la
  causa más común.
- Fija la URL en **Ajustes** y pulsa **Probar conexión**.
- En dev, el proxy `/api` de Vite asume el backend en `localhost:5000`; si está en otra
  máquina usa la URL completa en **Ajustes** o `VITE_API_URL`.

**`401 unauthorized`**
- El backend tiene `API_TOKEN` pero el dashboard no lo envía. Define `VITE_API_TOKEN` (mismo
  valor) en `frontend/.env` o ponlo en **Ajustes**.

**Túnel ngrok ("Failed to fetch" con una URL `*.ngrok-free.dev`)**
- Los túneles gratuitos de ngrok muestran una página intersticial; `fetch`/WebSocket reciben
  ese HTML (sin CORS) y el navegador reporta "Failed to fetch". El dashboard ya envía la
  cabecera `ngrok-skip-browser-warning` en cada petición (ver `getApiHeaders()` en
  [`src/config.ts`](src/config.ts)) para saltarla. Refresca, fija la URL en **Ajustes** y
  prueba la conexión.

**Falla el WebSocket**
- Revisa la consola del navegador, que el backend esté accesible y que el firewall permita la
  conexión. El backend fuerza `async_mode="threading"`; **nunca** añadir `eventlet` (rompe el
  WebSocket).
