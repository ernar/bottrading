import { useEffect, useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { Coordination, CoordinatorDecision, CoordinatorOverview } from '../types/bot'
import { getApiUrl, getApiHeaders } from '../config'

const API_URL = getApiUrl()

const pct = (n: number | null | undefined, dp = 0) =>
  n === null || n === undefined ? 'n/a' : `${(n * 100).toFixed(dp)}%`

const actionColors: Record<string, string> = {
  close: 'bg-red-900 text-red-200',
  reduce: 'bg-yellow-900 text-yellow-200',
  hedge: 'bg-indigo-900 text-indigo-200',
  hold: 'bg-gray-700 text-gray-300',
}

// Resumen compacto de la última decisión de la mesa de dirección, para mostrarlo
// fuera de la página "Mesa" (p. ej. encima de Open Positions). Usa la
// coordinación en vivo (WebSocket) si se pasa; si no, la última conocida del API.
export function LastDeskDecision({ live }: { live?: Coordination | null }) {
  const [overview, setOverview] = useState<CoordinatorOverview | null>(null)

  const load = useCallback(() => {
    fetch(`${API_URL}/api/coordinator`, { headers: getApiHeaders() })
      .then(r => r.json())
      .then(setOverview)
      .catch(() => {})
  }, [])

  useEffect(() => {
    load()
    const id = setInterval(load, 15000)
    return () => clearInterval(id)
  }, [load])

  // La mesa desactivada no aporta nada aquí.
  if (overview && overview.enabled === false) return null

  const coordination = live || overview?.last_coordination || null
  const when = overview?.last_coordination_at

  if (!coordination || coordination.decisions.length === 0) {
    return (
      <div className="bg-gray-800 border border-gray-700 rounded-lg p-3 text-sm text-gray-400 flex items-center justify-between gap-2">
        <span>Mesa de dirección: aún sin decisiones.</span>
        <Link to="/coordinator" className="text-xs text-cyan-300 hover:underline whitespace-nowrap">Ver mesa →</Link>
      </div>
    )
  }

  return (
    <div className="bg-gray-800 border border-gray-700 rounded-lg p-4">
      <div className="flex items-center justify-between gap-2 flex-wrap mb-2">
        <h3 className="text-sm font-bold text-emerald-300">
          Última decisión de la mesa
          {when && <span className="text-xs text-gray-500 font-normal ml-2">{when}</span>}
        </h3>
        <Link to="/coordinator" className="text-xs text-cyan-300 hover:underline whitespace-nowrap">Ver mesa →</Link>
      </div>
      {coordination.rationale && (
        <p className="text-xs text-gray-300 italic mb-2">“{coordination.rationale}”</p>
      )}
      <div className="flex flex-wrap gap-2">
        {coordination.decisions.map((d, i) => <DecisionChip key={`${d.symbol}-${i}`} d={d} />)}
      </div>
    </div>
  )
}

function DecisionChip({ d }: { d: CoordinatorDecision }) {
  return (
    <div className="flex items-center gap-1.5 bg-gray-900/70 border border-gray-700 rounded-full pl-2 pr-2 py-1 text-xs">
      <span className="font-mono text-cyan-300">{d.symbol}</span>
      <span className={`px-1.5 py-0.5 rounded ${d.approve ? 'bg-green-900 text-green-200' : 'bg-red-900 text-red-200'}`}>
        {d.approve ? 'aprobada' : 'vetada'}
      </span>
      <span className={`px-1.5 py-0.5 rounded ${actionColors[d.position_action] || 'bg-gray-700 text-gray-300'}`}>
        {d.position_action}{d.manage_direction ? ` (${d.manage_direction})` : ''}
      </span>
      <span className="text-gray-500">P{d.priority}·{pct(d.allocation_pct)}</span>
    </div>
  )
}
