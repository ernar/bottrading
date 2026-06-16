import { useEffect, useRef, useState, useCallback } from 'react'
import type { ReactNode, CSSProperties } from 'react'
import { getApiUrl, getApiHeaders } from '../config'

const API_URL = getApiUrl()
const POLL_MS = 1200          // cadencia del polling incremental
const MAX_LINES = 3000        // tope de líneas en pantalla (recorte por la cabeza)

interface ConsoleLine { seq: number; ts: string; src: string; text: string }
interface ConsoleSnapshot {
  lines: ConsoleLine[]
  latest_seq: number
  oldest_seq: number
  reset: boolean
}

// Códigos de color ANSI -> CSS (tema oscuro). Cubre los que emite core/console.py
// (rojo/verde/amarillo/azul/magenta/cian + bold + dim + reset).
const ANSI_COLORS: Record<number, string> = {
  31: '#f87171', 32: '#4ade80', 33: '#facc15',
  34: '#60a5fa', 35: '#e879f9', 36: '#22d3ee',
}

interface AnsiStyle { color?: string; bold?: boolean; dim?: boolean }

// Convierte una línea con secuencias ANSI (\x1b[..m) en spans estilizados.
function renderAnsi(text: string, keyBase: number): ReactNode[] {
  const out: ReactNode[] = []
  const re = /\x1b\[([0-9;]*)m/g
  let last = 0
  let style: AnsiStyle = {}
  let part = 0
  const push = (chunk: string, s: AnsiStyle) => {
    if (!chunk) return
    const css: CSSProperties = {}
    if (s.color) css.color = s.color
    if (s.bold) css.fontWeight = 700
    if (s.dim) css.opacity = 0.6
    out.push(<span key={`${keyBase}-${part++}`} style={css}>{chunk}</span>)
  }
  let m: RegExpExecArray | null
  while ((m = re.exec(text)) !== null) {
    push(text.slice(last, m.index), style)
    last = re.lastIndex
    const codes = m[1].split(';').filter(Boolean).map(Number)
    if (codes.length === 0) { style = {}; continue }  // ESC[m == reset
    for (const c of codes) {
      if (c === 0) style = {}
      else if (c === 1) style = { ...style, bold: true }
      else if (c === 2) style = { ...style, dim: true }
      else if (ANSI_COLORS[c]) style = { ...style, color: ANSI_COLORS[c] }
    }
  }
  push(text.slice(last), style)
  return out
}

export function TerminalPage() {
  const [lines, setLines] = useState<ConsoleLine[]>([])
  const [paused, setPaused] = useState(false)
  const [autoScroll, setAutoScroll] = useState(true)
  const [live, setLive] = useState(false)   // ¿llegó respuesta del backend?
  const cursorRef = useRef<number>(-1)       // último seq recibido (-1 = backlog)
  const pausedRef = useRef(paused)
  pausedRef.current = paused
  const autoScrollRef = useRef(autoScroll)
  autoScrollRef.current = autoScroll
  const scrollRef = useRef<HTMLDivElement | null>(null)

  const poll = useCallback(() => {
    if (pausedRef.current) return
    fetch(`${API_URL}/api/console?since=${cursorRef.current}`, { headers: getApiHeaders() })
      .then(r => r.json())
      .then((snap: ConsoleSnapshot) => {
        setLive(true)
        if (!snap || !Array.isArray(snap.lines)) return
        if (snap.reset) {
          // Primer pull, o el buffer giró: reemplaza lo mostrado.
          cursorRef.current = snap.latest_seq
          setLines(snap.lines.slice(-MAX_LINES))
          return
        }
        if (snap.lines.length === 0) return
        cursorRef.current = snap.latest_seq
        setLines(prev => {
          const merged = prev.concat(snap.lines)
          return merged.length > MAX_LINES ? merged.slice(-MAX_LINES) : merged
        })
      })
      .catch(() => setLive(false))
  }, [])

  useEffect(() => {
    poll()
    const id = setInterval(poll, POLL_MS)
    return () => clearInterval(id)
  }, [poll])

  // Auto-scroll al fondo cuando llegan líneas (si el usuario no subió a leer).
  useEffect(() => {
    if (!autoScrollRef.current) return
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [lines])

  // Si el usuario sube, suspende el auto-scroll; al volver al fondo, lo reanuda.
  const onScroll = () => {
    const el = scrollRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40
    if (atBottom !== autoScrollRef.current) setAutoScroll(atBottom)
  }

  const jumpToBottom = () => {
    setAutoScroll(true)
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }

  return (
    <div className="p-3 sm:p-6">
      <div className="flex flex-wrap items-center gap-3 mb-3">
        <h1 className="text-lg sm:text-xl font-bold">Terminal</h1>
        <span className="text-xs text-gray-400">
          réplica de <span className="font-mono">python main.py</span> · {lines.length} líneas
        </span>
        <span className={`flex items-center gap-1.5 text-xs ${live ? 'text-green-400' : 'text-gray-500'}`}>
          <span className={`w-2 h-2 rounded-full ${live ? 'bg-green-400' : 'bg-gray-600'}`} />
          {live ? 'en vivo' : 'sin conexión'}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => setPaused(p => !p)}
            className={`px-3 py-1.5 rounded text-sm font-medium transition ${
              paused ? 'bg-yellow-600 hover:bg-yellow-500' : 'bg-gray-700 hover:bg-gray-600'
            }`}
          >
            {paused ? '▶ Reanudar' : '⏸ Pausar'}
          </button>
          <button
            onClick={() => setLines([])}
            className="px-3 py-1.5 rounded text-sm font-medium bg-gray-700 hover:bg-gray-600 transition"
          >
            Limpiar vista
          </button>
          <button
            onClick={jumpToBottom}
            disabled={autoScroll}
            className="px-3 py-1.5 rounded text-sm font-medium bg-gray-700 hover:bg-gray-600 transition disabled:opacity-40 disabled:cursor-default"
          >
            ↓ Ir al final
          </button>
        </div>
      </div>

      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="bg-gray-950 border border-gray-800 rounded-lg overflow-auto font-mono text-xs leading-relaxed p-3"
        style={{ height: 'calc(100vh - 220px)', minHeight: '300px' }}
      >
        {lines.length === 0 ? (
          <div className="text-gray-600">
            {live ? 'Sin salida todavía. Esperando al bot…' : 'Conectando con el backend…'}
          </div>
        ) : (
          lines.map(ln => (
            <div key={ln.seq} className="whitespace-pre">
              <span className="text-gray-600 select-none">{ln.ts} </span>
              {renderAnsi(ln.text, ln.seq)}
            </div>
          ))
        )}
      </div>

      {!autoScroll && (
        <p className="mt-2 text-xs text-gray-500">
          Auto-scroll en pausa (subiste a leer). Vuelve al fondo o pulsa «Ir al final» para reanudarlo.
        </p>
      )}
    </div>
  )
}
