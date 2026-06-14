import { useEffect, useState, useCallback } from 'react'
import { Coordination, CoordinatorDecision, CoordinatorOverview } from '../types/bot'
import { getApiUrl, getApiHeaders } from '../config'

const API_URL = getApiUrl()

const pct = (n: number | null | undefined, dp = 1) =>
  n === null || n === undefined ? 'n/a' : `${(n * 100).toFixed(dp)}%`
const money = (n: number | null | undefined) =>
  n === null || n === undefined ? '—' : `$${n.toFixed(2)}`

export function CoordinatorPage({ liveCoordination }: { liveCoordination: Coordination | null }) {
  const [overview, setOverview] = useState<CoordinatorOverview | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    fetch(`${API_URL}/api/coordinator`, { headers: getApiHeaders() })
      .then(r => r.json())
      .then(setOverview)
      .catch(() => {})
  }, [])

  useEffect(() => {
    load()
    const id = setInterval(load, 10000)
    return () => clearInterval(id)
  }, [load])

  const forceDecision = () => {
    setBusy(true)
    fetch(`${API_URL}/api/coordinator/decide`, { method: 'POST', headers: getApiHeaders() })
      .then(r => r.json())
      .then(() => load())
      .catch(() => {})
      .finally(() => setBusy(false))
  }

  if (overview && overview.enabled === false) {
    return (
      <div className="p-4 sm:p-8">
        <div className="bg-gray-800 text-gray-400 p-8 rounded text-center">
          La mesa de dirección está desactivada. Arranca el bot con
          <code className="mx-1">COORDINATOR_ENABLED=true</code>
          y elige el LLM del coordinador para activarla.
        </div>
      </div>
    )
  }

  // La coordinación en vivo (WebSocket) tiene prioridad sobre la última conocida.
  const coordination = liveCoordination || overview?.last_coordination || null
  const snap = coordination?.snapshot
  // Decisión por símbolo, para cruzar el sesgo con la acción de la mesa.
  const decisionBySymbol: Record<string, CoordinatorDecision> = {}
  for (const d of coordination?.decisions || []) decisionBySymbol[d.symbol] = d

  return (
    <div className="p-4 sm:p-8 space-y-8">
      <section>
        <div className="flex flex-wrap items-center justify-between gap-2 mb-1">
          <h2 className="text-xl font-bold">Mesa de dirección</h2>
          <button
            onClick={forceDecision}
            disabled={busy}
            className="px-3 py-2 text-sm rounded bg-blue-600 hover:bg-blue-500 disabled:opacity-50"
          >
            Forzar decisión (dry-run)
          </button>
        </div>
        <p className="text-xs text-gray-500">
          Director LLM: <span className="text-gray-300">{overview?.provider?.toUpperCase()}/{overview?.model}</span>
          {' · '}cierre automático: {overview?.can_close ? 'activado' : 'desactivado'}
          {' · '}cobertura (hedge): {snap?.hedging ? <span className="text-gray-300">disponible</span> : <span className="text-gray-400">no disponible</span>}
          {overview?.last_coordination_at ? ` · última: ${overview.last_coordination_at}` : ' · aún sin coordinar'}
        </p>
        <p className="text-xs text-gray-500">
          Junta horaria: <span className="text-gray-300">{overview?.last_junta_at || 'pendiente'}</span>
          {' · '}último reporte: <span className="text-gray-300">{overview?.last_report_at || 'pendiente'}</span>
        </p>
      </section>

      {/* Resumen de cartera */}
      {snap ? (
        <section className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          <Card label="Equity" value={money(snap.equity)} sub={`Balance ${money(snap.balance)}`} />
          <Card label="Margen libre" value={money(snap.free_margin)} sub={`Usado ${money(snap.used_margin)}`} />
          <ExposureCard
            label="Exposición total"
            value={snap.total_exposure_pct}
            cap={snap.max_total_exposure_pct}
          />
          <Card
            label="P/L del día"
            value={pct(snap.daily_pnl_pct, 2)}
            valueClass={(snap.daily_pnl_pct ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}
            sub={snap.in_cooldown ? '⚠ cooldown activo' : 'sin cooldown'}
            subClass={snap.in_cooldown ? 'text-yellow-400' : 'text-gray-500'}
          />
        </section>
      ) : (
        <div className="bg-gray-800 text-gray-400 p-6 rounded text-center text-sm">
          Sin coordinación todavía. Se generará en el próximo ciclo del bot.
        </div>
      )}

      {/* Asignación / exposición por símbolo */}
      {snap && Object.keys(snap.symbols).length > 0 && (
        <section>
          <h3 className="text-lg font-bold mb-3">Capital por símbolo</h3>
          <div className="space-y-3">
            {Object.entries(snap.symbols).map(([sym, s]) => {
              const d = decisionBySymbol[sym]
              const protectNote = d?.clamp && /reversión|hard-stop/i.test(d.clamp) ? d.clamp : null
              return (
              <div key={sym} className="bg-gray-800 rounded-lg p-4 border border-gray-700">
                <div className="flex justify-between text-sm mb-1">
                  <span className="flex items-center gap-2">
                    <span className="font-mono text-cyan-300">{sym}</span>
                    <NetBadge dir={s.net_direction} value={s.net_exposure_pct} />
                  </span>
                  <span className="text-gray-400">
                    {s.long_positions}L / {s.short_positions}S · P/L {' '}
                    <span className={s.floating_pnl >= 0 ? 'text-green-400' : 'text-red-400'}>
                      {money(s.floating_pnl)}
                    </span>
                  </span>
                </div>
                <AllocationBar used={s.exposure_pct} cap={s.max_allocation_pct} />
                <div className="flex justify-between text-xs text-gray-500 mt-1">
                  <span>Exposición {pct(s.exposure_pct)}</span>
                  <span>Tope {pct(s.max_allocation_pct, 0)} · libre {pct(s.remaining_pct)}</span>
                </div>
                {protectNote && <p className="text-xs text-amber-300 mt-1">⚠ {protectNote}</p>}
              </div>
              )
            })}
          </div>
        </section>
      )}

      {/* Decisiones go/no-go */}
      <section>
        <h3 className="text-lg font-bold mb-1">Decisiones de la mesa</h3>
        {coordination?.rationale && (
          <p className="text-sm text-gray-300 italic mb-3">“{coordination.rationale}”</p>
        )}
        {coordination && coordination.decisions.length > 0 ? (
          <div className="grid gap-3 md:grid-cols-2">
            {coordination.decisions.map((d, i) => (
              <DecisionCard key={`${d.symbol}-${i}`} d={d} />
            ))}
          </div>
        ) : (
          <div className="bg-gray-800 text-gray-400 p-6 rounded text-center text-sm">
            Sin decisiones en la última coordinación.
          </div>
        )}
      </section>
    </div>
  )
}

function Card({
  label, value, sub, valueClass = 'text-white', subClass = 'text-gray-500',
}: {
  label: string; value: string; sub?: string; valueClass?: string; subClass?: string
}) {
  return (
    <div className="bg-gray-800 rounded-lg p-5 border border-gray-700">
      <div className="text-xs text-gray-400">{label}</div>
      <div className={`text-2xl font-bold mt-1 ${valueClass}`}>{value}</div>
      {sub && <div className={`text-xs mt-1 ${subClass}`}>{sub}</div>}
    </div>
  )
}

function ExposureCard({ label, value, cap }: { label: string; value: number; cap: number }) {
  const ratio = cap > 0 ? value / cap : 0
  const color = ratio >= 0.9 ? 'bg-red-500' : ratio >= 0.6 ? 'bg-yellow-500' : 'bg-green-500'
  return (
    <div className="bg-gray-800 rounded-lg p-5 border border-gray-700">
      <div className="text-xs text-gray-400">{label}</div>
      <div className="text-2xl font-bold mt-1">{pct(value)}</div>
      <div className="w-full bg-gray-900 rounded h-2 mt-2 overflow-hidden">
        <div className={`h-2 ${color}`} style={{ width: `${Math.min(100, ratio * 100)}%` }} />
      </div>
      <div className="text-xs text-gray-500 mt-1">Tope {pct(cap, 0)}</div>
    </div>
  )
}

function AllocationBar({ used, cap }: { used: number; cap: number }) {
  const ratio = cap > 0 ? used / cap : 0
  const color = ratio >= 1 ? 'bg-red-500' : ratio >= 0.7 ? 'bg-yellow-500' : 'bg-cyan-500'
  return (
    <div className="w-full bg-gray-900 rounded h-2 overflow-hidden">
      <div className={`h-2 ${color}`} style={{ width: `${Math.min(100, ratio * 100)}%` }} />
    </div>
  )
}

function NetBadge({ dir, value }: { dir: 'LONG' | 'SHORT' | 'FLAT'; value: number }) {
  const styles: Record<string, string> = {
    LONG: 'bg-green-900 text-green-200',
    SHORT: 'bg-red-900 text-red-200',
    FLAT: 'bg-gray-700 text-gray-400',
  }
  return (
    <span className={`text-[10px] px-2 py-0.5 rounded ${styles[dir] || styles.FLAT}`}>
      neto {dir}{dir !== 'FLAT' ? ` ${pct(value)}` : ''}
    </span>
  )
}

function DecisionCard({ d }: { d: CoordinatorDecision }) {
  const actionColors: Record<string, string> = {
    close: 'bg-red-900 text-red-200',
    reduce: 'bg-yellow-900 text-yellow-200',
    hedge: 'bg-indigo-900 text-indigo-200',
    hold: 'bg-gray-700 text-gray-300',
  }
  return (
    <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
      <div className="flex items-center justify-between">
        <span className="font-mono text-cyan-300">{d.symbol}</span>
        <span className={`text-xs px-2 py-0.5 rounded ${d.approve ? 'bg-green-900 text-green-200' : 'bg-red-900 text-red-200'}`}>
          {d.approve ? 'APROBADA' : 'vetada'}
        </span>
      </div>
      <div className="flex flex-wrap gap-2 mt-2 text-xs">
        <span className="px-2 py-0.5 rounded bg-gray-900 text-gray-300">prioridad {d.priority}</span>
        <span className="px-2 py-0.5 rounded bg-gray-900 text-gray-300">asignación {pct(d.allocation_pct, 0)}</span>
        <span className={`px-2 py-0.5 rounded ${actionColors[d.position_action] || 'bg-gray-700 text-gray-300'}`}>
          posiciones: {d.position_action}{d.manage_direction ? ` (${d.manage_direction})` : ''}
        </span>
      </div>
      {d.reason && <p className="text-xs text-gray-400 mt-2">{d.reason}</p>}
      {d.clamp && <p className="text-xs text-yellow-300 mt-1">⚠ {d.clamp}</p>}
    </div>
  )
}
