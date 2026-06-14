import { useEffect, useState } from 'react'
import { BotState, Signal } from '../types/bot'
import { StatusBadge } from '../components/StatusBadge'
import { PortfolioChart, EquityPoint } from '../components/PortfolioChart'
import { getApiUrl, getApiHeaders } from '../config'

const API_URL = getApiUrl()

interface Stats {
  signals_total: number
  signals_today: number
  by_action: { BUY: number; SELL: number; HOLD: number }
  avg_confidence: number | null
  trades_total: number
  memory: { evaluated: number; wins: number; win_rate: number | null }
}

interface DashboardPageProps {
  state: BotState | null
}

function StatCard({ title, value, sub, accent }: { title: string; value: string; sub?: string; accent?: string }) {
  return (
    <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
      <div className="text-gray-400 text-sm">{title}</div>
      <div className={`text-2xl font-bold mt-1 ${accent || 'text-white'}`}>{value}</div>
      {sub && <div className="text-xs text-gray-500 mt-1">{sub}</div>}
    </div>
  )
}

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100)
  const color = pct >= 75 ? 'bg-green-500' : pct >= 60 ? 'bg-yellow-500' : 'bg-red-500'
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 bg-gray-700 rounded-full h-2">
        <div className={`${color} h-2 rounded-full transition-all`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-sm font-semibold w-10 text-right">{pct}%</span>
    </div>
  )
}

function SignalCard({ signal }: { signal: Signal }) {
  const action = signal.action.toUpperCase()
  const actionStyle =
    action === 'BUY' ? 'bg-green-900/60 text-green-300 border-green-700'
    : action === 'SELL' ? 'bg-red-900/60 text-red-300 border-red-700'
    : 'bg-gray-700 text-gray-300 border-gray-600'

  return (
    <div className="bg-gray-800 rounded-lg p-4 border border-gray-700 hover:border-gray-500 transition">
      <div className="flex justify-between items-center mb-3">
        <div className="flex items-center gap-3">
          <span className="text-lg font-bold">{signal.symbol}</span>
          <StatusBadge status={signal.trend as any} />
        </div>
        <span className={`px-3 py-1 rounded font-bold text-sm border ${actionStyle}`}>{action}</span>
      </div>

      <div className="mb-3">
        <div className="text-xs text-gray-400 mb-1">Confianza</div>
        <ConfidenceBar value={signal.confidence} />
      </div>

      {signal.entry > 0 && (
        <div className="grid grid-cols-3 gap-2 text-sm mb-3">
          <div>
            <div className="text-xs text-gray-400">Entry</div>
            <div className="font-semibold">{signal.entry}</div>
          </div>
          <div>
            <div className="text-xs text-gray-400">SL</div>
            <div className="font-semibold text-red-400">{signal.stop_loss || '—'}</div>
          </div>
          <div>
            <div className="text-xs text-gray-400">TP</div>
            <div className="font-semibold text-green-400">{signal.take_profit || '—'}</div>
          </div>
        </div>
      )}

      <p className="text-xs text-gray-400 border-t border-gray-700 pt-2">{signal.reason}</p>
      <div className="text-xs text-gray-600 mt-2">
        {signal.timestamp ? new Date(signal.timestamp).toLocaleTimeString() : ''}
      </div>
    </div>
  )
}

export function DashboardPage({ state }: DashboardPageProps) {
  const [stats, setStats] = useState<Stats | null>(null)
  const [equity, setEquity] = useState<EquityPoint[]>([])
  const liveSignals = Object.values(state?.signals || {})
  const positions = Object.values(state?.positions || {})
  const platform = (state?.account_info?.platform || 'mt4').toLowerCase()
  const liveEquity = state?.account_info?.equity

  useEffect(() => {
    const load = () => {
      fetch(`${API_URL}/api/stats?platform=${platform}`, { headers: getApiHeaders() })
        .then(r => r.json())
        .then(setStats)
        .catch(() => {})
      fetch(`${API_URL}/api/equity?platform=${platform}&limit=500`, { headers: getApiHeaders() })
        .then(r => r.json())
        .then(setEquity)
        .catch(() => {})
    }
    load()
    const interval = setInterval(load, 30000)
    return () => clearInterval(interval)
  }, [platform, liveSignals.length])

  // Punto en vivo: añade el equity actual (WebSocket) como último punto para que
  // el gráfico se mueva sin esperar al próximo refresco/registro del backend.
  const equitySeries: EquityPoint[] =
    liveEquity != null && equity.length > 0 && equity[equity.length - 1].equity !== liveEquity
      ? [...equity, { t: 'now', equity: liveEquity }]
      : equity

  const floatingPnl = positions.reduce((sum, p) => sum + (p.profit || 0), 0)
  const winRate = stats?.memory.win_rate

  return (
    <div className="p-4 sm:p-8 space-y-8">
      <section>
        <h2 className="text-xl font-bold mb-4">Evolución de la cartera</h2>
        <PortfolioChart points={equitySeries} />
      </section>

      <section>
        <h2 className="text-xl font-bold mb-4">Resumen</h2>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
          <StatCard title="Señales hoy" value={String(stats?.signals_today ?? '—')} sub={`${stats?.signals_total ?? 0} en total`} />
          <StatCard
            title="BUY / SELL / HOLD"
            value={stats ? `${stats.by_action.BUY} / ${stats.by_action.SELL} / ${stats.by_action.HOLD}` : '—'}
          />
          <StatCard
            title="Confianza media"
            value={stats?.avg_confidence != null ? `${(stats.avg_confidence * 100).toFixed(0)}%` : '—'}
          />
          <StatCard
            title="Acierto (memoria)"
            value={winRate != null ? `${(winRate * 100).toFixed(0)}%` : '—'}
            sub={stats ? `${stats.memory.wins}/${stats.memory.evaluated} señales evaluadas` : undefined}
            accent={winRate != null ? (winRate >= 0.5 ? 'text-green-400' : 'text-red-400') : undefined}
          />
          <StatCard title="Órdenes ejecutadas" value={String(stats?.trades_total ?? '—')} />
          <StatCard
            title="P/L flotante"
            value={`${floatingPnl >= 0 ? '+' : ''}$${floatingPnl.toFixed(2)}`}
            sub={`${positions.length} posiciones abiertas`}
            accent={floatingPnl > 0 ? 'text-green-400' : floatingPnl < 0 ? 'text-red-400' : undefined}
          />
        </div>
      </section>

      <section>
        <h2 className="text-xl font-bold mb-4">Señales en vivo</h2>
        {liveSignals.length === 0 ? (
          <div className="bg-gray-800 text-gray-400 p-8 rounded-lg text-center border border-gray-700">
            Sin señales en esta sesión todavía. El bot analiza cada 60 segundos.
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {liveSignals.map(signal => (
              <SignalCard key={signal.symbol} signal={signal} />
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
