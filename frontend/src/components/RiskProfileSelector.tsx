import { useCallback, useEffect, useRef, useState } from 'react'
import { CoordinatorOverview, RiskProfile } from '../types/bot'
import { getApiUrl, getApiHeaders } from '../config'

const API_URL = getApiUrl()

const pct = (n: number | null | undefined, dp = 0) =>
  n === null || n === undefined ? 'n/a' : `${(n * 100).toFixed(dp)}%`

// Niveles del selector, en orden de menor a mayor riesgo. Las clases de color van
// como strings literales completos para que el scanner de Tailwind las incluya.
export const RISK_PROFILE_OPTIONS: {
  id: RiskProfile; label: string; desc: string; accent: string; dot: string; ringKnob: string
}[] = [
  { id: 'conservative', label: 'Conservador', desc: 'Topes estrictos, sin piramidar',
    accent: 'text-sky-300', dot: 'group-hover:bg-sky-400', ringKnob: 'ring-sky-400' },
  { id: 'moderate', label: 'Moderado', desc: 'Comportamiento por defecto',
    accent: 'text-emerald-300', dot: 'group-hover:bg-emerald-400', ringKnob: 'ring-emerald-400' },
  { id: 'aggressive', label: 'Agresivo', desc: 'Piramida tendencias ganadoras',
    accent: 'text-amber-300', dot: 'group-hover:bg-amber-400', ringKnob: 'ring-amber-400' },
  { id: 'extreme', label: 'Extremo', desc: 'Máxima exposición y piramidación',
    accent: 'text-red-300', dot: 'bg-red-700/70 group-hover:bg-red-400', ringKnob: 'ring-red-400' },
]

const N = RISK_PROFILE_OPTIONS.length

/**
 * Selector de perfil de riesgo (estilo slider "Effort"): pista con dots y un knob
 * deslizante/arrastrable. Cambia en vivo (POST /api/risk-profile) y persiste en .env.
 *
 * - Si recibe `overview` por props (p. ej. la página Mesa que ya lo tiene), lo usa
 *   y avisa al padre con `onChanged` para que recargue tras aplicar.
 * - Si NO recibe `overview` (Dashboard), se autogestiona: hace fetch del estado y lo
 *   refresca periódicamente.
 */
export function RiskProfileSelector({
  overview: overviewProp, onChanged, className = '',
}: {
  overview?: CoordinatorOverview | null
  onChanged?: () => void
  className?: string
}) {
  const selfManaged = overviewProp === undefined
  const [overviewLocal, setOverviewLocal] = useState<CoordinatorOverview | null>(null)
  const overview = selfManaged ? overviewLocal : overviewProp
  const [busy, setBusy] = useState(false)
  // Índice "en arrastre" (snap al dot más cercano); null = no se está arrastrando.
  const [dragIdx, setDragIdx] = useState<number | null>(null)
  const trackRef = useRef<HTMLDivElement>(null)

  const loadLocal = useCallback(() => {
    fetch(`${API_URL}/api/coordinator`, { headers: getApiHeaders() })
      .then(r => r.json())
      .then(setOverviewLocal)
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (!selfManaged) return
    loadLocal()
    const id = setInterval(loadLocal, 10000)
    return () => clearInterval(id)
  }, [selfManaged, loadLocal])

  const apply = useCallback((profile: RiskProfile) => {
    setBusy(true)
    fetch(`${API_URL}/api/risk-profile`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getApiHeaders() },
      body: JSON.stringify({ profile }),
    })
      .then(r => r.json())
      .then(() => { if (selfManaged) loadLocal(); else onChanged?.() })
      .catch(() => {})
      .finally(() => setBusy(false))
  }, [selfManaged, loadLocal, onChanged])

  const active: RiskProfile = overview?.risk_profile || 'moderate'
  const activeIdx = Math.max(0, RISK_PROFILE_OPTIONS.findIndex(o => o.id === active))
  const displayIdx = dragIdx ?? activeIdx
  const displayOpt = RISK_PROFILE_OPTIONS[displayIdx]
  const activeOpt = RISK_PROFILE_OPTIONS[activeIdx]
  const isHot = displayOpt.id === 'aggressive' || displayOpt.id === 'extreme'
  // Centro de la celda activa; el knob (w-5 = 1.25rem) se centra restando 0.625rem.
  const knobLeft = `calc(${(displayIdx + 0.5) * (100 / N)}% - 0.625rem)`

  // Índice del dot más cercano a una coordenada X del puntero (snap discreto).
  const idxFromClientX = (clientX: number): number => {
    const el = trackRef.current
    if (!el) return activeIdx
    const rect = el.getBoundingClientRect()
    const ratio = (clientX - rect.left) / rect.width
    return Math.min(N - 1, Math.max(0, Math.round(ratio * N - 0.5)))
  }

  const onPointerDown = (e: React.PointerEvent) => {
    if (busy) return
    e.preventDefault()
    trackRef.current?.setPointerCapture(e.pointerId)
    setDragIdx(idxFromClientX(e.clientX))
  }
  const onPointerMove = (e: React.PointerEvent) => {
    if (dragIdx === null) return
    setDragIdx(idxFromClientX(e.clientX))
  }
  const onPointerUp = (e: React.PointerEvent) => {
    if (dragIdx === null) return
    const idx = idxFromClientX(e.clientX)
    setDragIdx(null)
    if (idx !== activeIdx) apply(RISK_PROFILE_OPTIONS[idx].id)
  }
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (busy) return
    let next = activeIdx
    if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') next = Math.max(0, activeIdx - 1)
    else if (e.key === 'ArrowRight' || e.key === 'ArrowUp') next = Math.min(N - 1, activeIdx + 1)
    else return
    e.preventDefault()
    if (next !== activeIdx) apply(RISK_PROFILE_OPTIONS[next].id)
  }

  return (
    <section className={`bg-gray-800/60 border border-gray-700 rounded-lg px-4 py-3 ${className}`}>
      <div className="flex items-center justify-between gap-4">
        {/* Etiqueta con el nivel activo entre paréntesis (estilo "Effort (Medium)") */}
        <div className="min-w-0">
          <span className="text-sm font-semibold text-gray-200">Perfil de riesgo </span>
          <span className={`text-sm font-semibold ${displayOpt.accent}`}>({displayOpt.label})</span>
          <p className="text-[11px] text-gray-400 mt-0.5 truncate">
            {displayOpt.desc}
            {isHot && <span className="text-amber-300"> · ⚠ mayor drawdown posible</span>}
          </p>
        </div>

        {/* Slider de niveles discretos: pista + dots + knob arrastrable */}
        <div
          ref={trackRef}
          role="slider"
          tabIndex={0}
          aria-label="Perfil de riesgo"
          aria-valuemin={1}
          aria-valuemax={N}
          aria-valuenow={activeIdx + 1}
          aria-valuetext={activeOpt.label}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onKeyDown={onKeyDown}
          className={`relative shrink-0 h-7 w-44 rounded-full bg-gray-900 border border-gray-700 flex
            touch-none select-none cursor-pointer focus:outline-none focus:ring-2 focus:ring-gray-500
            ${busy ? 'opacity-60 pointer-events-none' : ''} ${dragIdx !== null ? 'cursor-grabbing' : ''}`}
        >
          {RISK_PROFILE_OPTIONS.map((opt, i) => {
            const selected = i === displayIdx
            return (
              <div
                key={opt.id}
                title={`${opt.label} — ${opt.desc}`}
                className="relative flex-1 flex items-center justify-center group"
              >
                <span
                  className={`block w-1.5 h-1.5 rounded-full transition-colors
                    ${selected ? 'opacity-0' : `bg-gray-600 ${opt.dot}`}`}
                />
              </div>
            )
          })}
          {/* Knob deslizante sobre el nivel mostrado (activo o en arrastre) */}
          <span
            aria-hidden
            className={`pointer-events-none absolute top-1/2 -translate-y-1/2 h-5 w-5 rounded-full
              bg-white shadow-md ring-2 ${displayOpt.ringKnob}
              ${dragIdx === null ? 'transition-[left] duration-200 ease-out' : ''}`}
            style={{ left: knobLeft }}
          />
        </div>
      </div>

      {overview && (
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-gray-500 mt-2 pt-2 border-t border-gray-700/60">
          <span>Exposición total máx. {pct(overview.max_total_exposure_pct)}</span>
          <span>Sesgo neto máx. {pct(overview.max_net_direction_pct)}</span>
          <span>Piramidar hasta {pct(overview.max_pyramid_direction_pct)}</span>
          <span>Reanálisis al máx. {overview.at_max_analysis_interval ? `${Math.round(overview.at_max_analysis_interval / 60)} min` : 'n/a'}</span>
        </div>
      )}
    </section>
  )
}
