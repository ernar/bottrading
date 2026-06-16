import { useEffect, useState, useCallback } from 'react'
import { Coordination, CoordinatorDecision, CoordinatorOverview, CoordinatorSnapshot } from '../types/bot'
import { getApiUrl, getApiHeaders } from '../config'
import { TradingProfiles } from '../components/RiskProfileSelector'
import { formatBrokerClock, formatBrokerTime } from '../utils/format'

const API_URL = getApiUrl()

const pct = (n: number | null | undefined, dp = 1) =>
  n === null || n === undefined ? 'n/a' : `${(n * 100).toFixed(dp)}%`
const money = (n: number | null | undefined) =>
  n === null || n === undefined ? '—' : `$${n.toFixed(2)}`

// Longitud de la ventana móvil de riesgo en formato corto ("6h", "30min").
const windowLabel = (secs: number): string => {
  const h = secs / 3600
  if (h >= 1) return `${Number.isInteger(h) ? h : h.toFixed(1)}h`
  return `${Math.round(secs / 60)}min`
}

// Rango temporal al que aplica el "P/L del día": NO es un día natural, sino la
// ventana móvil de riesgo (RISK_LOSS_WINDOW_SECONDS) desde su último rearme.
const dailyRangeSub = (s: CoordinatorSnapshot): string => {
  if (!s.daily_pnl_window_seconds) return 'guardia de pérdida diaria desactivada'
  const since = s.daily_pnl_since
    ? ` · desde ${formatBrokerClock(s.daily_pnl_since)}`
    : ''
  return `ventana móvil ${windowLabel(s.daily_pnl_window_seconds)}${since}`
}

const dailyRangeHint = (s: CoordinatorSnapshot): string => {
  if (!s.daily_pnl_window_seconds) {
    return 'P/L del día desactivado: requiere MAX_DAILY_LOSS_PCT > 0 para fijar el equity de referencia.'
  }
  const win = windowLabel(s.daily_pnl_window_seconds)
  const desde = s.daily_pnl_since
    ? ` Inicio de la ventana actual: ${formatBrokerTime(s.daily_pnl_since)}.`
    : ''
  return `P/L medido desde el equity al inicio de la ventana móvil de riesgo (se rearma cada ${win}), no desde la medianoche.${desde}`
}

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

      {/* Selectores de perfil de riesgo + horizonte (mesa + agentes, en vivo) */}
      <TradingProfiles overview={overview} onChanged={load} />

      {/* Diagrama de flujo de información mesa ↔ agentes */}
      <FlowDiagram coordination={coordination} overview={overview} />

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
            sub={`${dailyRangeSub(snap)}${snap.in_cooldown ? ' · ⚠ cooldown activo' : ''}`}
            subClass={snap.in_cooldown ? 'text-yellow-400' : 'text-gray-500'}
            hint={dailyRangeHint(snap)}
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
                {/* P/L desglosado por lado: solo informativo en libro cubierto (largos
                    y cortos a la vez), donde el neto oculta qué pata sangra. */}
                {s.long_positions > 0 && s.short_positions > 0 &&
                 s.long_pnl !== undefined && s.short_pnl !== undefined && (
                  <div className="flex gap-3 text-xs text-gray-400 mb-1">
                    <span>
                      largos <span className={s.long_pnl >= 0 ? 'text-green-400' : 'text-red-400'}>{money(s.long_pnl)}</span>
                    </span>
                    <span>
                      cortos <span className={s.short_pnl >= 0 ? 'text-green-400' : 'text-red-400'}>{money(s.short_pnl)}</span>
                    </span>
                  </div>
                )}
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

// Diagrama del flujo de información: los especialistas reportan señales → la
// mesa (RiskBook snapshot → Director LLM → topes/clamp) decide → ejecución por
// prioridad. Cada símbolo es un "carril" con su propuesta y su veredicto.
function FlowDiagram({
  coordination, overview,
}: { coordination: Coordination | null; overview: CoordinatorOverview | null }) {
  const decisions = coordination?.decisions || []
  const snap = coordination?.snapshot
  const symbols = snap ? Object.keys(snap.symbols) : decisions.map(d => d.symbol)
  const decBySym: Record<string, CoordinatorDecision> = {}
  for (const d of decisions) decBySym[d.symbol] = d

  const actionColors: Record<string, string> = {
    close: 'bg-red-900 text-red-200',
    reduce: 'bg-yellow-900 text-yellow-200',
    hedge: 'bg-indigo-900 text-indigo-200',
    hold: 'bg-gray-700 text-gray-300',
  }

  return (
    <section>
      <h3 className="text-lg font-bold mb-3">Flujo de información</h3>
      {/* Tubería de 3 fases */}
      <div className="grid grid-cols-1 md:grid-cols-[1fr_auto_1.4fr_auto_1fr] gap-2 items-stretch">
        <StageCard
          title="① Especialistas"
          accent="text-cyan-300"
          lines={[
            `${symbols.length} agente${symbols.length === 1 ? '' : 's'} por símbolo`,
            'Analizan H1/H4 + noticias',
            'Reportan señal a la mesa',
          ]}
        />
        <FlowArrow label="recolecta" />
        <StageCard
          title="② Mesa de dirección"
          accent="text-emerald-300"
          lines={[
            'RiskBook · snapshot (equity/exposición)',
            `Director LLM · ${overview?.provider?.toUpperCase() || '—'}/${overview?.model || '—'}`,
            'Topes duros · clamp (exposición/reversión)',
          ]}
          highlight
        />
        <FlowArrow label="decide" />
        <StageCard
          title="③ Ejecución"
          accent="text-amber-300"
          lines={[
            'Ordenada por prioridad',
            'Abre entradas aprobadas',
            'Gestiona close/reduce/hedge',
          ]}
        />
      </div>

      {/* Carriles por símbolo: propuesta del agente → veredicto de la mesa */}
      {symbols.length > 0 && (
        <div className="mt-4 space-y-2">
          {symbols.map(sym => {
            const d = decBySym[sym]
            const s = snap?.symbols[sym]
            return (
              <div
                key={sym}
                className="grid grid-cols-[1fr_auto_1fr] items-center gap-2 bg-gray-800/60 rounded-lg border border-gray-700 px-3 py-2 text-xs"
              >
                {/* Especialista */}
                <div className="flex items-center gap-2 min-w-0">
                  <span className="font-mono text-cyan-300">{sym}</span>
                  {s && (
                    <span className="text-gray-500 truncate">
                      {s.long_positions}L/{s.short_positions}S · neto {s.net_direction}
                    </span>
                  )}
                </div>
                {/* Flecha con prioridad/asignación */}
                <div className="flex items-center gap-1 text-gray-500 whitespace-nowrap">
                  {d && <span className="text-[10px] text-gray-400">P{d.priority} · {pct(d.allocation_pct, 0)}</span>}
                  <span className="text-emerald-400">→</span>
                </div>
                {/* Veredicto de la mesa */}
                <div className="flex items-center justify-end gap-2 flex-wrap">
                  {d ? (
                    <>
                      <span className={`px-2 py-0.5 rounded ${d.approve ? 'bg-green-900 text-green-200' : 'bg-red-900 text-red-200'}`}>
                        {d.approve ? 'aprobada' : 'vetada'}
                      </span>
                      <span className={`px-2 py-0.5 rounded ${actionColors[d.position_action] || 'bg-gray-700 text-gray-300'}`}>
                        {d.position_action}{d.manage_direction ? ` (${d.manage_direction})` : ''}
                      </span>
                    </>
                  ) : (
                    <span className="text-gray-500">sin decisión</span>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
      {symbols.length === 0 && (
        <div className="mt-3 bg-gray-800 text-gray-400 p-4 rounded text-center text-xs">
          Aún no hay flujo: se dibujará tras la primera coordinación.
        </div>
      )}

      {/* El responsable (asistente LLM) observa todo el flujo y responde al usuario. */}
      <AssistantNode overview={overview} />
    </section>
  )
}

// Nodo del "responsable de la organización": el asistente conversacional que
// supervisa todas las fases (lee cuenta, mesa y agentes vía API) y rinde cuentas
// al usuario. No ejecuta: solo informa. Enlaza a su chat.
function AssistantNode({ overview }: { overview: CoordinatorOverview | null }) {
  return (
    <div className="mt-4">
      {/* Conector visual hacia arriba (supervisa la tubería) */}
      <div className="flex justify-center">
        <span className="text-gray-600 text-lg leading-none">⌃</span>
      </div>
      <button
        type="button"
        onClick={() => window.dispatchEvent(new Event('assistant:open'))}
        className="w-full text-left rounded-lg p-4 border border-purple-700/60 bg-purple-950/20 hover:border-purple-500 transition"
      >
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-3 min-w-0">
            <span className="text-2xl">🧑‍💼</span>
            <div className="min-w-0">
              <div className="text-sm font-bold text-purple-300">Responsable de la mesa (asistente)</div>
              <div className="text-xs text-gray-400">
                Supervisa cuenta, mesa y agentes en vivo · responde tus preguntas · no opera
              </div>
            </div>
          </div>
          <span className="text-xs text-purple-300 whitespace-nowrap">Abrir chat →</span>
        </div>
        {overview?.provider && (
          <div className="text-[10px] text-gray-500 mt-2">
            consulta el mismo libro que el director · pregúntale “¿cómo vamos?”
          </div>
        )}
      </button>
    </div>
  )
}

function StageCard({
  title, lines, accent, highlight = false,
}: { title: string; lines: string[]; accent: string; highlight?: boolean }) {
  return (
    <div className={`rounded-lg p-4 border ${highlight ? 'border-emerald-700/60 bg-emerald-950/20' : 'border-gray-700 bg-gray-800'}`}>
      <div className={`text-sm font-bold mb-2 ${accent}`}>{title}</div>
      <ul className="space-y-1 text-xs text-gray-300">
        {lines.map((l, i) => (
          <li key={i} className="flex gap-1.5">
            <span className="text-gray-600">•</span>
            <span className="min-w-0">{l}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function FlowArrow({ label }: { label: string }) {
  return (
    <div className="flex md:flex-col items-center justify-center text-gray-500 px-1">
      <span className="text-2xl text-emerald-400 animate-pulse">→</span>
      <span className="text-[10px] uppercase tracking-wide md:mt-1">{label}</span>
    </div>
  )
}

function Card({
  label, value, sub, valueClass = 'text-white', subClass = 'text-gray-500', hint,
}: {
  label: string; value: string; sub?: string; valueClass?: string; subClass?: string; hint?: string
}) {
  return (
    <div className="bg-gray-800 rounded-lg p-5 border border-gray-700" title={hint}>
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
        {d.approve && !!d.size_mult && d.size_mult > 0 && (
          <span className={`px-2 py-0.5 rounded ${d.size_mult > 1 ? 'bg-emerald-900 text-emerald-200' : 'bg-amber-900 text-amber-200'}`}>
            lote {d.size_mult.toFixed(2)}×
          </span>
        )}
        {d.approve && !!d.tp_rr && d.tp_rr > 0 && (
          <span className="px-2 py-0.5 rounded bg-gray-900 text-gray-300">TP obj. 1:{d.tp_rr.toFixed(2)}</span>
        )}
        <span className={`px-2 py-0.5 rounded ${actionColors[d.position_action] || 'bg-gray-700 text-gray-300'}`}>
          posiciones: {d.position_action}{d.manage_direction ? ` (${d.manage_direction})` : ''}
        </span>
      </div>
      {d.reason && <p className="text-xs text-gray-400 mt-2">{d.reason}</p>}
      {d.clamp && <p className="text-xs text-yellow-300 mt-1">⚠ {d.clamp}</p>}
    </div>
  )
}
