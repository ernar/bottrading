import { useCallback, useEffect, useState } from 'react'
import { getApiUrl, getApiHeaders } from '../config'

const API_URL = getApiUrl()

// Una entrada del esquema de ajustes que devuelve GET /api/settings.
interface SettingEntry {
  key: string
  label: string
  group: string
  type: 'bool' | 'int' | 'float' | 'str'
  secret: boolean
  hot: boolean
  help: string
  value?: string | number | boolean | null // ausente en secretos
  is_set?: boolean // solo en secretos
}

interface SaveResult {
  changed: string[]
  restart_required: string[]
  applied_hot: string[]
}

type Status =
  | { s: 'idle' }
  | { s: 'loading' }
  | { s: 'error'; msg: string }
  | { s: 'ready' }
  | { s: 'saving' }
  | { s: 'saved'; result: SaveResult }

export function BotSettings() {
  const [entries, setEntries] = useState<SettingEntry[]>([])
  const [edited, setEdited] = useState<Record<string, string | boolean>>({})
  const [status, setStatus] = useState<Status>({ s: 'idle' })

  const load = useCallback(() => {
    setStatus({ s: 'loading' })
    fetch(`${API_URL}/api/settings`, { headers: getApiHeaders() })
      .then(async r => {
        if (r.status === 401) throw new Error('No autorizado: define el API Token arriba.')
        if (!r.ok) throw new Error(`El servidor respondió HTTP ${r.status}.`)
        return r.json()
      })
      .then((data: { settings: SettingEntry[] }) => {
        setEntries(data.settings || [])
        // Valor inicial editable: el actual (no secretos); los secretos vacíos.
        const init: Record<string, string | boolean> = {}
        for (const e of data.settings || []) {
          if (e.secret) init[e.key] = ''
          else if (e.type === 'bool') init[e.key] = Boolean(e.value)
          else init[e.key] = e.value == null ? '' : String(e.value)
        }
        setEdited(init)
        setStatus({ s: 'ready' })
      })
      .catch(e => setStatus({ s: 'error', msg: e instanceof Error ? e.message : 'error' }))
  }, [])

  useEffect(() => { load() }, [load])

  const setVal = (key: string, v: string | boolean) =>
    setEdited(prev => ({ ...prev, [key]: v }))

  const save = () => {
    // Envía todos los no-secretos; los secretos solo si el usuario escribió algo.
    const updates: Record<string, string | boolean> = {}
    for (const e of entries) {
      const v = edited[e.key]
      if (e.secret) {
        if (typeof v === 'string' && v.trim() !== '') updates[e.key] = v
      } else {
        updates[e.key] = v
      }
    }
    setStatus({ s: 'saving' })
    fetch(`${API_URL}/api/settings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getApiHeaders() },
      body: JSON.stringify({ updates }),
    })
      .then(async r => {
        if (r.status === 401) throw new Error('No autorizado: define el API Token arriba.')
        const j = await r.json()
        if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`)
        return j as SaveResult
      })
      .then(result => {
        setStatus({ s: 'saved', result })
        load() // refresca valores y vacía los campos de secreto
      })
      .catch(e => setStatus({ s: 'error', msg: e instanceof Error ? e.message : 'error' }))
  }

  // Agrupa preservando el orden de aparición del esquema.
  const groups: string[] = []
  for (const e of entries) if (!groups.includes(e.group)) groups.push(e.group)

  return (
    <div className="mt-8">
      <h2 className="text-xl font-bold mb-1">Ajustes del bot (.env)</h2>
      <p className="text-sm text-gray-400 mb-4">
        Edita la configuración del bot. Los cambios marcados <Hot /> se aplican en caliente;
        el resto requiere reiniciar <code>main.py</code>. Las contraseñas/token solo se
        guardan si escribes un valor nuevo.
      </p>

      {status.s === 'loading' && (
        <div className="bg-gray-800 text-gray-400 p-6 rounded text-center text-sm">Cargando ajustes…</div>
      )}
      {status.s === 'error' && (
        <div className="bg-red-900/40 text-red-300 p-4 rounded text-sm mb-4">{status.msg}</div>
      )}

      {entries.length > 0 && (
        <>
          {status.s === 'saved' && (
            <div className="bg-green-900/40 text-green-300 p-3 rounded text-sm mb-4 space-y-1">
              <div>Guardado. {status.result.changed.length} ajuste(s) cambiado(s).</div>
              {status.result.applied_hot.length > 0 && (
                <div className="text-green-400">✓ Aplicado en caliente: {status.result.applied_hot.join(', ')}</div>
              )}
              {status.result.restart_required.length > 0 && (
                <div className="text-amber-300">⚠ Requiere reiniciar el bot: {status.result.restart_required.join(', ')}</div>
              )}
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6 items-start">
            {groups.map(group => (
              <div key={group} className="bg-gray-800 rounded-lg border border-gray-700 p-5">
                <h3 className="text-sm font-bold text-cyan-300 uppercase tracking-wide mb-3">{group}</h3>
                <div className="space-y-4">
                  {entries.filter(e => e.group === group).map(e => (
                    <Field key={e.key} entry={e} value={edited[e.key]} onChange={v => setVal(e.key, v)} />
                  ))}
                </div>
              </div>
            ))}
          </div>

          <div className="flex items-center gap-3 mt-4">
            <button
              onClick={save}
              disabled={status.s === 'saving'}
              className="px-4 py-2 text-sm rounded bg-blue-600 hover:bg-blue-500 disabled:opacity-50"
            >
              {status.s === 'saving' ? 'Guardando…' : 'Guardar ajustes'}
            </button>
            <button
              onClick={load}
              disabled={status.s === 'saving'}
              className="px-3 py-2 text-sm rounded bg-gray-700 hover:bg-gray-600 disabled:opacity-50"
            >
              Descartar cambios
            </button>
          </div>
        </>
      )}
    </div>
  )
}

function Hot() {
  return <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-900 text-emerald-200 align-middle">en caliente</span>
}

function Field({
  entry, value, onChange,
}: {
  entry: SettingEntry
  value: string | boolean
  onChange: (v: string | boolean) => void
}) {
  const labelRow = (
    <div className="flex items-center gap-2 flex-wrap">
      <label className="text-sm font-medium text-gray-200">{entry.label}</label>
      <code className="text-[10px] text-gray-500">{entry.key}</code>
      {entry.hot ? <Hot /> : <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-700 text-gray-400">reinicio</span>}
    </div>
  )

  if (entry.type === 'bool') {
    return (
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          {labelRow}
          <p className="text-xs text-gray-500 mt-0.5">{entry.help}</p>
        </div>
        <button
          type="button"
          onClick={() => onChange(!(value as boolean))}
          className={`shrink-0 w-11 h-6 rounded-full transition-colors ${value ? 'bg-emerald-600' : 'bg-gray-600'}`}
          aria-pressed={Boolean(value)}
        >
          <span className={`block w-5 h-5 bg-white rounded-full transition-transform ${value ? 'translate-x-5' : 'translate-x-0.5'}`} />
        </button>
      </div>
    )
  }

  return (
    <div>
      {labelRow}
      <input
        type={entry.secret ? 'password' : entry.type === 'str' ? 'text' : 'number'}
        step={entry.type === 'float' ? 'any' : entry.type === 'int' ? '1' : undefined}
        value={value as string}
        onChange={ev => onChange(ev.target.value)}
        placeholder={entry.secret ? (entry.is_set ? '•••••• (configurado — escribe para cambiar)' : 'sin definir') : ''}
        className="w-full mt-1 px-3 py-2 rounded bg-gray-900 border border-gray-600 text-white text-sm
                   focus:outline-none focus:border-blue-500"
      />
      <p className="text-xs text-gray-500 mt-0.5">{entry.help}</p>
    </div>
  )
}
