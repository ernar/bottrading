import { useCallback, useEffect, useState } from 'react'
import { getApiUrl, getApiHeaders } from '../config'

const API_URL = getApiUrl()

interface SpreadRow {
  name: string
  symbol: string
  value: number
}

interface SpreadData {
  default: string
  symbols: SpreadRow[]
}

type Status =
  | { s: 'loading' }
  | { s: 'ready' }
  | { s: 'saving' }
  | { s: 'saved' }
  | { s: 'error'; msg: string }

/**
 * Editor del filtro de spread (en puntos) por símbolo, dentro de la pestaña
 * Riesgo de Ajustes. Autocontenido (como ConnectionSettings): tiene su propio
 * fetch y botón Guardar, separado del formulario del .env. Escribe en
 * MAX_SPREAD_FILTER_DEFAULT / MAX_SPREAD_FILTER_<SÍMBOLO> y se aplica en caliente.
 *
 * El baseline lo puede ajustar además la MESA por decisión (max_spread), de forma
 * transitoria; aquí solo se configura el valor por defecto del símbolo.
 */
export function SpreadSettings() {
  const [status, setStatus] = useState<Status>({ s: 'loading' })
  const [def, setDef] = useState('')
  const [rows, setRows] = useState<Record<string, string>>({})
  const [meta, setMeta] = useState<SpreadRow[]>([])

  const load = useCallback(() => {
    setStatus({ s: 'loading' })
    fetch(`${API_URL}/api/risk/spread`, { headers: getApiHeaders() })
      .then(async r => {
        if (r.status === 401) throw new Error('No autorizado: define el API Token en la pestaña «Conexión».')
        if (!r.ok) throw new Error(`El servidor respondió HTTP ${r.status}.`)
        return r.json() as Promise<SpreadData>
      })
      .then(data => {
        setDef(data.default ?? '')
        setMeta(data.symbols || [])
        const init: Record<string, string> = {}
        for (const row of data.symbols || []) init[row.symbol] = String(row.value)
        setRows(init)
        setStatus({ s: 'ready' })
      })
      .catch(e => setStatus({ s: 'error', msg: e instanceof Error ? e.message : 'error' }))
  }, [])

  useEffect(() => { load() }, [load])

  const save = () => {
    const symbols: Record<string, string> = {}
    for (const row of meta) symbols[row.symbol] = rows[row.symbol] ?? ''
    setStatus({ s: 'saving' })
    fetch(`${API_URL}/api/risk/spread`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getApiHeaders() },
      body: JSON.stringify({ default: def, symbols }),
    })
      .then(async r => {
        const j = await r.json()
        if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`)
        return j
      })
      .then(() => { setStatus({ s: 'saved' }); load() })
      .catch(e => setStatus({ s: 'error', msg: e instanceof Error ? e.message : 'error' }))
  }

  const input = (value: string, onChange: (v: string) => void) => (
    <input
      type="number"
      step="any"
      min="0"
      value={value}
      onChange={e => onChange(e.target.value)}
      className="w-24 px-2 py-1 rounded bg-gray-900 border border-gray-600 text-white text-sm
                 focus:outline-none focus:border-blue-500"
    />
  )

  return (
    <div className="mb-5 pl-3 border-l-2 border-transparent">
      <div className="flex items-center gap-2 flex-wrap">
        <label className="text-sm font-medium text-gray-200">Filtro de spread por símbolo (puntos)</label>
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-900 text-emerald-200 align-middle">en caliente</span>
      </div>
      <p className="text-xs text-gray-500 mt-0.5 mb-3 max-w-2xl">
        Por encima de este spread no se abre la entrada (filtro de coste). El valor por defecto
        aplica a los símbolos sin ajuste propio. La <span className="text-gray-400">mesa</span> puede
        afinarlo por decisión de forma transitoria sin tocar esta configuración.
      </p>

      {status.s === 'loading' && (
        <div className="text-gray-400 text-sm">Cargando…</div>
      )}
      {status.s === 'error' && (
        <div className="bg-red-900/40 text-red-300 p-3 rounded text-sm mb-3">{status.msg}</div>
      )}

      {status.s !== 'loading' && (
        <>
          <div className="space-y-2 max-w-md">
            <div className="flex items-center justify-between gap-4 pb-2 border-b border-gray-700/60">
              <span className="text-sm text-gray-300">Por defecto</span>
              {input(def, setDef)}
            </div>
            {meta.map(row => (
              <div key={row.symbol} className="flex items-center justify-between gap-4">
                <span className="text-sm text-gray-300">
                  {row.symbol}
                  <span className="text-[11px] text-gray-500 ml-2">{row.name}</span>
                </span>
                {input(rows[row.symbol] ?? '', v => setRows(prev => ({ ...prev, [row.symbol]: v })))}
              </div>
            ))}
            {meta.length === 0 && (
              <div className="text-gray-500 text-sm py-2">No hay agentes cargados.</div>
            )}
          </div>
          <div className="flex items-center gap-3 mt-3">
            <button
              onClick={save}
              disabled={status.s === 'saving'}
              className="px-3 py-1.5 text-sm rounded bg-blue-600 hover:bg-blue-500 disabled:opacity-40"
            >
              {status.s === 'saving' ? 'Guardando…' : 'Guardar spreads'}
            </button>
            {status.s === 'saved' && <span className="text-sm text-green-400">✓ Guardado y aplicado</span>}
          </div>
        </>
      )}
    </div>
  )
}
