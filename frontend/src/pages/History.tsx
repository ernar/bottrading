import { useEffect, useState } from 'react'
import { BotState, Trade } from '../types/bot'

interface CsvTrade {
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

interface HistoryPageProps {
  state: BotState | null
}

export function HistoryPage({ state }: HistoryPageProps) {
  const [csvTrades, setCsvTrades] = useState<CsvTrade[]>([])
  const sessionTrades = state?.closed_trades || []
  const platform = (state?.account_info?.platform || 'mt4').toLowerCase()

  const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:5000'

  useEffect(() => {
    fetch(`${API_URL}/api/csv/trades?limit=50&platform=${platform}`)
      .then(r => r.json())
      .then(setCsvTrades)
      .catch(() => {})
  }, [sessionTrades.length, platform])

  const formatDate = (dateString: string) => new Date(dateString).toLocaleString()

  const formatDuration = (seconds: number | null) => {
    if (!seconds) return 'N/A'
    const hours = Math.floor(seconds / 3600)
    const minutes = Math.floor((seconds % 3600) / 60)
    return `${hours}h ${minutes}m`
  }

  return (
    <div className="p-8 space-y-8">
      {sessionTrades.length > 0 && (
        <section>
          <h2 className="text-xl font-bold mb-4">Closed Trades (session)</h2>
          <div className="bg-gray-800 p-4 rounded mb-4 text-sm text-gray-300">
            <div className="grid grid-cols-3 gap-4">
              <div><span className="text-gray-400">Total:</span> {sessionTrades.length}</div>
              <div><span className="text-gray-400">Winning:</span> {sessionTrades.filter((t: Trade) => t.pnl > 0).length}</div>
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
        <h2 className="text-xl font-bold mb-4">Trade Log (last 50 from CSV)</h2>
        {csvTrades.length === 0 ? (
          <div className="bg-gray-800 text-gray-400 p-8 rounded text-center">
            No trades in CSV yet
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
                {[...csvTrades].reverse().map((t, i) => (
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
