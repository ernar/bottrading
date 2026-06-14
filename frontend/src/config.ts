// Configuración de conexión al backend resuelta en runtime.
// Prioridad: valor guardado en localStorage (editable desde la UI) >
// variable de entorno de build (VITE_API_URL / VITE_API_TOKEN) > default.
// Los módulos leen estos getters al cargarse; tras guardar se recarga la
// página (ver Settings.tsx) para que axios y el socket tomen el nuevo valor.

const DEFAULT_API_URL = 'http://localhost:5000'

const URL_KEY = 'apiUrl'
const TOKEN_KEY = 'apiToken'

export function getApiUrl(): string {
  const stored = localStorage.getItem(URL_KEY)
  if (stored) return stored.replace(/\/+$/, '') // sin barra final
  return (import.meta.env.VITE_API_URL || DEFAULT_API_URL).replace(/\/+$/, '')
}

export function getApiToken(): string {
  const stored = localStorage.getItem(TOKEN_KEY)
  if (stored !== null) return stored
  return import.meta.env.VITE_API_TOKEN || ''
}

// true si la URL/token actual provienen de un override guardado en la UI.
export function hasUrlOverride(): boolean {
  return localStorage.getItem(URL_KEY) !== null
}

export function setApiConfig(url: string, token: string): void {
  const clean = url.trim().replace(/\/+$/, '')
  if (clean) localStorage.setItem(URL_KEY, clean)
  else localStorage.removeItem(URL_KEY)

  if (token.trim()) localStorage.setItem(TOKEN_KEY, token.trim())
  else localStorage.removeItem(TOKEN_KEY)
}

// Borra el override y vuelve al valor del entorno/default.
export function resetApiConfig(): void {
  localStorage.removeItem(URL_KEY)
  localStorage.removeItem(TOKEN_KEY)
}

// Cabeceras comunes para toda petición al backend:
// - X-API-Token: si el backend exige token (rutas que mutan estado).
// - ngrok-skip-browser-warning: salta la página intersticial de ngrok-free, que
//   de otro modo responde HTML sin CORS y rompe los fetch/XHR ("Failed to fetch").
export function getApiHeaders(): Record<string, string> {
  const headers: Record<string, string> = { 'ngrok-skip-browser-warning': 'true' }
  const token = getApiToken()
  if (token) headers['X-API-Token'] = token
  return headers
}

// Valor del entorno (para mostrar de referencia en la UI).
export const ENV_API_URL = import.meta.env.VITE_API_URL || DEFAULT_API_URL
