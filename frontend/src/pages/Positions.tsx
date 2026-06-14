import { BotState, Position } from '../types/bot'
import { useApi } from '../hooks/useApi'

interface PositionsPageProps {
  state: BotState | null
}

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
      <h2 className="text-xl font-bold mb-4">Open Positions</h2>
      <div className="overflow-x-auto">
        <table className="w-full text-sm text-left text-gray-300">
          <thead className="bg-gray-800 text-white">
            <tr>
              <th className="px-6 py-3">Symbol</th>
              <th className="px-6 py-3">Direction</th>
              <th className="px-6 py-3">Volume</th>
              <th className="px-6 py-3">Entry Price</th>
              <th className="px-6 py-3">Current Price</th>
              <th className="px-6 py-3">P&L</th>
              <th className="px-6 py-3">Stop Loss</th>
              <th className="px-6 py-3">Take Profit</th>
              <th className="px-6 py-3">Action</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(positions).map(([key, position]: [string, Position]) => {
              // Coacción defensiva: algún origen del bridge puede mandar números
              // como string; Number() evita que .toFixed reviente el render.
              const num = (v: unknown) => (v == null ? NaN : Number(v))
              // Tolera ambos formatos: alias normalizados (backend al día) y
              // claves crudas del EA (backend viejo / sin _normalize_position):
              //   direction←type, stop_loss←sl, take_profit←tp,
              //   current_price←open_price (fallback si no hay tick).
              const raw = position as unknown as Record<string, unknown>
              const pick = (...keys: string[]) => {
                for (const k of keys) if (raw[k] != null && raw[k] !== '') return raw[k]
                return undefined
              }
              const rawDir = String(pick('direction', 'type') ?? '').toUpperCase()
              const direction = rawDir === '0' ? 'BUY' : rawDir === '1' ? 'SELL' : rawDir
              const profit = num(pick('profit'))
              const volume = num(pick('volume'))
              const openPrice = num(pick('open_price', 'price'))
              const currentPrice = num(pick('current_price', 'open_price', 'price'))
              const sl = num(pick('stop_loss', 'sl'))
              const tp = num(pick('take_profit', 'tp'))
              const pnlColor = profit > 0 ? 'text-green-400' : 'text-red-400'
              const fix = (v: number, d: number) => (Number.isFinite(v) ? v.toFixed(d) : 'N/A')
              return (
                <tr key={key} className="border-b border-gray-700 hover:bg-gray-800">
                  <td className="px-6 py-4 font-semibold">{position.symbol}</td>
                  <td className="px-6 py-4">
                    <span className={direction === 'BUY' ? 'text-green-400 font-semibold' : 'text-red-400 font-semibold'}>
                      {direction || '—'}
                    </span>
                  </td>
                  <td className="px-6 py-4">{fix(volume, 2)}</td>
                  <td className="px-6 py-4">{fix(openPrice, 5)}</td>
                  <td className="px-6 py-4">{fix(currentPrice, 5)}</td>
                  <td className={`px-6 py-4 font-semibold ${pnlColor}`}>
                    ${fix(profit, 2)}
                  </td>
                  <td className="px-6 py-4 text-red-400">{sl ? fix(sl, 5) : 'N/A'}</td>
                  <td className="px-6 py-4 text-green-400">{tp ? fix(tp, 5) : 'N/A'}</td>
                  <td className="px-6 py-4">
                    <button
                      onClick={() => handleClosePosition(position.symbol)}
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
