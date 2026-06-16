import { useCallback, useEffect, useRef, useState } from 'react'
import { CoordinatorOverview, RiskProfile, Horizon } from '../types/bot'
import { getApiUrl, getApiHeaders } from '../config'

const API_URL = getApiUrl()

const pct = (n: number | null | undefined, dp = 0) =>
  n === null || n === undefined ? 'n/a' : `${(n * 100).toFixed(dp)}%`

interface SliderOption {
  id: string; label: string; desc: string; accent: string; dot: string; ringKnob: string
}

// Eje RIESGO: apetito / exposición / selectividad (4 niveles).
export const RISK_OPTIONS: SliderOption[] = [
  { id: 'conservative', label: 'Conservador', desc: 'Muy selectivo, exposición baja',
    accent: 'text-sky-300', dot: 'group-hover:bg-sky-400', ringKnob: 'ring-sky-400' },
  { id: 'moderate', label: 'Moderado', desc: 'Equilibrio oportunidad/prudencia',
    accent: 'text-emerald-300', dot: 'group-hover:bg-emerald-400', ringKnob: 'ring-emerald-400' },
  { id: 'aggressive', label: 'Agresivo', desc: 'Busca entradas, piramida ganadores',
    accent: 'text-amber-300', dot: 'group-hover:bg-amber-400', ringKnob: 'ring-amber-400' },
  { id: 'extreme', label: 'Extremo', desc: 'Máxima proactividad y exposición',
    accent: 'text-red-300', dot: 'bg-red-700/70 group-hover:bg-red-400', ringKnob: 'ring-red-400' },
]

// Eje HORIZONTE: duración de las operaciones (3 niveles).
export const HORIZON_OPTIONS: SliderOption[] = [
  { id: 'corto', label: 'Corto', desc: 'Scalping: TP cercano, asegura pronto',
    accent: 'text-fuchsia-300', dot: 'group-hover:bg-fuchsia-400', ringKnob: 'ring-fuchsia-400' },
  { id: 'medio', label: 'Medio', desc: 'Objetivos y stops equilibrados',
    accent: 'text-cyan-300', dot: 'group-hover:bg-cyan-400', ringKnob: 'ring-cyan-400' },
  { id: 'largo', label: 'Largo', desc: 'Swing: deja correr la tendencia',
    accent: 'text-violet-300', dot: 'group-hover:bg-violet-400', ringKnob: 'ring-violet-400' },
]

/**
 * Slider genérico de niveles discretos (estilo "Effort"): pista con dots y un knob
 * arrastrable. Soporta clic, arrastre (pointer) y flechas del teclado.
 */
export function ProfileSlider({
  label, options, activeId, onSelect, busy = false,
}: {
  label: string
  options: SliderOption[]
  activeId: string
  onSelect: (id: string) => void
  busy?: boolean
}) {
  const n = options.length
  const activeIdx = Math.max(0, options.findIndex(o => o.id === activeId))
  const [dragIdx, setDragIdx] = useState<number | null>(null)
  const trackRef = useRef<HTMLDivElement>(null)

  const displayIdx = dragIdx ?? activeIdx
  const displayOpt = options[displayIdx]
  const activeOpt = options[activeIdx]
  const knobLeft = `calc(${(displayIdx + 0.5) * (100 / n)}% - 0.625rem)`

  const idxFromClientX = (clientX: number): number => {
    const el = trackRef.current
    if (!el) return activeIdx
    const rect = el.getBoundingClientRect()
    const ratio = (clientX - rect.left) / rect.width
    return Math.min(n - 1, Math.max(0, Math.round(ratio * n - 0.5)))
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
    if (idx !== activeIdx) onSelect(options[idx].id)
  }
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (busy) return
    let next = activeIdx
    if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') next = Math.max(0, activeIdx - 1)
    else if (e.key === 'ArrowRight' || e.key === 'ArrowUp') next = Math.min(n - 1, activeIdx + 1)
    else return
    e.preventDefault()
    if (next !== activeIdx) onSelect(options[next].id)
  }

  return (
    <div className="flex items-center justify-between gap-4">
      <div className="min-w-0">
        <span className="text-sm font-semibold text-gray-200">{label} </span>
        <span className={`text-sm font-semibold ${displayOpt.accent}`}>({displayOpt.label})</span>
        <p className="text-[11px] text-gray-400 mt-0.5 truncate">{displayOpt.desc}</p>
      </div>
      <div
        ref={trackRef}
        role="slider"
        tabIndex={0}
        aria-label={label}
        aria-valuemin={1}
        aria-valuemax={n}
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
        {options.map((opt, i) => {
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
        <span
          aria-hidden
          className={`pointer-events-none absolute top-1/2 -translate-y-1/2 h-5 w-5 rounded-full
            bg-white shadow-md ring-2 ${displayOpt.ringKnob}
            ${dragIdx === null ? 'transition-[left] duration-200 ease-out' : ''}`}
          style={{ left: knobLeft }}
        />
      </div>
    </div>
  )
}

/**
 * Contenedor con los DOS selectores de trading (riesgo + horizonte), ejes
 * independientes. Cambia en vivo (POST /api/risk-profile y /api/horizon) y persiste
 * en .env. Si recibe `overview` por props (Mesa) lo usa y avisa con `onChanged`; si
 * no (Dashboard), se autogestiona con su propio fetch periódico.
 */
export function TradingProfiles({
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

  const post = useCallback((path: string, body: object) => {
    setBusy(true)
    fetch(`${API_URL}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getApiHeaders() },
      body: JSON.stringify(body),
    })
      .then(r => r.json())
      .then(() => { if (selfManaged) loadLocal(); else onChanged?.() })
      .catch(() => {})
      .finally(() => setBusy(false))
  }, [selfManaged, loadLocal, onChanged])

  const risk: RiskProfile = overview?.risk_profile || 'moderate'
  const horizon: Horizon = overview?.horizon || 'medio'
  const isHotRisk = risk === 'aggressive' || risk === 'extreme'

  return (
    <section className={`inline-flex flex-col w-fit max-w-full bg-gray-800/60 border border-gray-700 rounded-lg px-4 py-3 gap-3 ${className}`}>
      {/* Perfil de riesgo y Horizonte en una sola fila (cada uno: etiqueta — toggle) */}
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <ProfileSlider
          label="Perfil de riesgo"
          options={RISK_OPTIONS}
          activeId={risk}
          onSelect={(id) => post('/api/risk-profile', { profile: id })}
          busy={busy}
        />
        <div className="self-stretch w-px bg-gray-700/60" />
        <ProfileSlider
          label="Horizonte"
          options={HORIZON_OPTIONS}
          activeId={horizon}
          onSelect={(id) => post('/api/horizon', { horizon: id })}
          busy={busy}
        />
      </div>
      {overview && (
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-gray-500 pt-2 border-t border-gray-700/60">
          <span>Exposición total máx. {pct(overview.max_total_exposure_pct)}</span>
          <span>Sesgo neto máx. {pct(overview.max_net_direction_pct)}</span>
          <span>Piramidar hasta {pct(overview.max_pyramid_direction_pct)}</span>
          {overview.max_open_positions != null && (
            <span>Posiciones máx./símbolo {overview.max_open_positions || '∞'}</span>
          )}
          <span>R:R objetivo {overview.tp_rr_min ?? '?'}–{overview.tp_rr_max ?? '?'}</span>
          <span>Gracia {overview.min_hold_seconds != null ? `${Math.round(overview.min_hold_seconds / 60)} min` : 'n/a'}</span>
          {isHotRisk && <span className="text-amber-300">⚠ mayor drawdown posible</span>}
        </div>
      )}
    </section>
  )
}
