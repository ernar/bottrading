import { useEffect, useState, useCallback } from 'react'
import { AgentsOverview } from '../types/bot'
import { getApiUrl, getApiHeaders } from '../config'

const API_URL = getApiUrl()

export function AgentsPage() {
  const [overview, setOverview] = useState<AgentsOverview | null>(null)
  const [models, setModels] = useState<Record<string, string[]>>({})
  const [busy, setBusy] = useState(false)

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
          <div className="flex gap-2">
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

        <div className="grid gap-4 md:grid-cols-2">
          {agents.map(a => (
            <div key={a.name} className="bg-gray-800 rounded-lg p-5 border border-gray-700">
              <div className="flex items-baseline justify-between">
                <h3 className="text-lg font-bold text-cyan-300">{a.name}</h3>
                <div className="flex items-center gap-2">
                  {a.market_open === false && (
                    <span className="text-xs px-2 py-0.5 rounded bg-amber-900 text-amber-200">
                      Mercado cerrado
                    </span>
                  )}
                  <span className="text-sm font-mono text-gray-400">{a.symbol}</span>
                </div>
              </div>
              <p className="text-xs text-gray-400 mt-1">{a.description}</p>
              <ModelSelector
                provider={a.provider}
                model={a.model}
                models={models}
                disabled={busy}
                onChange={(p, m) => changeModel(a.name, p, m)}
              />

              <div className="grid grid-cols-3 gap-2 mt-4 text-center">
                <Stat label="Señales" value={a.stats.signals} />
                <Stat label="Trades" value={a.stats.trades} />
                <Stat label="Holds" value={a.stats.holds} />
              </div>

              <div className="mt-4 text-xs text-gray-300 space-y-1">
                <div className="font-semibold text-gray-400">Rendimiento (memoria)</div>
                <div className="flex justify-between">
                  <span>Win rate</span>
                  <span>{pct(a.performance.win_rate)} ({a.performance.samples} señales)</span>
                </div>
                <div className="flex justify-between">
                  <span>SL tocados / TP alcanzados</span>
                  <span>{pct(a.performance.sl_hit_rate)} / {pct(a.performance.tp_hit_rate)}</span>
                </div>
                <div className="flex justify-between">
                  <span>Movimiento medio</span>
                  <span>{a.performance.avg_move_pct >= 0 ? '+' : ''}{a.performance.avg_move_pct}%</span>
                </div>
              </div>

              <div className="mt-4 text-xs text-gray-300 space-y-1">
                <div className="font-semibold text-gray-400">Parámetros</div>
                <div className="flex justify-between"><span>Confianza mín.</span><span>{pct(a.params.min_confidence)}</span></div>
                <div className="flex justify-between"><span>R:R mín.</span><span>1:{a.params.min_rr}</span></div>
                <div className="flex justify-between"><span>ATR SL / TP</span><span>{a.params.atr_sl_mult}× / {a.params.atr_tp_mult}×</span></div>
                <div className="flex justify-between"><span>Lote</span><span>{a.params.lot_size}</span></div>
              </div>
            </div>
          ))}
        </div>
      </section>

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

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-gray-900 rounded py-2">
      <div className="text-lg font-bold">{value}</div>
      <div className="text-xs text-gray-500">{label}</div>
    </div>
  )
}
