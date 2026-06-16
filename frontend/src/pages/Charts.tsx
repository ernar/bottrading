import { useEffect, useMemo, useState } from 'react'
import type { BotState, Candle, DbSignal, SignalMarker, Position } from '../types/bot'
import { CandlestickChart } from '../components/CandlestickChart'
import { useApi } from '../hooks/useApi'
import { getApiUrl, getApiHeaders } from '../config'

const API_URL = getApiUrl()

interface ChartsPageProps {
  state: BotState | null
}

// Normaliza una fila del histórico de señales (DB) en un marcador pintable.
// El backend devuelve action en MAYÚSCULAS y entry/confidence como string/"".
function toMarker(r: DbSignal): SignalMarker | null {
  const action = (r.action || '').toLowerCase()
  if (action !== 'buy' && action !== 'sell') return null
  const price = Number(r.entry)
  if (!Number.isFinite(price) || price === 0) return null
  const conf = r.confidence !== '' ? Number(r.confidence) : NaN
  return {
    t: r.timestamp,
    action,
    price,
    confidence: Number.isFinite(conf) ? conf : undefined,
    reason: r.reason || undefined,
  }
}

export function ChartsPage({ state }: ChartsPageProps) {
  const { getCandles, getSignalHistory } = useApi()
  const [agentSymbols, setAgentSymbols] = useState<string[]>([])
  const [symbol, setSymbol] = useState<string>('')
  const [candles, setCandles] = useState<Candle[]>([])
  const [markers, setMarkers] = useState<SignalMarker[]>([])
  const [showSignals, setShowSignals] = useState(true)
  const [showPosition, setShowPosition] = useState(true)
  const [loading, setLoading] = useState(false)

  // Lista de símbolos: agentes configurados + lo que haya vivo en el estado.
  const symbols = useMemo(() => {
    const set = new Set<string>(agentSymbols)
    Object.values(state?.signals || {}).forEach(s => set.add(s.symbol))
    Object.values(state?.positions || {}).forEach(p => set.add(p.symbol))
    return Array.from(set).filter(Boolean).sort()
  }, [agentSymbols, state?.signals, state?.positions])

  // Símbolo seleccionado (primero disponible por defecto / si el actual desaparece).
  useEffect(() => {
    if (symbols.length && !symbols.includes(symbol)) setSymbol(symbols[0])
  }, [symbols, symbol])

  // Lista de agentes (una vez): cada agente tiene su símbolo.
  useEffect(() => {
    fetch(`${API_URL}/api/agents`, { headers: getApiHeaders() })
      .then(r => r.json())
      .then(data => {
        const list = (data?.agents || []).map((a: { symbol: string }) => a.symbol)
        setAgentSymbols(Array.isArray(list) ? list : [])
      })
      .catch(() => {})
  }, [])

  // Posición abierta del símbolo (en vivo, desde el estado del WebSocket).
  const position: Position | null = useMemo(() => {
    if (!symbol) return null
    return Object.values(state?.positions || {}).find(p => p.symbol === symbol) || null
  }, [state?.positions, symbol])

  // Marca temporal de la última señal viva del símbolo: dispara el refresco de
  // marcadores cuando llega una señal nueva por WebSocket.
  const liveSignalTs = symbol ? state?.signals?.[symbol]?.timestamp : undefined

  // Carga velas + señales del símbolo; re-fetch periódico (alineado a la rotación).
  useEffect(() => {
    if (!symbol) return
    let alive = true
    const loadCandles = () => {
      setLoading(true)
      getCandles(symbol, 150)
        .then(res => { if (alive) setCandles(Array.isArray(res?.candles) ? res.candles : []) })
        .catch(() => { if (alive) setCandles([]) })
        .finally(() => { if (alive) setLoading(false) })
    }
    const loadSignals = () => {
      getSignalHistory(symbol, 50)
        .then(rows => {
          if (!alive) return
          const ms = (Array.isArray(rows) ? rows : []).map(toMarker).filter(Boolean) as SignalMarker[]
          setMarkers(ms)
        })
        .catch(() => { if (alive) setMarkers([]) })
    }
    loadCandles()
    loadSignals()
    const interval = setInterval(() => { loadCandles(); loadSignals() }, 60000)
    return () => { alive = false; clearInterval(interval) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, liveSignalTs])

  return (
    <div className="p-4 sm:p-8 space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <h2 className="text-xl font-bold">Gráficos</h2>
          <span className="px-2 py-0.5 text-xs rounded bg-gray-700 text-gray-300">H1</span>
          {loading && <span className="text-xs text-gray-500">cargando…</span>}
        </div>
        {/* Toggles de capas */}
        <div className="flex items-center gap-4 text-sm">
          <label className="flex items-center gap-1.5 cursor-pointer select-none">
            <input type="checkbox" checked={showSignals}
              onChange={e => setShowSignals(e.target.checked)} />
            <span className="text-gray-300">Señales</span>
          </label>
          <label className="flex items-center gap-1.5 cursor-pointer select-none">
            <input type="checkbox" checked={showPosition}
              onChange={e => setShowPosition(e.target.checked)} />
            <span className="text-gray-300">Posición</span>
          </label>
        </div>
      </div>

      {/* Selector de símbolo */}
      <div className="flex gap-1 overflow-x-auto whitespace-nowrap">
        {symbols.length === 0 && (
          <span className="text-gray-500 text-sm">No hay símbolos disponibles.</span>
        )}
        {symbols.map(s => (
          <button
            key={s}
            onClick={() => setSymbol(s)}
            className={`px-3 py-1.5 text-sm rounded shrink-0 transition-colors ${
              s === symbol
                ? 'bg-blue-500/20 text-blue-400 font-semibold'
                : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800'
            }`}
          >
            {s}
          </button>
        ))}
      </div>

      {symbol && (
        <CandlestickChart
          candles={candles}
          symbol={symbol}
          signals={markers}
          position={position}
          showSignals={showSignals}
          showPosition={showPosition}
        />
      )}

      <p className="text-xs text-gray-600">
        Velas H1 del bróker. ▲ verde = señal de compra · ▼ rojo = señal de venta (en su
        precio de entrada). Líneas discontinuas: entrada, SL y TP de la posición abierta.
      </p>
    </div>
  )
}
