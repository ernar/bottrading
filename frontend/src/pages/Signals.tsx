import { useEffect, useState } from 'react'
import { BotState, Signal } from '../types/bot'
import { StatusBadge } from '../components/StatusBadge'

interface CsvSignal {
  timestamp: string
  agent: string
  symbol: string
  action: string
  confidence: string
  trend: string
  risk_level: string
  entry: string
  stop_loss: string
  take_profit: string
  reason: string
}

interface SignalsPageProps {
  state: BotState | null
}

export function SignalsPage({ state }: SignalsPageProps) {
  const [csvSignals, setCsvSignals] = useState<CsvSignal[]>([])
  const liveSignals = Object.values(state?.signals || {})
  const platform = (state?.account_info?.platform || 'mt4').toLowerCase()

  const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:5000'

  useEffect(() => {
    fetch(`${API_URL}/api/csv/signals?limit=15&platform=${platform}`)
      .then(r => r.json())
      .then(setCsvSignals)
      .catch(() => {})
  }, [liveSignals.length, platform])

  const riskColor = (risk: string) => {
    if (risk === 'low') return 'bg-green-900 text-green-200'
    if (risk === 'high') return 'bg-red-900 text-red-200'
    return 'bg-yellow-900 text-yellow-200'
  }

  return (
    <div className="p-8 space-y-8">
      {liveSignals.length > 0 && (
        <section>
          <h2 className="text-xl font-bold mb-4">Live Signals (session)</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-left text-gray-300">
              <thead className="bg-gray-800 text-white">
                <tr>
                  <th className="px-4 py-3">Agent</th>
                  <th className="px-4 py-3">Symbol</th>
                  <th className="px-4 py-3">Trend</th>
                  <th className="px-4 py-3">Action</th>
                  <th className="px-4 py-3">Confidence</th>
                  <th className="px-4 py-3">Entry</th>
                  <th className="px-4 py-3">SL</th>
                  <th className="px-4 py-3">TP</th>
                  <th className="px-4 py-3">Risk</th>
                  <th className="px-4 py-3">Reason</th>
                </tr>
              </thead>
              <tbody>
                {liveSignals.map((signal: Signal) => (
                  <tr key={signal.symbol} className="border-b border-gray-700 hover:bg-gray-800">
                    <td className="px-4 py-3 text-xs text-cyan-300">{signal.agent || '—'}</td>
                    <td className="px-4 py-3 font-semibold">{signal.symbol}</td>
                    <td className="px-4 py-3"><StatusBadge status={signal.trend as any} /></td>
                    <td className="px-4 py-3 font-semibold">{signal.action.toUpperCase()}</td>
                    <td className="px-4 py-3">{(signal.confidence * 100).toFixed(0)}%</td>
                    <td className="px-4 py-3">{signal.entry || 'N/A'}</td>
                    <td className="px-4 py-3 text-red-400">{signal.stop_loss || 'N/A'}</td>
                    <td className="px-4 py-3 text-green-400">{signal.take_profit || 'N/A'}</td>
                    <td className="px-4 py-3">
                      <span className={`px-2 py-1 rounded text-xs ${riskColor(signal.risk_level)}`}>
                        {signal.risk_level.toUpperCase()}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-400">{signal.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section>
        <h2 className="text-xl font-bold mb-4">Signal History (last 15 from CSV)</h2>
        {csvSignals.length === 0 ? (
          <div className="bg-gray-800 text-gray-400 p-8 rounded text-center">
            No signals in CSV yet
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-left text-gray-300">
              <thead className="bg-gray-800 text-white">
                <tr>
                  <th className="px-4 py-3">Time</th>
                  <th className="px-4 py-3">Agent</th>
                  <th className="px-4 py-3">Symbol</th>
                  <th className="px-4 py-3">Trend</th>
                  <th className="px-4 py-3">Action</th>
                  <th className="px-4 py-3">Conf.</th>
                  <th className="px-4 py-3">Entry</th>
                  <th className="px-4 py-3">SL</th>
                  <th className="px-4 py-3">TP</th>
                  <th className="px-4 py-3">Risk</th>
                  <th className="px-4 py-3">Reason</th>
                </tr>
              </thead>
              <tbody>
                {[...csvSignals].reverse().map((s, i) => (
                  <tr key={i} className="border-b border-gray-700 hover:bg-gray-800">
                    <td className="px-4 py-3 text-xs text-gray-400 whitespace-nowrap">{s.timestamp}</td>
                    <td className="px-4 py-3 text-xs text-cyan-300">{s.agent || '—'}</td>
                    <td className="px-4 py-3 font-semibold">{s.symbol}</td>
                    <td className="px-4 py-3"><StatusBadge status={s.trend as any} /></td>
                    <td className="px-4 py-3 font-semibold">{s.action}</td>
                    <td className="px-4 py-3">{(parseFloat(s.confidence) * 100).toFixed(0)}%</td>
                    <td className="px-4 py-3">{s.entry || 'N/A'}</td>
                    <td className="px-4 py-3 text-red-400">{s.stop_loss || 'N/A'}</td>
                    <td className="px-4 py-3 text-green-400">{s.take_profit || 'N/A'}</td>
                    <td className="px-4 py-3">
                      <span className={`px-2 py-1 rounded text-xs ${riskColor(s.risk_level)}`}>
                        {s.risk_level.toUpperCase()}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-400">{s.reason}</td>
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
