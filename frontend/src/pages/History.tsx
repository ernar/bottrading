import { useEffect, useState } from 'react'
import { BotState, Trade } from '../types/bot'
import { getApiUrl, getApiHeaders } from '../config'
import { useTableFilter, FilterText, FilterSelect, FilterBar, SortHeader, uniqueOptions, Accessors } from '../components/tableFilters'
import { priceDecimals } from '../utils/format'

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

interface HistoryPageProps {
  state: BotState | null
}

const formatDate = (dateString: string | null) =>
  dateString ? new Date(dateString.replace(' ', 'T')).toLocaleString() : 'N/A'

const dateValue = (dateString: string | null) =>
  dateString ? new Date(dateString.replace(' ', 'T')).getTime() : -Infinity

const formatDuration = (seconds: number | null) => {
  if (!seconds) return 'N/A'
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  return `${hours}h ${minutes}m`
}

const num = (v: number | null, digits: number) => (v === null || v === undefined ? 'N/A' : v.toFixed(digits))
const nz = (v: number | null) => (v == null ? -Infinity : v)  // null al fondo al ordenar
const thf = 'px-4 py-2'  // celda de la fila de filtros

// Escala del ahorro del IRPF (España). Tramos marginales sobre la ganancia neta.
const SAVINGS_BRACKETS: [number, number][] = [
  [6000, 0.19], [50000, 0.21], [200000, 0.23], [300000, 0.27], [Infinity, 0.28],
]
function spanishSavingsTax(gain: number): number {
  if (gain <= 0) return 0
  let tax = 0, prev = 0
  for (const [upto, rate] of SAVINGS_BRACKETS) {
    if (gain <= prev) break
    tax += (Math.min(gain, upto) - prev) * rate
    prev = upto
  }
  return tax
}

type Tab = 'cierres' | 'trades'

export function HistoryPage({ state }: HistoryPageProps) {
  const [tab, setTab] = useState<Tab>('cierres')
  const [storedTrades, setStoredTrades] = useState<StoredTrade[]>([])
  const [error, setError] = useState<string | null>(null)
  const sessionTrades = state?.closed_trades || []
  const platform = (state?.account_info?.platform || 'mt4').toLowerCase()

  // Histórico persistido + controles de servidor (rango de fechas + límite).
  const [closedTrades, setClosedTrades] = useState<ClosedTradeRow[]>([])
  const [histError, setHistError] = useState<string | null>(null)
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [applied, setApplied] = useState({ from: '', to: '' })  // fechas confirmadas con "Aplicar"
  const [limit, setLimit] = useState('200')
  const pendingDates = from !== applied.from || to !== applied.to

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

  // Histórico de cierres: el servidor acota por fecha (aplicada) + límite; el
  // resto del filtrado/orden se hace en las cabeceras de la tabla (cliente).
  useEffect(() => {
    const params = new URLSearchParams({ platform, limit })
    if (applied.from) params.set('from', applied.from)
    if (applied.to) params.set('to', applied.to)
    const url = `${API_URL}/api/db/closed-trades?${params.toString()}`
    fetch(url, { headers: getApiHeaders() })
      .then(async r => {
        if (!r.ok) throw new Error(`HTTP ${r.status} en ${url}`)
        const data = await r.json()
        if (!data || !Array.isArray(data.trades)) throw new Error('respuesta inesperada del API')
        return data.trades as ClosedTradeRow[]
      })
      .then(data => { setClosedTrades(data); setHistError(null) })
      .catch(e => setHistError(e instanceof Error ? e.message : String(e)))
  }, [platform, applied.from, applied.to, limit, sessionTrades.length])

  // --- Filtros + orden de cabecera (hooks siempre llamados, sin importar la pestaña) ---
  const sessAcc: Accessors<Trade> = {
    symbol: t => t.symbol, action: t => t.action,
    pnl: t => (t.pnl > 0 ? 'Ganadora' : 'Perdedora'),
  }
  const sess = useTableFilter(sessionTrades, sessAcc, {
    exact: ['symbol', 'action', 'pnl'],
    sort: {
      entry: t => t.entry_price, exit: t => nz(t.exit_price), volume: t => t.volume,
      pnl: t => t.pnl, open: t => dateValue(t.open_time), close: t => dateValue(t.close_time),
      duration: t => nz(t.duration_seconds),
    },
  })

  const closedAcc: Accessors<ClosedTradeRow> = {
    symbol: t => t.symbol, action: t => t.action,
    result: t => ((t.pnl ?? 0) > 0 ? 'Ganadora' : 'Perdedora'),
    reason: t => t.close_reason || '—',
  }
  const closed = useTableFilter(closedTrades, closedAcc, {
    exact: ['symbol', 'action', 'result', 'reason'],
    sort: {
      entry: t => nz(t.entry_price), exit: t => nz(t.exit_price), volume: t => nz(t.volume),
      pnl: t => nz(t.pnl), open: t => dateValue(t.open_time), close: t => dateValue(t.close_time),
      duration: t => nz(t.duration_seconds),
    },
  })

  const logAcc: Accessors<StoredTrade> = {
    symbol: t => t.symbol, action: t => t.action, order_id: t => t.order_id, comment: t => t.comment,
  }
  const reversedLog = [...storedTrades].reverse()
  const log = useTableFilter(reversedLog, logAcc, {
    exact: ['symbol', 'action'],
    sort: {
      timestamp: t => dateValue(t.timestamp), volume: t => parseFloat(t.volume), price: t => parseFloat(t.price),
      stop_loss: t => parseFloat(t.stop_loss), take_profit: t => parseFloat(t.take_profit),
    },
  })

  // --- Panel fiscal (sobre las filas visibles del histórico de cierres) ---
  const grossPnl = closed.filtered.reduce((s, t) => s + (t.pnl || 0), 0)
  const winning = closed.filtered.filter(t => (t.pnl ?? 0) > 0).length
  const balance = state?.account_info?.balance || 0
  const initialBalance = balance - grossPnl
  const grossPct = initialBalance > 0 ? (grossPnl / initialBalance) * 100 : null
  const tax = spanishSavingsTax(grossPnl)
  const net = grossPnl - tax

  const tabBtn = (key: Tab, label: string) => (
    <button
      onClick={() => setTab(key)}
      className={`px-4 py-2 text-sm rounded-full border transition-colors ${
        tab === key ? 'bg-blue-600 border-blue-500 text-white' : 'bg-gray-800 border-gray-700 text-gray-300 hover:bg-gray-700'
      }`}
    >
      {label}
    </button>
  )

  return (
    <div className="p-4 sm:p-8 space-y-6">
      <div className="flex items-center gap-2">
        {tabBtn('cierres', 'Cierres')}
        {tabBtn('trades', 'Histórico de trades')}
      </div>

      {tab === 'cierres' && (
        <div className="space-y-8">
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
              <FilterBar active={sess.active} shown={sess.filtered.length} total={sessionTrades.length} onClear={sess.clear} />
              <div className="overflow-x-auto">
                <table className="w-full text-sm text-left text-gray-300">
                  <thead className="bg-gray-800 text-white">
                    <tr>
                      <th className="px-4 py-3">Symbol</th>
                      <th className="px-4 py-3">Action</th>
                      <SortHeader label="Entry" colKey="entry" sortKey={sess.sortKey} dir={sess.sortDir} onClick={sess.toggleSort} />
                      <SortHeader label="Exit" colKey="exit" sortKey={sess.sortKey} dir={sess.sortDir} onClick={sess.toggleSort} />
                      <SortHeader label="Volume" colKey="volume" sortKey={sess.sortKey} dir={sess.sortDir} onClick={sess.toggleSort} />
                      <SortHeader label="P&L" colKey="pnl" sortKey={sess.sortKey} dir={sess.sortDir} onClick={sess.toggleSort} />
                      <SortHeader label="Open" colKey="open" sortKey={sess.sortKey} dir={sess.sortDir} onClick={sess.toggleSort} />
                      <SortHeader label="Close" colKey="close" sortKey={sess.sortKey} dir={sess.sortDir} onClick={sess.toggleSort} />
                      <SortHeader label="Duration" colKey="duration" sortKey={sess.sortKey} dir={sess.sortDir} onClick={sess.toggleSort} />
                    </tr>
                    <tr className="bg-gray-800/60">
                      <th className={thf}><FilterSelect value={sess.filters.symbol} onChange={v => sess.setFilter('symbol', v)} options={uniqueOptions(sessionTrades, sessAcc.symbol)} /></th>
                      <th className={thf}><FilterSelect value={sess.filters.action} onChange={v => sess.setFilter('action', v)} options={uniqueOptions(sessionTrades, sessAcc.action)} /></th>
                      <th className={thf}></th>
                      <th className={thf}></th>
                      <th className={thf}></th>
                      <th className={thf}><FilterSelect value={sess.filters.pnl} onChange={v => sess.setFilter('pnl', v)} options={['Ganadora', 'Perdedora']} /></th>
                      <th className={thf}></th>
                      <th className={thf}></th>
                      <th className={thf}></th>
                    </tr>
                  </thead>
                  <tbody>
                    {sess.filtered.map((trade: Trade, i: number) => (
                      <tr key={i} className="border-b border-gray-700 hover:bg-gray-700">
                        <td className="px-4 py-3 font-semibold">{trade.symbol}</td>
                        <td className="px-4 py-3">
                          <span className={trade.action === 'BUY' ? 'text-green-400 font-semibold' : 'text-red-400 font-semibold'}>{trade.action}</span>
                        </td>
                        <td className="px-4 py-3">{trade.entry_price.toFixed(priceDecimals(trade.symbol))}</td>
                        <td className="px-4 py-3">{trade.exit_price?.toFixed(priceDecimals(trade.symbol)) || 'N/A'}</td>
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

            {/* Controles de servidor: rango de fechas (con Aplicar) + límite */}
            <div className="bg-gray-800 p-4 rounded mb-4 flex flex-wrap items-end gap-3">
              <label className="flex flex-col gap-1 text-xs text-gray-400">
                Desde
                <input type="date" className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-sm text-gray-200 focus:outline-none focus:border-blue-500" value={from} onChange={e => setFrom(e.target.value)} />
              </label>
              <label className="flex flex-col gap-1 text-xs text-gray-400">
                Hasta
                <input type="date" className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-sm text-gray-200 focus:outline-none focus:border-blue-500" value={to} onChange={e => setTo(e.target.value)} />
              </label>
              <button
                onClick={() => setApplied({ from, to })}
                disabled={!pendingDates}
                className={`px-3 py-1.5 text-sm rounded ${pendingDates ? 'bg-blue-600 hover:bg-blue-500' : 'bg-gray-700 opacity-50 cursor-default'}`}
              >
                Aplicar
              </button>
              {(applied.from || applied.to) && (
                <button
                  onClick={() => { setFrom(''); setTo(''); setApplied({ from: '', to: '' }) }}
                  className="px-3 py-1.5 text-sm rounded border border-gray-600 hover:bg-gray-700"
                >
                  Quitar fechas
                </button>
              )}
              <label className="flex flex-col gap-1 text-xs text-gray-400">
                Límite
                <select className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-sm text-gray-200 focus:outline-none focus:border-blue-500" value={limit} onChange={e => setLimit(e.target.value)}>
                  <option value="50">50</option>
                  <option value="200">200</option>
                  <option value="500">500</option>
                  <option value="0">Todas</option>
                </select>
              </label>
              <span className="text-xs text-gray-500 self-end pb-1">El resto de filtros están en las cabeceras de la tabla ↓</span>
            </div>

            {/* Panel: rendimiento bruto, fiscalidad estimada (España) y neto */}
            <div className="bg-gray-800 p-4 rounded mb-4">
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4 text-sm">
                <div>
                  <div className="text-gray-400 text-xs">Operaciones</div>
                  <div className="text-gray-100 font-semibold">{closed.filtered.length}</div>
                </div>
                <div>
                  <div className="text-gray-400 text-xs">Win rate</div>
                  <div className="text-gray-100 font-semibold">
                    {closed.filtered.length > 0 ? `${((winning / closed.filtered.length) * 100).toFixed(1)}%` : 'N/A'}
                  </div>
                </div>
                <div>
                  <div className="text-gray-400 text-xs">Beneficio bruto</div>
                  <div className={`font-semibold ${grossPnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>${grossPnl.toFixed(2)}</div>
                </div>
                <div>
                  <div className="text-gray-400 text-xs">Incremento bruto</div>
                  <div className={`font-semibold ${grossPnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {grossPct === null ? 'N/A' : `${grossPct >= 0 ? '+' : ''}${grossPct.toFixed(2)}%`}
                  </div>
                </div>
                <div>
                  <div className="text-gray-400 text-xs">Impuestos (est.)</div>
                  <div className="text-amber-300 font-semibold">−${tax.toFixed(2)}</div>
                </div>
                <div>
                  <div className="text-gray-400 text-xs">Neto estimado</div>
                  <div className={`font-semibold ${net >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>${net.toFixed(2)}</div>
                </div>
              </div>
              <p className="text-[11px] text-gray-500 mt-3">
                Estimación orientativa sobre las operaciones visibles. Impuestos según la escala del ahorro del IRPF
                (19% hasta 6.000 € · 21% 6.000–50.000 € · 23% 50.000–200.000 € · 27% 200.000–300.000 € · 28% &gt;300.000 €).
                El «incremento bruto» se calcula sobre el balance previo estimado (balance actual − beneficio bruto).
                No es asesoramiento fiscal; no incluye comisiones, pérdidas compensables de otros ejercicios ni mínimos.
              </p>
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
            ) : closedTrades.length === 0 ? (
              <div className="bg-gray-800 text-gray-400 p-8 rounded text-center">
                Sin cierres para el rango seleccionado
              </div>
            ) : (
              <>
                <FilterBar active={closed.active} shown={closed.filtered.length} total={closedTrades.length} onClear={closed.clear} />
                <div className="overflow-x-auto">
                  <table className="w-full text-sm text-left text-gray-300">
                    <thead className="bg-gray-800 text-white">
                      <tr>
                        <th className="px-4 py-3">Symbol</th>
                        <th className="px-4 py-3">Action</th>
                        <SortHeader label="Entry" colKey="entry" sortKey={closed.sortKey} dir={closed.sortDir} onClick={closed.toggleSort} />
                        <SortHeader label="Exit" colKey="exit" sortKey={closed.sortKey} dir={closed.sortDir} onClick={closed.toggleSort} />
                        <SortHeader label="Volume" colKey="volume" sortKey={closed.sortKey} dir={closed.sortDir} onClick={closed.toggleSort} />
                        <SortHeader label="P&L" colKey="pnl" sortKey={closed.sortKey} dir={closed.sortDir} onClick={closed.toggleSort} />
                        <SortHeader label="Open" colKey="open" sortKey={closed.sortKey} dir={closed.sortDir} onClick={closed.toggleSort} />
                        <SortHeader label="Close" colKey="close" sortKey={closed.sortKey} dir={closed.sortDir} onClick={closed.toggleSort} />
                        <SortHeader label="Duration" colKey="duration" sortKey={closed.sortKey} dir={closed.sortDir} onClick={closed.toggleSort} />
                        <th className="px-4 py-3">Motivo</th>
                      </tr>
                      <tr className="bg-gray-800/60">
                        <th className={thf}><FilterSelect value={closed.filters.symbol} onChange={v => closed.setFilter('symbol', v)} options={uniqueOptions(closedTrades, closedAcc.symbol)} /></th>
                        <th className={thf}><FilterSelect value={closed.filters.action} onChange={v => closed.setFilter('action', v)} options={uniqueOptions(closedTrades, closedAcc.action)} /></th>
                        <th className={thf}></th>
                        <th className={thf}></th>
                        <th className={thf}></th>
                        <th className={thf}><FilterSelect value={closed.filters.result} onChange={v => closed.setFilter('result', v)} options={['Ganadora', 'Perdedora']} /></th>
                        <th className={thf}></th>
                        <th className={thf}></th>
                        <th className={thf}></th>
                        <th className={thf}><FilterSelect value={closed.filters.reason} onChange={v => closed.setFilter('reason', v)} options={uniqueOptions(closedTrades, closedAcc.reason)} /></th>
                      </tr>
                    </thead>
                    <tbody>
                      {closed.filtered.map((t, i) => (
                        <tr key={i} className="border-b border-gray-700 hover:bg-gray-700">
                          <td className="px-4 py-3 font-semibold">{t.symbol}</td>
                          <td className="px-4 py-3">
                            <span className={t.action === 'BUY' ? 'text-green-400 font-semibold' : 'text-red-400 font-semibold'}>{t.action}</span>
                          </td>
                          <td className="px-4 py-3">{num(t.entry_price, priceDecimals(t.symbol))}</td>
                          <td className="px-4 py-3">{num(t.exit_price, priceDecimals(t.symbol))}</td>
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
              </>
            )}
          </section>
        </div>
      )}

      {tab === 'trades' && (
        <section>
          <h2 className="text-xl font-bold mb-4">Histórico de trades (últimas 50 órdenes)</h2>
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
            <>
              <FilterBar active={log.active} shown={log.filtered.length} total={storedTrades.length} onClear={log.clear} />
              <div className="overflow-x-auto">
                <table className="w-full text-sm text-left text-gray-300">
                  <thead className="bg-gray-800 text-white">
                    <tr>
                      <SortHeader label="Time" colKey="timestamp" sortKey={log.sortKey} dir={log.sortDir} onClick={log.toggleSort} />
                      <th className="px-4 py-3">Symbol</th>
                      <th className="px-4 py-3">Action</th>
                      <SortHeader label="Volume" colKey="volume" sortKey={log.sortKey} dir={log.sortDir} onClick={log.toggleSort} />
                      <SortHeader label="Price" colKey="price" sortKey={log.sortKey} dir={log.sortDir} onClick={log.toggleSort} />
                      <SortHeader label="SL" colKey="stop_loss" sortKey={log.sortKey} dir={log.sortDir} onClick={log.toggleSort} />
                      <SortHeader label="TP" colKey="take_profit" sortKey={log.sortKey} dir={log.sortDir} onClick={log.toggleSort} />
                      <th className="px-4 py-3">Order ID</th>
                      <th className="px-4 py-3">Comment</th>
                    </tr>
                    <tr className="bg-gray-800/60">
                      <th className={thf}></th>
                      <th className={thf}><FilterSelect value={log.filters.symbol} onChange={v => log.setFilter('symbol', v)} options={uniqueOptions(reversedLog, logAcc.symbol)} /></th>
                      <th className={thf}><FilterSelect value={log.filters.action} onChange={v => log.setFilter('action', v)} options={uniqueOptions(reversedLog, logAcc.action)} /></th>
                      <th className={thf}></th>
                      <th className={thf}></th>
                      <th className={thf}></th>
                      <th className={thf}></th>
                      <th className={thf}><FilterText value={log.filters.order_id} onChange={v => log.setFilter('order_id', v)} /></th>
                      <th className={thf}><FilterText value={log.filters.comment} onChange={v => log.setFilter('comment', v)} /></th>
                    </tr>
                  </thead>
                  <tbody>
                    {log.filtered.map((t, i) => (
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
            </>
          )}
        </section>
      )}
    </div>
  )
}
