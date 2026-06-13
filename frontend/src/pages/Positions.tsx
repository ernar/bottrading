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
      <div className="p-8">
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
            {Object.entries(positions).map(([symbol, position]: [string, Position]) => {
              const pnlColor = position.profit > 0 ? 'text-green-400' : 'text-red-400'
              return (
                <tr key={symbol} className="border-b border-gray-700 hover:bg-gray-800">
                  <td className="px-6 py-4 font-semibold">{symbol}</td>
                  <td className="px-6 py-4">
                    <span className={position.direction === 'BUY' ? 'text-green-400 font-semibold' : 'text-red-400 font-semibold'}>
                      {position.direction}
                    </span>
                  </td>
                  <td className="px-6 py-4">{position.volume.toFixed(2)}</td>
                  <td className="px-6 py-4">{position.open_price.toFixed(5)}</td>
                  <td className="px-6 py-4">{position.current_price.toFixed(5)}</td>
                  <td className={`px-6 py-4 font-semibold ${pnlColor}`}>
                    ${position.profit.toFixed(2)}
                  </td>
                  <td className="px-6 py-4 text-red-400">{position.stop_loss?.toFixed(5) || 'N/A'}</td>
                  <td className="px-6 py-4 text-green-400">{position.take_profit?.toFixed(5) || 'N/A'}</td>
                  <td className="px-6 py-4">
                    <button
                      onClick={() => handleClosePosition(symbol)}
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
