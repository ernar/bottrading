import { useEffect, useRef, useState, useCallback } from 'react'
import { getApiUrl, getApiHeaders } from '../config'

const API_URL = getApiUrl()

interface ChatMessage {
  role: 'user' | 'model'
  content: string
}

interface AssistantInfo {
  provider: string
  model: string
  available: boolean
}

// Sesión persistente: el id sobrevive a recargas para conservar la memoria de
// la conversación en el backend.
function getSessionId(): string {
  const KEY = 'assistantSessionId'
  let id = localStorage.getItem(KEY)
  if (!id) {
    id = `web-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    localStorage.setItem(KEY, id)
  }
  return id
}

// Sugerencias iniciales (cuando aún no hay conversación).
const INITIAL_SUGGESTIONS = [
  '¿Cómo vamos?',
  '¿Qué posiciones tenemos?',
  '¿Cuál es el riesgo ahora?',
]

// Comandos disponibles en el chat (se escriben con "/").
const HELP_TEXT = [
  'Comandos disponibles:',
  '• /clear — borra la conversación y la memoria',
  '• /help — muestra esta ayuda',
  '• /close — cierra el chat',
].join('\n')

export function ChatWidget() {
  const [open, setOpen] = useState(false)
  // `closing` mantiene el panel montado mientras corre la animación de cierre.
  const [closing, setClosing] = useState(false)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [suggestions, setSuggestions] = useState<string[]>(INITIAL_SUGGESTIONS)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [info, setInfo] = useState<AssistantInfo | null>(null)
  const sessionId = useRef(getSessionId())
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Cierra el panel tras reproducir la animación de salida (≈180ms).
  const requestClose = useCallback(() => {
    if (closing) return
    setClosing(true)
    closeTimer.current = setTimeout(() => { setOpen(false); setClosing(false) }, 180)
  }, [closing])

  useEffect(() => () => { if (closeTimer.current) clearTimeout(closeTimer.current) }, [])

  // Carga inicial perezosa (al abrir por primera vez).
  const loaded = useRef(false)
  useEffect(() => {
    if (!open || loaded.current) return
    loaded.current = true
    fetch(`${API_URL}/api/assistant/info`, { headers: getApiHeaders() })
      .then(r => r.json()).then(setInfo).catch(() => {})
    fetch(`${API_URL}/api/assistant/history?session_id=${sessionId.current}`, { headers: getApiHeaders() })
      .then(r => r.json())
      .then(d => { if (Array.isArray(d.history) && d.history.length) setMessages(d.history) })
      .catch(() => {})
  }, [open])

  // Permite abrir el chat desde otros sitios (p. ej. el diagrama de la Mesa).
  useEffect(() => {
    const openHandler = () => {
      if (closeTimer.current) clearTimeout(closeTimer.current)
      setClosing(false)
      setOpen(true)
    }
    window.addEventListener('assistant:open', openHandler)
    return () => window.removeEventListener('assistant:open', openHandler)
  }, [])

  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 50)
  }, [open])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, busy, suggestions])

  const reset = useCallback(() => {
    fetch(`${API_URL}/api/assistant/reset`, {
      method: 'POST',
      headers: { ...getApiHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId.current }),
    }).finally(() => {
      setMessages([])
      setSuggestions(INITIAL_SUGGESTIONS)
    })
  }, [])

  const send = useCallback((text: string) => {
    const message = text.trim()
    if (!message || busy) return

    // Comandos locales (no van al backend salvo /clear que resetea memoria).
    if (message.startsWith('/')) {
      const cmd = message.toLowerCase().split(/\s+/)[0]
      setInput('')
      if (cmd === '/clear') { reset(); return }
      if (cmd === '/close') { requestClose(); return }
      if (cmd === '/help') {
        setMessages(prev => [...prev, { role: 'user', content: message }, { role: 'model', content: HELP_TEXT }])
        return
      }
      setMessages(prev => [...prev, { role: 'user', content: message },
        { role: 'model', content: `Comando no reconocido: ${cmd}\n\n${HELP_TEXT}` }])
      return
    }

    setMessages(prev => [...prev, { role: 'user', content: message }])
    setInput('')
    setSuggestions([])
    setBusy(true)
    fetch(`${API_URL}/api/assistant/chat`, {
      method: 'POST',
      headers: { ...getApiHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, session_id: sessionId.current }),
    })
      .then(r => r.json())
      .then(d => {
        const reply = d.reply || d.error || 'No he podido responder.'
        const extra: ChatMessage[] = [{ role: 'model', content: reply }]
        // Confirmación del efecto real: el backend fijó/retiró una nota de dirección
        // para la mesa (solo viene cuando el asistente emitió el marcador).
        if (d.director_note !== undefined) {
          extra.push({
            role: 'model',
            content: d.director_note
              ? `📝 Nota fijada para la mesa: “${d.director_note}”. La tendrá en cuenta en las próximas rotaciones (visible en la pestaña Mesa).`
              : '🗑️ He retirado la nota de dirección de la mesa.',
          })
        }
        setMessages(prev => [...prev, ...extra])
        setSuggestions(Array.isArray(d.suggestions) ? d.suggestions : [])
      })
      .catch(() => {
        setMessages(prev => [...prev, { role: 'model', content: 'Error de conexión con el backend.' }])
      })
      .finally(() => setBusy(false))
  }, [busy, reset, requestClose])

  return (
    <>
      {/* Botón burbuja */}
      <button
        onClick={() => (open ? requestClose() : setOpen(true))}
        aria-label={open ? 'Cerrar asistente' : 'Abrir asistente'}
        className="fixed bottom-5 right-5 z-50 w-14 h-14 rounded-full bg-emerald-600 hover:bg-emerald-500 shadow-lg shadow-emerald-500/30 flex items-center justify-center text-2xl transition"
      >
        {open && !closing ? '✕' : '🧑‍💼'}
      </button>

      {/* Panel de chat */}
      {(open || closing) && (
        <div className={`${closing ? 'animate-chat-pop-out' : 'animate-chat-pop'} fixed bottom-24 right-5 z-50 w-[calc(100vw-2.5rem)] sm:w-[400px] h-[70vh] max-h-[600px] flex flex-col bg-gray-900 border border-gray-700 rounded-xl shadow-2xl shadow-black/50 overflow-hidden`}>
          {/* Cabecera */}
          <div className="flex items-center justify-between gap-2 px-4 py-3 bg-gray-800 border-b border-gray-700">
            <div className="min-w-0">
              <div className="text-sm font-bold flex items-center gap-2">
                <span>🧑‍💼</span> Responsable de la mesa
              </div>
              {info && (
                <div className="text-[10px] text-gray-500">
                  {info.provider.toUpperCase()}/{info.model}{' '}
                  {info.available
                    ? <span className="text-green-400">●</span>
                    : <span className="text-red-400" title="Falta la API key en Ajustes">● sin clave</span>}
                </div>
              )}
            </div>
            <div className="flex items-center gap-1">
              <button onClick={reset} title="Nueva conversación (/clear)"
                className="px-2 py-1 text-xs rounded bg-gray-700 hover:bg-gray-600">↺</button>
              <button onClick={requestClose} title="Cerrar"
                className="px-2 py-1 text-xs rounded bg-gray-700 hover:bg-gray-600">✕</button>
            </div>
          </div>

          {info && !info.available && (
            <div className="bg-yellow-900/40 border-b border-yellow-700 text-yellow-200 text-xs px-3 py-2">
              Configura el token en <span className="font-semibold">Ajustes → Asistente</span>.
            </div>
          )}

          {/* Mensajes */}
          <div ref={scrollRef} className="flex-1 overflow-y-auto p-3 space-y-3">
            {messages.length === 0 && (
              <p className="text-gray-500 text-sm">
                Hola, soy el responsable de la mesa. ¿Sobre qué quieres que te ponga al día?
                <br />
                <span className="text-gray-600 text-xs">Escribe /help para ver los comandos.</span>
              </p>
            )}
            {messages.map((m, i) => (
              <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div className={`max-w-[88%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap ${
                  m.role === 'user' ? 'bg-emerald-600 text-white' : 'bg-gray-800 text-gray-100 border border-gray-700'
                }`}>
                  {m.content}
                </div>
              </div>
            ))}
            {busy && (
              <div className="flex justify-start">
                <div className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-400">
                  <span className="animate-pulse">Revisando los datos…</span>
                </div>
              </div>
            )}

            {/* Sugerencias de seguimiento */}
            {!busy && suggestions.length > 0 && (
              <div className="flex flex-wrap gap-2 pt-1">
                {suggestions.map(s => (
                  <button key={s} onClick={() => send(s)}
                    className="px-3 py-1.5 text-xs rounded-full bg-gray-800 border border-gray-700 hover:border-emerald-500 hover:text-white text-gray-300">
                    {s}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Entrada */}
          <form onSubmit={e => { e.preventDefault(); send(input) }}
            className="p-2 border-t border-gray-700 flex gap-2">
            <input
              ref={inputRef}
              value={input}
              onChange={e => setInput(e.target.value)}
              placeholder="Pregunta o /comando…"
              className="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-emerald-500"
            />
            <button type="submit" disabled={busy || !input.trim()}
              className="px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-sm font-medium">
              ➤
            </button>
          </form>
        </div>
      )}
    </>
  )
}
