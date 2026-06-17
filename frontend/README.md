# Dashboard (frontend)

Dashboard del **MT4 Ollama Bot**: panel React + TypeScript + Tailwind (build con Vite) que
consume el API REST + WebSocket del backend (Flask, puerto `5000`). Muestra estado del bot,
agentes, mesa de dirección (coordinador), gráficos, posiciones, historial y la terminal del bot
en tiempo real, e incluye un **asistente** (chatbot) para consultar el estado y dejar notas de
dirección a la mesa.

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

### Despliegue en Plesk

El dashboard es una **SPA estática**: Plesk solo tiene que servir el contenido de `dist/`.
El backend (Flask, `python main.py`) corre aparte (en la misma VPS o en otra máquina) y el
frontend lo alcanza por su URL pública. **No subas el código fuente al hosting**, solo el build.

1. **Compila apuntando al backend público.** El build inyecta `VITE_*` en tiempo de
   compilación (ver [Configuración](#configuración)), así que define la URL real **antes** de
   `npm run build`. En tu equipo, desde `frontend/`:

   ```bash
   # crea frontend/.env con la URL pública del backend
   #   VITE_API_URL=https://api.tudominio.com
   #   VITE_API_TOKEN=<mismo valor que API_TOKEN del .env de la raíz>
   npm install
   npm run build
   ```

   > Alternativa: deja el build neutro y fija la **URL/token desde la UI** (pestaña *Ajustes*,
   > se guarda en `localStorage`). Útil si no quieres recompilar por cada cambio de URL.

2. **Sube el contenido de `dist/`** (no la carpeta, su **contenido**: `index.html`, `assets/`,
   `favicon`, …) al *document root* del dominio/subdominio en Plesk —normalmente
   `httpdocs/`— vía **Administrador de archivos**, FTP o **Git** (Plesk → *Git*, apuntando a
   `dist/`). Borra el `index.html` de bienvenida que Plesk crea por defecto.

3. **Configura el fallback de SPA** (React Router usa rutas del lado cliente; sin esto, recargar
   `/agentes` o `/mesa` da **404**):

   - **Apache** (lo habitual en Plesk): crea `httpdocs/.htaccess` con:

     ```apache
     <IfModule mod_rewrite.c>
       RewriteEngine On
       RewriteBase /
       RewriteRule ^index\.html$ - [L]
       RewriteCond %{REQUEST_FILENAME} !-f
       RewriteCond %{REQUEST_FILENAME} !-d
       RewriteRule . /index.html [L]
     </IfModule>
     ```

   - **Nginx** (Plesk → *Apache & nginx Settings* → *Additional nginx directives*):

     ```nginx
     location / {
       try_files $uri $uri/ /index.html;
     }
     ```

4. **HTTPS:** activa el certificado **Let's Encrypt** (Plesk → *SSL/TLS Certificates*) en el
   dominio. Si el frontend va por `https://` el backend **también** debe ir por `https://`
   (contenido mixto: un backend en `http://` se bloquea desde una web en `https://`).

5. **Backend accesible y CORS.** El frontend llama directo al API (no hay proxy en producción,
   el proxy de Vite es solo para `npm run dev`). Asegúrate de que el backend está publicado y
   alcanzable desde el navegador:

   - Arráncalo con `API_HOST=0.0.0.0` y un `API_TOKEN` definido (ver el README de la raíz);
     **expón el `:5000` solo con token**.
   - Recomendado: ponlo tras un **subdominio con proxy inverso** en Plesk
     (p. ej. `api.tudominio.com` → `http://127.0.0.1:5000`) para servirlo por HTTPS y no abrir
     el `:5000` al exterior. El WebSocket (socket.io) necesita el *upgrade* de conexión; en las
     directivas nginx del proxy añade:

     ```nginx
     location /socket.io/ {
       proxy_pass http://127.0.0.1:5000;
       proxy_http_version 1.1;
       proxy_set_header Upgrade $http_upgrade;
       proxy_set_header Connection "upgrade";
       proxy_set_header Host $host;
     }
     ```

   - `VITE_API_URL` (o la URL fijada en *Ajustes*) debe apuntar a esa URL pública del backend.

> **Nota:** Plesk tiene soporte "Node.js", pero **no hace falta**: esta SPA se sirve como
> estáticos una vez compilada. El soporte Node solo sería útil si quisieras ejecutar el `build`
> en el propio servidor; aun así, el artefacto final que se sirve sigue siendo `dist/`.

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
│   ├── pages/              # Vistas: Dashboard, Agents, Coordinator (Mesa), Charts,
│   │   │                   #         Positions, History, Terminal, Settings (Ajustes)
│   ├── components/         # UI reutilizable (Header, Navigation, charts, ChatWidget,
│   │   │                   #         NewsTicker, RiskProfileSelector, SpreadSettings…)
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

- **Dashboard**: estado general del bot, cuenta, gráfico de portfolio y teletipo de noticias.
- **Agentes**: configuración, stats de sesión y cambio de modelo LLM en caliente.
- **Mesa** (coordinador): snapshot de riesgo, propuestas de los especialistas, veredictos,
  nota de dirección y LLM de la mesa.
- **Gráficos / Posiciones / Historial**: velas e indicadores, posiciones abiertas con P&L en
  vivo y trades cerrados.
- **Terminal**: salida en vivo de la consola del bot.
- **Asistente** (ChatWidget): chatbot que consulta el estado en vivo y puede dejar una nota de
  dirección para la mesa.
- **Ajustes**: URL/token del backend (override por navegador) y "Probar conexión", perfil de
  riesgo/horizonte, filtro de spread por símbolo y proveedor/modelo del asistente.
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
