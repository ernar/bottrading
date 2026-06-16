import { useEffect, useState } from 'react'
import { BotState, Trade } from '../types/bot'
import { getApiUrl, getApiHeaders } from '../config'

interface StoredTrade {
  timestamp: string
  symbol: string
  action: string
  volume: string
  price: string
  stop_loss: string
  take_profit: string
  retcode: string
  order_id: string
  comment: string
}

// Fila del histórico persistido de cierres (tabla closed_trades vía API).
interface ClosedTradeRow {
  symbol: string
  action: string
  entry_price: number | null
  exit_price: number | null
  volume: number | null
  pnl: number | null
  commission: number | null
  open_time: string
  close_time: string
  duration_seconds: number | null
  close_reason: string
  trade_id: string
}

interface ClosedHistory {
  trades: ClosedTradeRow[]
  summary: { total: number; winning: number; pnl: number }
  symbols: string[]
}

interface HistoryPageProps {
  state: BotState | null
}

const EMPTY_HISTORY: ClosedHistory = { trades: [], summary: { total: 0, winning: 0, pnl: 0 }, symbols: [] }

const formatDate = (dateString: string | null) =>
  dateString ? new Date(dateString.replace(' ', 'T')).toLocaleString() : 'N/A'

const formatDuration = (seconds: number | null) => {
  if (!seconds) return 'N/A'
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  return `${hours}h ${minutes}m`
}

const num = (v: number | null, digits: number) => (v === null || v === undefined ? 'N/A' : v.toFixed(digits))

export function HistoryPage({ state }: HistoryPageProps) {
  const [storedTrades, setStoredTrades] = useState<StoredTrade[]>([])
  const [error, setError] = useState<string | null>(null)
  const sessionTrades = state?.closed_trades || []
  const platform = (state?.account_info?.platform || 'mt4').toLowerCase()

  // Filtros del histórico de cierres persistido.
  const [history, setHistory] = useState<ClosedHistory>(EMPTY_HISTORY)
  const [histError, setHistError] = useState<string | null>(null)
  const [symbol, setSymbol] = useState('')
  const [action, setAction] = useState('')
  const [result, setResult] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [limit, setLimit] = useState('200')

  const API_URL = getApiUrl()

  useEffect(() => {
    const url = `${API_URL}/api/db/trades?limit=50&platform=${platform}`
    fetch(url, { headers: getApiHeaders() })
      .then(async r => {
        if (!r.ok) throw new Error(`HTTP ${r.status} en ${url}`)
        const data = await r.json()
        if (!Array.isArray(data)) throw new Error('respuesta inesperada del API (no es una lista)')
        return data
      })
      .then(data => { setStoredTrades(data); setError(null) })
      .catch(e => setError(e instanceof Error ? e.message : String(e)))
  }, [sessionTrades.length, platform])

  // Histórico de cierres persistido: refetch al cambiar filtros, plataforma o al
  // cerrarse un nuevo trade en la sesión (sessionTrades.length).
  useEffect(() => {
    const params = new URLSearchParams({ platform, limit })
    if (symbol) params.set('symbol', symbol)
    if (action) params.set('action', action)
    if (result) params.set('result', result)
    if (from) params.set('from', from)
    if (to) params.set('to', to)
    const url = `${API_URL}/api/db/closed-trades?${params.toString()}`
    fetch(url, { headers: getApiHeaders() })
      .then(async r => {
        if (!r.ok) throw new Error(`HTTP ${r.status} en ${url}`)
        const data = await r.json()
        if (!data || !Array.isArray(data.trades)) throw new Error('respuesta inesperada del API')
        return data as ClosedHistory
      })
      .then(data => { setHistory(data); setHistError(null) })
      .catch(e => setHistError(e instanceof Error ? e.message : String(e)))
  }, [platform, symbol, action, result, from, to, limit, sessionTrades.length])

  const resetFilters = () => {
    setSymbol(''); setAction(''); setResult(''); setFrom(''); setTo(''); setLimit('200')
  }

  const inputCls = 'bg-gray-900 border border-gray-700 rounded px-2 py-1 text-sm text-gray-200 focus:outline-none focus:border-blue-500'

  return (
    <div className="p-4 sm:p-8 space-y-8">
      {sessionTrades.length > 0 && (
        <section>
          <h2 className="text-xl font-bold mb-4">Cierres (sesión)</h2>
          <div className="bg-gray-800 p-4 rounded mb-4 text-sm text-gray-300">
            <div className="grid grid-cols-3 gap-4">
              <div><span className="text-gray-400">Total:</span> {sessionTrades.length}</div>
              <div><span className="text-gray-400">Ganadoras:</span> {sessionTrades.filter((t: Trade) => t.pnl > 0).length}</div>
              <div><span className="text-gray-400">P&L:</span> ${sessionTrades.reduce((s: number, t: Trade) => s + t.pnl, 0).toFixed(2)}</div>
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-left text-gray-300">
              <thead className="bg-gray-800 text-white">
                <tr>
                  <th className="px-4 py-3">Symbol</th>
                  <th className="px-4 py-3">Action</th>
                  <th className="px-4 py-3">Entry</th>
                  <th className="px-4 py-3">Exit</th>
                  <th className="px-4 py-3">Volume</th>
                  <th className="px-4 py-3">P&L</th>
                  <th className="px-4 py-3">Open</th>
                  <th className="px-4 py-3">Close</th>
                  <th className="px-4 py-3">Duration</th>
                </tr>
              </thead>
              <tbody>
                {sessionTrades.map((trade: Trade, i: number) => (
                  <tr key={i} className="border-b border-gray-700 hover:bg-gray-700">
                    <td className="px-4 py-3 font-semibold">{trade.symbol}</td>
                    <td className="px-4 py-3">
                      <span className={trade.action === 'BUY' ? 'text-green-400 font-semibold' : 'text-red-400 font-semibold'}>{trade.action}</span>
                    </td>
                    <td className="px-4 py-3">{trade.entry_price.toFixed(5)}</td>
                    <td className="px-4 py-3">{trade.exit_price?.toFixed(5) || 'N/A'}</td>
                    <td className="px-4 py-3">{trade.volume.toFixed(2)}</td>
                    <td className={`px-4 py-3 font-semibold ${trade.pnl > 0 ? 'text-green-400' : 'text-red-400'}`}>${trade.pnl.toFixed(2)}</td>
                    <td className="px-4 py-3 text-xs">{formatDate(trade.open_time)}</td>
                    <td className="px-4 py-3 text-xs">{trade.close_time ? formatDate(trade.close_time) : 'N/A'}</td>
                    <td className="px-4 py-3 text-xs">{formatDuration(trade.duration_seconds)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section>
        <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
          <h2 className="text-xl font-bold">Histórico de cierres</h2>
          <span className="text-xs text-gray-500">Persistido en la base de datos (sobrevive a reinicios)</span>
        </div>

        {/* Controles de filtro */}
        <div className="bg-gray-800 p-4 rounded mb-4 flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-xs text-gray-400">
            Símbolo
            <select className={inputCls} value={symbol} onChange={e => setSymbol(e.target.value)}>
              <option value="">Todos</option>
              {history.symbols.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-gray-400">
            Dirección
            <select className={inputCls} value={action} onChange={e => setAction(e.target.value)}>
              <option value="">Todas</option>
              <option value="BUY">BUY</option>
              <option value="SELL">SELL</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-gray-400">
            Resultado
            <select className={inputCls} value={result} onChange={e => setResult(e.target.value)}>
              <option value="">Todos</option>
              <option value="win">Ganadoras</option>
              <option value="loss">Perdedoras</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-gray-400">
            Desde
            <input type="date" className={inputCls} value={from} onChange={e => setFrom(e.target.value)} />
          </label>
          <label className="flex flex-col gap-1 text-xs text-gray-400">
            Hasta
            <input type="date" className={inputCls} value={to} onChange={e => setTo(e.target.value)} />
          </label>
          <label className="flex flex-col gap-1 text-xs text-gray-400">
            Límite
            <select className={inputCls} value={limit} onChange={e => setLimit(e.target.value)}>
              <option value="50">50</option>
              <option value="200">200</option>
              <option value="500">500</option>
              <option value="0">Todas</option>
            </select>
          </label>
          <button
            onClick={resetFilters}
            className="px-3 py-1 text-sm rounded border border-gray-600 text-gray-300 hover:bg-gray-700"
          >
            Limpiar
          </button>
        </div>

        {/* Resumen del set filtrado completo */}
        <div className="bg-gray-800 p-4 rounded mb-4 text-sm text-gray-300">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <div><span className="text-gray-400">Total:</span> {history.summary.total}</div>
            <div><span className="text-gray-400">Ganadoras:</span> {history.summary.winning}</div>
            <div>
              <span className="text-gray-400">Win rate:</span>{' '}
              {history.summary.total > 0 ? `${((history.summary.winning / history.summary.total) * 100).toFixed(1)}%` : 'N/A'}
            </div>
            <div>
              <span className="text-gray-400">P&L:</span>{' '}
              <span className={history.summary.pnl >= 0 ? 'text-green-400' : 'text-red-400'}>${history.summary.pnl.toFixed(2)}</span>
            </div>
          </div>
        </div>

        {histError ? (
          <div className="bg-red-950 border border-red-800 text-red-200 p-4 rounded text-sm">
            <div className="font-semibold">No se pudo cargar el histórico de cierres.</div>
            <div className="text-red-300/80 mt-1 break-all">{histError}</div>
            <div className="text-red-300/60 mt-2 text-xs">
              Comprueba que el bot esté corriendo y sirviendo el código actual
              (un proceso viejo en el puerto 5000 puede no tener la ruta <code>/api/db/closed-trades</code>).
            </div>
          </div>
        ) : history.trades.length === 0 ? (
          <div className="bg-gray-800 text-gray-400 p-8 rounded text-center">
            Sin cierres para los filtros seleccionados
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-left text-gray-300">
              <thead className="bg-gray-800 text-white">
                <tr>
                  <th className="px-4 py-3">Symbol</th>
                  <th className="px-4 py-3">Action</th>
                  <th className="px-4 py-3">Entry</th>
                  <th className="px-4 py-3">Exit</th>
                  <th className="px-4 py-3">Volume</th>
                  <th className="px-4 py-3">P&L</th>
                  <th className="px-4 py-3">Open</th>
                  <th className="px-4 py-3">Close</th>
                  <th className="px-4 py-3">Duration</th>
                  <th className="px-4 py-3">Motivo</th>
                </tr>
              </thead>
              <tbody>
                {history.trades.map((t, i) => (
                  <tr key={i} className="border-b border-gray-700 hover:bg-gray-700">
                    <td className="px-4 py-3 font-semibold">{t.symbol}</td>
                    <td className="px-4 py-3">
                      <span className={t.action === 'BUY' ? 'text-green-400 font-semibold' : 'text-red-400 font-semibold'}>{t.action}</span>
                    </td>
                    <td className="px-4 py-3">{num(t.entry_price, 5)}</td>
                    <td className="px-4 py-3">{num(t.exit_price, 5)}</td>
                    <td className="px-4 py-3">{num(t.volume, 2)}</td>
                    <td className={`px-4 py-3 font-semibold ${(t.pnl || 0) > 0 ? 'text-green-400' : 'text-red-400'}`}>${num(t.pnl, 2)}</td>
                    <td className="px-4 py-3 text-xs">{formatDate(t.open_time)}</td>
                    <td className="px-4 py-3 text-xs">{formatDate(t.close_time)}</td>
                    <td className="px-4 py-3 text-xs">{formatDuration(t.duration_seconds)}</td>
                    <td className="px-4 py-3 text-xs text-gray-400">{t.close_reason || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section>
        <h2 className="text-xl font-bold mb-4">Trade Log (last 50)</h2>
        {error ? (
          <div className="bg-red-950 border border-red-800 text-red-200 p-4 rounded text-sm">
            <div className="font-semibold">No se pudo cargar el registro de operaciones.</div>
            <div className="text-red-300/80 mt-1 break-all">{error}</div>
            <div className="text-red-300/60 mt-2 text-xs">
              Comprueba que el bot esté corriendo y sirviendo el código actual
              (un proceso viejo en el puerto 5000 puede no tener la ruta <code>/api/db/trades</code>).
            </div>
          </div>
        ) : storedTrades.length === 0 ? (
          <div className="bg-gray-800 text-gray-400 p-8 rounded text-center">
            No trades yet
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-left text-gray-300">
              <thead className="bg-gray-800 text-white">
                <tr>
                  <th className="px-4 py-3">Time</th>
                  <th className="px-4 py-3">Symbol</th>
                  <th className="px-4 py-3">Action</th>
                  <th className="px-4 py-3">Volume</th>
                  <th className="px-4 py-3">Price</th>
                  <th className="px-4 py-3">SL</th>
                  <th className="px-4 py-3">TP</th>
                  <th className="px-4 py-3">Order ID</th>
                  <th className="px-4 py-3">Comment</th>
                </tr>
              </thead>
              <tbody>
                {[...storedTrades].reverse().map((t, i) => (
                  <tr key={i} className="border-b border-gray-700 hover:bg-gray-800">
                    <td className="px-4 py-3 text-xs text-gray-400 whitespace-nowrap">{t.timestamp}</td>
                    <td className="px-4 py-3 font-semibold">{t.symbol}</td>
                    <td className="px-4 py-3">
                      <span className={t.action === 'BUY' ? 'text-green-400 font-semibold' : 'text-red-400 font-semibold'}>{t.action}</span>
                    </td>
                    <td className="px-4 py-3">{parseFloat(t.volume).toFixed(2)}</td>
                    <td className="px-4 py-3">{t.price}</td>
                    <td className="px-4 py-3 text-red-400">{t.stop_loss || 'N/A'}</td>
                    <td className="px-4 py-3 text-green-400">{t.take_profit || 'N/A'}</td>
                    <td className="px-4 py-3 text-xs text-gray-400">{t.order_id || '—'}</td>
                    <td className="px-4 py-3 text-xs text-gray-400">{t.comment}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
