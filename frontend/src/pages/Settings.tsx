import { useState } from 'react'
import {
  getApiUrl,
  getApiToken,
  setApiConfig,
  resetApiConfig,
  hasUrlOverride,
  ENV_API_URL,
} from '../config'

// Cabeceras para el test: usa el token escrito (aún sin guardar) + skip de ngrok.
const ngrokSkip = { 'ngrok-skip-browser-warning': 'true' }

type TestState =
  | { status: 'idle' }
  | { status: 'testing' }
  | { status: 'ok'; detail: string }
  | { status: 'error'; detail: string }

export function SettingsPage() {
  const [url, setUrl] = useState(getApiUrl())
  const [token, setToken] = useState(getApiToken())
  const [test, setTest] = useState<TestState>({ status: 'idle' })

  const override = hasUrlOverride()

  // Prueba la conexión contra la URL escrita (sin guardar todavía).
  const testConnection = async () => {
    const base = url.trim().replace(/\/+$/, '')
    if (!base) {
      setTest({ status: 'error', detail: 'Introduce una URL.' })
      return
    }
    setTest({ status: 'testing' })
    try {
      const controller = new AbortController()
      const t = setTimeout(() => controller.abort(), 8000)
      const res = await fetch(`${base}/api/state`, {
        headers: token.trim() ? { ...ngrokSkip, 'X-API-Token': token.trim() } : ngrokSkip,
        signal: controller.signal,
      })
      clearTimeout(t)
      if (res.ok) {
        setTest({ status: 'ok', detail: `Conectado (HTTP ${res.status}).` })
      } else {
        setTest({ status: 'error', detail: `El servidor respondió HTTP ${res.status}.` })
      }
    } catch (e) {
      // "Failed to fetch" / AbortError no dan status HTTP: el navegador no llegó
      // al servidor. Casi siempre es que el backend no está arrancado.
      const msg = e instanceof Error ? e.message : 'error de red'
      const aborted = e instanceof DOMException && e.name === 'AbortError'
      const hint = aborted
        ? 'tiempo de espera agotado'
        : `${msg} — ¿está el backend arrancado (python main.py) y la URL es correcta?`
      setTest({ status: 'error', detail: `No se pudo conectar: ${hint}` })
    }
  }

  // Guarda y recarga para que axios y el WebSocket tomen la nueva URL.
  const save = () => {
    setApiConfig(url, token)
    window.location.reload()
  }

  const reset = () => {
    resetApiConfig()
    window.location.reload()
  }

  return (
    <div className="p-4 sm:p-8 max-w-2xl">
      <h2 className="text-xl font-bold mb-1">Ajustes de conexión</h2>
      <p className="text-sm text-gray-400 mb-6">
        URL del backend (Flask + WebSocket) al que se conecta este dashboard. Se guarda en
        este navegador y tiene prioridad sobre <code>VITE_API_URL</code>.
      </p>

      <div className="bg-gray-800 rounded-lg p-6 border border-gray-700 space-y-5">
        <div>
          <label className="block text-sm font-medium text-gray-300 mb-1">URL de la API</label>
          <input
            type="text"
            value={url}
            onChange={e => {
              setUrl(e.target.value)
              setTest({ status: 'idle' })
            }}
            placeholder="http://localhost:5000"
            className="w-full px-3 py-2 rounded bg-gray-900 border border-gray-600 text-white
                       focus:outline-none focus:border-blue-500"
          />
          <p className="text-xs text-gray-500 mt-1">
            Ejemplos: <code>http://localhost:5000</code>, <code>http://192.168.1.50:5000</code>.
            Sin barra final.
          </p>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-300 mb-1">
            API Token <span className="text-gray-500">(opcional)</span>
          </label>
          <input
            type="password"
            value={token}
            onChange={e => setToken(e.target.value)}
            placeholder="Solo si el backend tiene API_TOKEN configurado"
            className="w-full px-3 py-2 rounded bg-gray-900 border border-gray-600 text-white
                       focus:outline-none focus:border-blue-500"
          />
          <p className="text-xs text-gray-500 mt-1">
            Se envía como cabecera <code>X-API-Token</code> en las rutas que mutan estado.
          </p>
        </div>

        {test.status !== 'idle' && (
          <div
            className={`text-sm rounded px-3 py-2 ${
              test.status === 'ok'
                ? 'bg-green-900/40 text-green-300'
                : test.status === 'error'
                ? 'bg-red-900/40 text-red-300'
                : 'bg-gray-700 text-gray-300'
            }`}
          >
            {test.status === 'testing' ? 'Probando conexión…' : test.detail}
          </div>
        )}

        <div className="flex flex-wrap gap-2 pt-1">
          <button
            onClick={testConnection}
            disabled={test.status === 'testing'}
            className="px-3 py-2 text-sm rounded bg-gray-700 hover:bg-gray-600 disabled:opacity-50"
          >
            Probar conexión
          </button>
          <button
            onClick={save}
            className="px-3 py-2 text-sm rounded bg-blue-600 hover:bg-blue-500"
          >
            Guardar y recargar
          </button>
          <button
            onClick={reset}
            disabled={!override}
            className="px-3 py-2 text-sm rounded bg-gray-700 hover:bg-gray-600 disabled:opacity-50"
            title={override ? 'Volver al valor de VITE_API_URL' : 'No hay override guardado'}
          >
            Restablecer
          </button>
        </div>

        <div className="text-xs text-gray-500 border-t border-gray-700 pt-4 space-y-1">
          <div>
            URL activa: <span className="text-gray-300">{getApiUrl()}</span>
            {override ? (
              <span className="ml-2 text-amber-400">(override de la UI)</span>
            ) : (
              <span className="ml-2 text-gray-500">(desde el entorno)</span>
            )}
          </div>
          <div>
            Valor de entorno (<code>VITE_API_URL</code>):{' '}
            <span className="text-gray-300">{ENV_API_URL}</span>
          </div>
        </div>
      </div>
    </div>
  )
}
