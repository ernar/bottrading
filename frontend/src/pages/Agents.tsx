import { useEffect, useState, useCallback, useMemo } from 'react'
import { AgentsOverview, AgentInfo } from '../types/bot'
import { getApiUrl, getApiHeaders } from '../config'

const API_URL = getApiUrl()

// Columnas ordenables de la tabla de agentes. `get` extrae el valor para
// ordenar (número o texto); `align` controla la alineación de la celda.
type SortKey =
  | 'name' | 'symbol' | 'model' | 'enabled' | 'market_open'
  | 'signals' | 'trades' | 'holds'
  | 'win_rate' | 'sl_hit_rate' | 'tp_hit_rate' | 'avg_move_pct'
  | 'min_confidence' | 'min_rr' | 'atr_sl_mult' | 'lot_size'

const COLUMNS: { key: SortKey; label: string; align: 'left' | 'right'; get: (a: AgentInfo) => number | string }[] = [
  { key: 'name', label: 'Agente', align: 'left', get: a => a.name },
  { key: 'symbol', label: 'Símbolo', align: 'left', get: a => a.symbol },
  { key: 'model', label: 'Modelo', align: 'left', get: a => `${a.provider}/${a.model}` },
  { key: 'enabled', label: 'Activo', align: 'left', get: a => (a.enabled === false ? 0 : 1) },
  { key: 'market_open', label: 'Mercado', align: 'left', get: a => (a.market_open === false ? 0 : 1) },
  { key: 'signals', label: 'Señales', align: 'right', get: a => a.stats.signals },
  { key: 'trades', label: 'Trades', align: 'right', get: a => a.stats.trades },
  { key: 'holds', label: 'Holds', align: 'right', get: a => a.stats.holds },
  { key: 'win_rate', label: 'Win rate', align: 'right', get: a => a.performance.win_rate },
  { key: 'sl_hit_rate', label: 'SL%', align: 'right', get: a => a.performance.sl_hit_rate },
  { key: 'tp_hit_rate', label: 'TP%', align: 'right', get: a => a.performance.tp_hit_rate },
  { key: 'avg_move_pct', label: 'Mov. medio', align: 'right', get: a => a.performance.avg_move_pct },
  { key: 'min_confidence', label: 'Conf. mín.', align: 'right', get: a => a.params.min_confidence },
  { key: 'min_rr', label: 'R:R mín.', align: 'right', get: a => a.params.min_rr },
  { key: 'atr_sl_mult', label: 'ATR SL/TP', align: 'right', get: a => a.params.atr_sl_mult },
  { key: 'lot_size', label: 'Lote', align: 'right', get: a => a.params.lot_size },
]

export function AgentsPage() {
  const [overview, setOverview] = useState<AgentsOverview | null>(null)
  const [models, setModels] = useState<Record<string, string[]>>({})
  const [busy, setBusy] = useState(false)
  const [sortKey, setSortKey] = useState<SortKey>('name')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')
  // Feedback efímero tras guardar la selección de agentes.
  const [savedMsg, setSavedMsg] = useState<string | null>(null)

  const load = useCallback(() => {
    fetch(`${API_URL}/api/agents`, { headers: getApiHeaders() })
      .then(r => r.json())
      .then(setOverview)
      .catch(() => {})
  }, [])

  useEffect(() => {
    load()
    fetch(`${API_URL}/api/models`, { headers: getApiHeaders() })
      .then(r => r.json())
      .then(setModels)
      .catch(() => {})
    const id = setInterval(load, 10000)
    return () => clearInterval(id)
  }, [load])

  const changeModel = (name: string, provider: string, model: string) => {
    setBusy(true)
    fetch(`${API_URL}/api/agents/${name}/model`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getApiHeaders() },
      body: JSON.stringify({ provider, model }),
    })
      .then(r => r.json())
      .then(() => load())
      .catch(() => {})
      .finally(() => setBusy(false))
  }

  const toggleEnabled = (name: string, enabled: boolean) => {
    setBusy(true)
    fetch(`${API_URL}/api/agents/${name}/enabled`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getApiHeaders() },
      body: JSON.stringify({ enabled }),
    })
      .then(r => r.json())
      .then(() => load())
      .catch(() => {})
      .finally(() => setBusy(false))
  }

  const activateAgent = (name: string) => {
    setBusy(true)
    fetch(`${API_URL}/api/agents/${name}/activate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getApiHeaders() },
      body: JSON.stringify({}),
    })
      .then(r => r.json())
      .then(() => load())
      .catch(() => {})
      .finally(() => setBusy(false))
  }

  const changeParams = (name: string, updates: Record<string, number>) => {
    setBusy(true)
    return fetch(`${API_URL}/api/agents/${name}/params`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getApiHeaders() },
      body: JSON.stringify(updates),
    })
      .then(r => r.json())
      .then(() => load())
      .catch(() => {})
      .finally(() => setBusy(false))
  }

  const saveSelection = () => {
    setBusy(true)
    fetch(`${API_URL}/api/agents/save`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getApiHeaders() },
      body: JSON.stringify({}),
    })
      .then(r => r.json())
      .then((res) => {
        const n = res?.saved?.length ?? 0
        setSavedMsg(`Selección guardada (${n} agente${n === 1 ? '' : 's'}) — se reusará al reiniciar`)
        setTimeout(() => setSavedMsg(null), 4000)
      })
      .catch(() => {})
      .finally(() => setBusy(false))
  }

  const runOptimization = (apply: boolean) => {
    setBusy(true)
    fetch(`${API_URL}/api/agents/optimize`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getApiHeaders() },
      body: JSON.stringify({ apply }),
    })
      .then(r => r.json())
      .then(() => load())
      .catch(() => {})
      .finally(() => setBusy(false))
  }

  const pct = (n: number) => `${(n * 100).toFixed(0)}%`
  const agents = overview?.agents || []
  const available = overview?.available || []

  const toggleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir(d => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir('asc')
    }
  }

  const sortedAgents = useMemo(() => {
    const col = COLUMNS.find(c => c.key === sortKey)
    if (!col) return agents
    const dir = sortDir === 'asc' ? 1 : -1
    return [...agents].sort((a, b) => {
      const va = col.get(a)
      const vb = col.get(b)
      if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * dir
      return String(va).localeCompare(String(vb)) * dir
    })
  }, [agents, sortKey, sortDir])

  if (agents.length === 0) {
    return (
      <div className="p-4 sm:p-8">
        <div className="bg-gray-800 text-gray-400 p-8 rounded text-center">
          No hay agentes activos. Arranca el sistema agéntico (<code>python main.py</code>) y
          selecciona al menos un agente.
        </div>
      </div>
    )
  }

  return (
    <div className="p-4 sm:p-8 space-y-8">
      <section>
        <div className="flex flex-wrap items-center justify-between gap-2 mb-4">
          <h2 className="text-xl font-bold">Agentes activos</h2>
          <div className="flex items-center gap-2">
            {savedMsg && <span className="text-xs text-emerald-400">✓ {savedMsg}</span>}
            <button
              onClick={saveSelection}
              disabled={busy}
              title="Guarda los agentes cargados (modelo + ON/OFF) para reusarlos en el próximo arranque"
              className="px-3 py-2 text-sm rounded bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50"
            >
              Guardar selección
            </button>
            <button
              onClick={() => runOptimization(false)}
              disabled={busy}
              className="px-3 py-2 text-sm rounded bg-gray-700 hover:bg-gray-600 disabled:opacity-50"
            >
              Simular optimización
            </button>
            <button
              onClick={() => runOptimization(true)}
              disabled={busy}
              className="px-3 py-2 text-sm rounded bg-blue-600 hover:bg-blue-500 disabled:opacity-50"
            >
              Optimizar y aplicar
            </button>
          </div>
        </div>

        <div className="overflow-x-auto bg-gray-800 rounded-lg border border-gray-700">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-700 text-gray-400">
                {COLUMNS.map(col => {
                  const active = col.key === sortKey
                  return (
                    <th
                      key={col.key}
                      onClick={() => toggleSort(col.key)}
                      className={`px-3 py-2 font-semibold whitespace-nowrap cursor-pointer select-none hover:text-gray-200 ${
                        col.align === 'right' ? 'text-right' : 'text-left'
                      } ${active ? 'text-cyan-300' : ''}`}
                    >
                      {col.label}
                      <span className="ml-1 text-xs">
                        {active ? (sortDir === 'asc' ? '▲' : '▼') : ''}
                      </span>
                    </th>
                  )
                })}
              </tr>
            </thead>
            <tbody>
              {sortedAgents.map(a => (
                <tr key={a.name} className="border-b border-gray-700/50 last:border-0 hover:bg-gray-700/30">
                  <td className="px-3 py-2 font-bold text-cyan-300 whitespace-nowrap" title={a.description}>{a.name}</td>
                  <td className="px-3 py-2 font-mono text-gray-300 whitespace-nowrap">{a.symbol}</td>
                  <td className="px-3 py-2 min-w-[180px]">
                    <ModelSelector
                      provider={a.provider}
                      model={a.model}
                      models={models}
                      disabled={busy}
                      onChange={(p, m) => changeModel(a.name, p, m)}
                    />
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    <button
                      onClick={() => toggleEnabled(a.name, a.enabled === false)}
                      disabled={busy}
                      title={a.enabled === false
                        ? 'Activar: el agente analizará en las siguientes rotaciones'
                        : 'Desactivar: el agente dejará de analizar y proponer entradas'}
                      className={`text-xs px-2 py-0.5 rounded font-semibold disabled:opacity-50 ${
                        a.enabled === false
                          ? 'bg-gray-700 text-gray-300 hover:bg-gray-600'
                          : 'bg-green-700 text-green-100 hover:bg-green-600'
                      }`}
                    >
                      {a.enabled === false ? 'OFF' : 'ON'}
                    </button>
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    {a.market_open === false ? (
                      <span className="text-xs px-2 py-0.5 rounded bg-amber-900 text-amber-200">Cerrado</span>
                    ) : (
                      <span className="text-xs px-2 py-0.5 rounded bg-green-900 text-green-200">Abierto</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{a.stats.signals}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{a.stats.trades}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{a.stats.holds}</td>
                  <td className="px-3 py-2 text-right tabular-nums" title={`${a.performance.samples} señales`}>
                    {pct(a.performance.win_rate)}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{pct(a.performance.sl_hit_rate)}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{pct(a.performance.tp_hit_rate)}</td>
                  <td className={`px-3 py-2 text-right tabular-nums ${a.performance.avg_move_pct >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {a.performance.avg_move_pct >= 0 ? '+' : ''}{a.performance.avg_move_pct}%
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{pct(a.params.min_confidence)}</td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    <EditableRR
                      value={a.params.min_rr}
                      disabled={busy}
                      onCommit={v => changeParams(a.name, { min_rr: v })}
                    />
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums whitespace-nowrap">{a.params.atr_sl_mult}× / {a.params.atr_tp_mult}×</td>
                  <td className="px-3 py-2 text-right tabular-nums">{a.params.lot_size}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {available.length > 0 && (
        <section>
          <h2 className="text-xl font-bold mb-1">Agentes inactivos</h2>
          <p className="text-xs text-gray-500 mb-4">
            Del catálogo, no cargados al arrancar. Actívalos para que analicen y propongan
            entradas desde la siguiente rotación.
          </p>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {available.map(a => (
              <div key={a.name} className="bg-gray-800 rounded-lg border border-gray-700 p-4 flex flex-col gap-2">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="font-bold text-cyan-300">{a.name}</span>
                  <span className="font-mono text-xs text-gray-400">{a.symbol}</span>
                </div>
                <p className="text-xs text-gray-400 flex-1">{a.description}</p>
                <div className="flex items-center justify-between gap-2 text-xs text-gray-500">
                  <span className="font-mono">{a.provider}/{a.model}</span>
                  {a.market_open === false ? (
                    <span className="px-2 py-0.5 rounded bg-amber-900 text-amber-200">Cerrado</span>
                  ) : (
                    <span className="px-2 py-0.5 rounded bg-green-900 text-green-200">Abierto</span>
                  )}
                </div>
                <button
                  onClick={() => activateAgent(a.name)}
                  disabled={busy}
                  className="mt-1 px-3 py-2 text-sm rounded bg-blue-600 hover:bg-blue-500 disabled:opacity-50 font-semibold"
                >
                  Activar
                </button>
              </div>
            ))}
          </div>
        </section>
      )}

      <section>
        <h2 className="text-xl font-bold mb-2">Última optimización</h2>
        <p className="text-xs text-gray-500 mb-3">
          Automática cada {overview?.optimize_every_cycles || 0} ciclos.
          {overview?.last_optimization_at ? ` Última: ${overview.last_optimization_at}` : ' Aún no se ha ejecutado.'}
        </p>
        {overview?.last_optimization && overview.last_optimization.length > 0 ? (
          <div className="space-y-3">
            {overview.last_optimization.map((e, i) => (
              <div key={i} className="bg-gray-800 rounded p-4 border border-gray-700">
                <div className="flex justify-between items-baseline">
                  <span className="font-semibold text-cyan-300">{e.agent} <span className="text-gray-500 font-mono text-xs">{e.symbol}</span></span>
                  <span className={`text-xs px-2 py-0.5 rounded ${e.applied ? 'bg-green-900 text-green-200' : 'bg-gray-700 text-gray-300'}`}>
                    {e.applied ? 'aplicado' : 'sin cambios'}
                  </span>
                </div>
                <div className="text-xs text-gray-400 mt-2">{e.reasons.join('; ')}</div>
                {e.changes.length > 0 && (
                  <ul className="text-xs text-yellow-300 mt-1 list-disc list-inside">
                    {e.changes.map((c, j) => <li key={j}>{c}</li>)}
                  </ul>
                )}
              </div>
            ))}
          </div>
        ) : (
          <div className="bg-gray-800 text-gray-400 p-6 rounded text-center text-sm">
            Sin optimizaciones aplicadas todavía.
          </div>
        )}
      </section>
    </div>
  )
}

// Celda editable del R:R mínimo: muestra "1:x.x" y al hacer clic abre un input
// numérico. Confirma con Enter o al perder el foco (Esc cancela). El backend
// recorta el valor a su rango de seguridad (1.0–3.0).
function EditableRR({
  value, disabled, onCommit,
}: {
  value: number
  disabled: boolean
  onCommit: (v: number) => void
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(String(value))

  const start = () => {
    if (disabled) return
    setDraft(String(value))
    setEditing(true)
  }

  const commit = () => {
    setEditing(false)
    const v = parseFloat(draft)
    if (!Number.isNaN(v) && v !== value) onCommit(v)
  }

  if (!editing) {
    return (
      <button
        onClick={start}
        disabled={disabled}
        title="Click para editar el R:R mínimo"
        className="tabular-nums hover:text-cyan-300 disabled:opacity-50 cursor-pointer"
      >
        1:{value}
      </button>
    )
  }

  return (
    <input
      type="number"
      step="0.1"
      min="1"
      max="3"
      autoFocus
      value={draft}
      onChange={e => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={e => {
        if (e.key === 'Enter') commit()
        else if (e.key === 'Escape') setEditing(false)
      }}
      className="w-16 bg-gray-900 border border-cyan-600 rounded px-1 py-0.5 text-right text-xs text-gray-100"
    />
  )
}

function ModelSelector({
  provider, model, models, disabled, onChange,
}: {
  provider: string
  model: string
  models: Record<string, string[]>
  disabled: boolean
  onChange: (provider: string, model: string) => void
}) {
  // Lista plana "provider/model" para el <select>; el valor activo es el actual.
  const current = `${provider}/${model}`
  const options = Object.entries(models).flatMap(([prov, list]) =>
    list.map(m => `${prov}/${m}`)
  )
  // El modelo activo puede no estar en la lista (p.ej. clave retirada): lo añadimos.
  if (!options.includes(current)) options.unshift(current)

  return (
    <div className="mt-2">
      <label className="text-xs text-gray-500">Modelo LLM</label>
      <select
        value={current}
        disabled={disabled}
        onChange={e => {
          const [p, ...rest] = e.target.value.split('/')
          onChange(p, rest.join('/'))
        }}
        className="w-full mt-1 bg-gray-900 border border-gray-700 rounded px-2 py-1 text-xs text-gray-200 disabled:opacity-50"
      >
        {options.map(opt => (
          <option key={opt} value={opt}>{opt.toUpperCase()}</option>
        ))}
      </select>
    </div>
  )
}

