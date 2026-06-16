import { BotState, Position } from '../types/bot'
import { useApi } from '../hooks/useApi'
import { useTableFilter, FilterSelect, FilterBar, SortHeader, uniqueOptions, Accessors } from '../components/tableFilters'
import { priceDecimals } from '../utils/format'

interface PositionsPageProps {
  state: BotState | null
}

// Fecha de apertura: el backend manda `open_time_str` ("YYYY-MM-DD HH:MM:SS"
// local) y, como respaldo, el epoch crudo del bróker en `open_time` (segundos).
const formatOpenTime = (str?: unknown, epoch?: unknown): string => {
  if (str) return new Date(String(str).replace(' ', 'T')).toLocaleString()
  const n = epoch == null ? NaN : Number(epoch)
  return Number.isFinite(n) && n > 0 ? new Date(n * 1000).toLocaleString() : 'N/A'
}

// Valor temporal (ms) para ordenar la columna Open Time cronológicamente.
const openTimeValue = (str?: unknown, epoch?: unknown): number => {
  if (str) return new Date(String(str).replace(' ', 'T')).getTime()
  const n = epoch == null ? NaN : Number(epoch)
  return Number.isFinite(n) && n > 0 ? n * 1000 : -Infinity
}

// Fila ya normalizada (valores compartidos entre filtros y celdas).
interface PosRow {
  key: string
  symbol: string
  direction: string
  volume: number
  openPrice: number
  currentPrice: number
  profit: number
  sl: number
  tp: number
  openTime: string
  openTimeValue: number
}

const fix = (v: number, d: number) => (Number.isFinite(v) ? v.toFixed(d) : 'N/A')
const thf = 'px-6 py-2'

export function PositionsPage({ state }: PositionsPageProps) {
  const api = useApi()
  const positions = state?.positions || {}

  const handleClosePosition = async (symbol: string) => {
    if (confirm(`Close position for ${symbol}?`)) {
      try {
        await api.closePosition(symbol)
        console.log(`Closed position for ${symbol}`)
      } catch (error) {
        console.error(`Failed to close position for ${symbol}:`, error)
        alert('Failed to close position')
      }
    }
  }

  const handleCloseAll = async () => {
    const count = Object.keys(positions).length
    if (confirm(`Close ALL ${count} open position(s)? This cannot be undone.`)) {
      try {
        const res = await api.closeAllPositions()
        console.log(`Closed ${res?.closed ?? 0} position(s)`, res)
        if (res?.errors?.length) {
          alert(`Closed ${res.closed}, but ${res.errors.length} failed. Check console.`)
        }
      } catch (error) {
        console.error('Failed to close all positions:', error)
        alert('Failed to close all positions')
      }
    }
  }

  // Normaliza cada posición a una fila plana, tolerando alias del bridge.
  const rows: PosRow[] = Object.entries(positions).map(([key, position]: [string, Position]) => {
    const num = (v: unknown) => (v == null ? NaN : Number(v))
    const raw = position as unknown as Record<string, unknown>
    const pick = (...keys: string[]) => {
      for (const k of keys) if (raw[k] != null && raw[k] !== '') return raw[k]
      return undefined
    }
    const rawDir = String(pick('direction', 'type') ?? '').toUpperCase()
    const direction = rawDir === '0' ? 'BUY' : rawDir === '1' ? 'SELL' : rawDir
    return {
      key,
      symbol: position.symbol,
      direction,
      volume: num(pick('volume')),
      openPrice: num(pick('open_price', 'price')),
      currentPrice: num(pick('current_price', 'open_price', 'price')),
      profit: num(pick('profit')),
      sl: num(pick('stop_loss', 'sl')),
      tp: num(pick('take_profit', 'tp')),
      openTime: formatOpenTime(pick('open_time_str'), pick('open_time')),
      openTimeValue: openTimeValue(pick('open_time_str'), pick('open_time')),
    }
  })

  const acc: Accessors<PosRow> = {
    symbol: r => r.symbol, direction: r => r.direction,
  }
  const f = useTableFilter(rows, acc, {
    exact: ['symbol', 'direction'],
    sort: {
      volume: r => r.volume, openPrice: r => r.openPrice, currentPrice: r => r.currentPrice,
      profit: r => r.profit, sl: r => r.sl, tp: r => r.tp, openTime: r => r.openTimeValue,
    },
  })

  if (Object.keys(positions).length === 0) {
    return (
      <div className="p-4 sm:p-8">
        <h2 className="text-xl font-bold mb-4">Open Positions</h2>
        <div className="bg-gray-800 text-gray-400 p-8 rounded text-center">
          No open positions
        </div>
      </div>
    )
  }

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-bold">Open Positions</h2>
        <button
          onClick={handleCloseAll}
          className="px-4 py-2 bg-red-600 hover:bg-red-700 rounded text-white text-sm font-bold"
        >
          CLOSE ALL
        </button>
      </div>
      <FilterBar active={f.active} shown={f.filtered.length} total={rows.length} onClear={f.clear} />
      <div className="overflow-x-auto">
        <table className="w-full text-sm text-left text-gray-300">
          <thead className="bg-gray-800 text-white">
            <tr>
              <th className="px-6 py-3">Symbol</th>
              <th className="px-6 py-3">Direction</th>
              <SortHeader label="Volume" colKey="volume" sortKey={f.sortKey} dir={f.sortDir} onClick={f.toggleSort} className="px-6 py-3" />
              <SortHeader label="Entry Price" colKey="openPrice" sortKey={f.sortKey} dir={f.sortDir} onClick={f.toggleSort} className="px-6 py-3" />
              <SortHeader label="P&L" colKey="profit" sortKey={f.sortKey} dir={f.sortDir} onClick={f.toggleSort} className="px-6 py-3" />
              <SortHeader label="Stop Loss" colKey="sl" sortKey={f.sortKey} dir={f.sortDir} onClick={f.toggleSort} className="px-6 py-3" />
              <SortHeader label="Current Price" colKey="currentPrice" sortKey={f.sortKey} dir={f.sortDir} onClick={f.toggleSort} className="px-6 py-3" />
              <SortHeader label="Take Profit" colKey="tp" sortKey={f.sortKey} dir={f.sortDir} onClick={f.toggleSort} className="px-6 py-3" />
              <SortHeader label="Open Time" colKey="openTime" sortKey={f.sortKey} dir={f.sortDir} onClick={f.toggleSort} className="px-6 py-3" />
              <th className="px-6 py-3">Action</th>
            </tr>
            <tr className="bg-gray-800/60">
              <th className={thf}><FilterSelect value={f.filters.symbol} onChange={v => f.setFilter('symbol', v)} options={uniqueOptions(rows, acc.symbol)} /></th>
              <th className={thf}><FilterSelect value={f.filters.direction} onChange={v => f.setFilter('direction', v)} options={uniqueOptions(rows, acc.direction)} /></th>
              <th className={thf}></th>
              <th className={thf}></th>
              <th className={thf}></th>
              <th className={thf}></th>
              <th className={thf}></th>
              <th className={thf}></th>
              <th className={thf}></th>
              <th className={thf}></th>
            </tr>
          </thead>
          <tbody>
            {f.filtered.map(r => {
              const pnlColor = r.profit > 0 ? 'text-green-400' : 'text-red-400'
              return (
                <tr key={r.key} className="border-b border-gray-700 hover:bg-gray-800">
                  <td className="px-6 py-4 font-semibold">{r.symbol}</td>
                  <td className="px-6 py-4">
                    <span className={r.direction === 'BUY' ? 'text-green-400 font-semibold' : 'text-red-400 font-semibold'}>
                      {r.direction || '—'}
                    </span>
                  </td>
                  <td className="px-6 py-4">{fix(r.volume, 2)}</td>
                  <td className="px-6 py-4">{fix(r.openPrice, priceDecimals(r.symbol))}</td>
                  <td className={`px-6 py-4 font-semibold ${pnlColor}`}>
                    ${fix(r.profit, 2)}
                  </td>
                  <td className="px-6 py-4 text-red-400">{r.sl ? fix(r.sl, priceDecimals(r.symbol)) : 'N/A'}</td>
                  <td className="px-6 py-4">{fix(r.currentPrice, priceDecimals(r.symbol))}</td>
                  <td className="px-6 py-4 text-green-400">{r.tp ? fix(r.tp, priceDecimals(r.symbol)) : 'N/A'}</td>
                  <td className="px-6 py-4 text-xs">{r.openTime}</td>
                  <td className="px-6 py-4">
                    <button
                      onClick={() => handleClosePosition(r.symbol)}
                      className="px-3 py-1 bg-red-600 hover:bg-red-700 rounded text-white text-xs font-semibold"
                    >
                      Close
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
