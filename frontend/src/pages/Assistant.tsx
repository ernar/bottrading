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

const SUGGESTIONS = [
  '¿Cómo vamos hoy?',
  '¿Qué posiciones tenemos abiertas?',
  '¿Por qué la mesa vetó o aprobó las últimas entradas?',
  '¿Cuál es nuestra exposición y riesgo ahora mismo?',
  '¿Qué tal el rendimiento de los agentes?',
]

export function AssistantPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [info, setInfo] = useState<AssistantInfo | null>(null)
  const sessionId = useRef(getSessionId())
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    fetch(`${API_URL}/api/assistant/info`, { headers: getApiHeaders() })
      .then(r => r.json()).then(setInfo).catch(() => {})
    fetch(`${API_URL}/api/assistant/history?session_id=${sessionId.current}`, { headers: getApiHeaders() })
      .then(r => r.json())
      .then(d => { if (Array.isArray(d.history)) setMessages(d.history) })
      .catch(() => {})
  }, [])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, busy])

  const send = useCallback((text: string) => {
    const message = text.trim()
    if (!message || busy) return
    setMessages(prev => [...prev, { role: 'user', content: message }])
    setInput('')
    setBusy(true)
    fetch(`${API_URL}/api/assistant/chat`, {
      method: 'POST',
      headers: { ...getApiHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, session_id: sessionId.current }),
    })
      .then(r => r.json())
      .then(d => {
        const reply = d.reply || d.error || 'No he podido responder.'
        setMessages(prev => [...prev, { role: 'model', content: reply }])
      })
      .catch(() => {
        setMessages(prev => [...prev, { role: 'model', content: 'Error de conexión con el backend.' }])
      })
      .finally(() => setBusy(false))
  }, [busy])

  const reset = () => {
    fetch(`${API_URL}/api/assistant/reset`, {
      method: 'POST',
      headers: { ...getApiHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId.current }),
    }).finally(() => setMessages([]))
  }

  return (
    <div className="p-4 sm:p-8 max-w-4xl mx-auto flex flex-col" style={{ height: 'calc(100vh - 8rem)' }}>
      <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
        <div>
          <h2 className="text-xl font-bold flex items-center gap-2">
            <span className="text-2xl">🧑‍💼</span> Responsable de la mesa
          </h2>
          <p className="text-xs text-gray-500">
            Pregúntale por la cuenta, las posiciones, la mesa o los agentes. Responde con datos en vivo; no opera ni cambia ajustes.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {info && (
            <span className="text-xs text-gray-500">
              {info.provider.toUpperCase()}/{info.model}{' '}
              {info.available
                ? <span className="text-green-400">●</span>
                : <span className="text-red-400" title="Falta la API key en Ajustes">● sin clave</span>}
            </span>
          )}
          <button onClick={reset} className="px-3 py-1.5 text-xs rounded bg-gray-700 hover:bg-gray-600">
            Nueva conversación
          </button>
        </div>
      </div>

      {info && !info.available && (
        <div className="bg-yellow-900/40 border border-yellow-700 text-yellow-200 text-sm rounded p-3 mb-3">
          El asistente necesita una API key. Configúrala en <span className="font-semibold">Ajustes → Asistente</span>
          {' '}(token de {info.provider.toUpperCase()}).
        </div>
      )}

      <div ref={scrollRef} className="flex-1 overflow-y-auto bg-gray-900 rounded-lg border border-gray-700 p-4 space-y-4">
        {messages.length === 0 && (
          <div className="text-gray-500 text-sm">
            <p className="mb-3">Hola, soy el responsable de la mesa. ¿Sobre qué quieres que te ponga al día?</p>
            <div className="flex flex-wrap gap-2">
              {SUGGESTIONS.map(s => (
                <button key={s} onClick={() => send(s)}
                  className="px-3 py-1.5 text-xs rounded-full bg-gray-800 border border-gray-700 hover:border-blue-500 hover:text-white">
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[85%] rounded-lg px-4 py-2 text-sm whitespace-pre-wrap ${
              m.role === 'user' ? 'bg-blue-600 text-white' : 'bg-gray-800 text-gray-100 border border-gray-700'
            }`}>
              {m.content}
            </div>
          </div>
        ))}
        {busy && (
          <div className="flex justify-start">
            <div className="bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 text-sm text-gray-400">
              <span className="animate-pulse">El responsable está revisando los datos…</span>
            </div>
          </div>
        )}
      </div>

      <form
        onSubmit={e => { e.preventDefault(); send(input) }}
        className="mt-3 flex gap-2"
      >
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          placeholder="Escribe tu pregunta…"
          className="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 text-sm focus:outline-none focus:border-blue-500"
        />
        <button
          type="submit"
          disabled={busy || !input.trim()}
          className="px-5 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-sm font-medium"
        >
          Enviar
        </button>
      </form>
    </div>
  )
}
