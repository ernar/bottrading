import { useEffect, useState } from 'react'
import { BotState } from '../types/bot'
import { PortfolioChart } from '../components/PortfolioChart'
import type { EquityPoint } from '../components/PortfolioChart'
import { RiskProfileSelector } from '../components/RiskProfileSelector'
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

// Rangos temporales del gráfico de evolución. `since` en segundos (0 = todo).
// `desc` se muestra junto al cambio para aclarar el periodo.
const EQUITY_RANGES = [
  { key: '1h', label: '1H', since: 3600, desc: 'última hora' },
  { key: '1d', label: '1D', since: 86400, desc: 'último día' },
  { key: '1w', label: '1S', since: 604800, desc: 'última semana' },
  { key: '1m', label: '1M', since: 2592000, desc: 'último mes' },
  { key: 'all', label: 'Todo', since: 0, desc: 'histórico completo' },
] as const

export function DashboardPage({ state }: DashboardPageProps) {
  const [stats, setStats] = useState<Stats | null>(null)
  const [equity, setEquity] = useState<EquityPoint[]>([])
  const [rangeKey, setRangeKey] = useState<string>('1d')
  // Antigüedad (en segundos) del punto de equity más viejo registrado; sirve
  // para atenuar los rangos cuya ventana excede la historia disponible.
  const [spanSeconds, setSpanSeconds] = useState<number>(0)
  const range = EQUITY_RANGES.find(r => r.key === rangeKey) ?? EQUITY_RANGES[4]
  const positions = Object.values(state?.positions || {})
  const platform = (state?.account_info?.platform || 'mt4').toLowerCase()
  const liveEquity = state?.account_info?.equity
  const liveBalance = state?.account_info?.balance

  useEffect(() => {
    const load = () => {
      fetch(`${API_URL}/api/stats?platform=${platform}`, { headers: getApiHeaders() })
        .then(r => r.json())
        .then(setStats)
        .catch(() => {})
      fetch(`${API_URL}/api/equity?platform=${platform}&limit=500&since=${range.since}`, { headers: getApiHeaders() })
        .then(r => r.json())
        .then(setEquity)
        .catch(() => {})
      // Consulta ligera (2 puntos) para conocer el punto más viejo y saber qué
      // rangos tienen historia suficiente (atenuar los que excedan el span).
      fetch(`${API_URL}/api/equity?platform=${platform}&limit=2&since=0`, { headers: getApiHeaders() })
        .then(r => r.json())
        .then((pts: EquityPoint[]) => {
          if (Array.isArray(pts) && pts.length > 0) {
            const oldest = new Date(pts[0].t.replace(' ', 'T')).getTime()
            if (!Number.isNaN(oldest)) {
              setSpanSeconds(Math.max(0, (Date.now() - oldest) / 1000))
            }
          }
        })
        .catch(() => {})
    }
    load()
    const interval = setInterval(load, 30000)
    return () => clearInterval(interval)
  }, [platform, range.since])

  // Punto en vivo: añade el equity actual (WebSocket) como último punto para que
  // el gráfico se mueva sin esperar al próximo refresco/registro del backend.
  // `safeEquity` protege ante respuestas que no sean un array.
  const safeEquity = Array.isArray(equity) ? equity : []
  // El punto en vivo lleva equity Y balance, para que ambos gráficos (equity y
  // balance) se muevan sin esperar al próximo registro del backend.
  const equitySeries: EquityPoint[] =
    liveEquity != null && safeEquity.length > 0 && safeEquity[safeEquity.length - 1].equity !== liveEquity
      ? [...safeEquity, { t: 'now', equity: liveEquity, balance: liveBalance }]
      : safeEquity

  // P/L flotante: misma fuente que el Header (equity - balance) para que ambos
  // coincidan; el array de posiciones puede llegar vacío aunque el equity ya
  // refleje el flotante. Fallback a la suma de posiciones si no hay account_info.
  const account = state?.account_info
  const floatingPnl = account
    ? account.equity - account.balance
    : positions.reduce((sum, p) => sum + (p.profit || 0), 0)
  const winRate = stats?.memory.win_rate

  return (
    <div className="p-4 sm:p-8 space-y-8">
      {/* Perfil de riesgo: visible y editable desde el dashboard (mesa + agentes) */}
      <RiskProfileSelector />

      <section>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xl font-bold">Evolución de la cartera</h2>
          <div className="flex gap-1">
            {EQUITY_RANGES.map(r => {
              // Un rango está "disponible" si hay historia que lo cubra; "Todo"
              // (since=0) siempre lo está. Sin span aún, no atenuamos nada.
              const enough = r.since === 0 || spanSeconds === 0 || spanSeconds >= r.since
              const active = rangeKey === r.key
              return (
                <button
                  key={r.key}
                  onClick={() => enough && setRangeKey(r.key)}
                  disabled={!enough}
                  title={enough ? r.desc : `Sin datos suficientes para ${r.desc}`}
                  className={`px-2.5 py-1 text-xs rounded transition-colors ${
                    active
                      ? 'bg-emerald-500/20 text-emerald-400 font-semibold'
                      : enough
                        ? 'text-gray-400 hover:text-gray-200 hover:bg-gray-800'
                        : 'text-gray-700 cursor-not-allowed'
                  }`}
                >
                  {r.label}
                </button>
              )
            })}
          </div>
        </div>
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          <PortfolioChart points={equitySeries} rangeLabel={range.desc} field="equity" />
          <PortfolioChart points={equitySeries} rangeLabel={range.desc} field="balance" />
        </div>
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
    </div>
  )
}
