import { useCallback, useEffect, useMemo, useState } from 'react'
import { getApiUrl, getApiHeaders } from '../config'
import { ConnectionSettings } from './ConnectionSettings'

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

type Val = string | boolean

const ALL = '__all__'
const CONN = '__conn__' // pestaña de conexión del dashboard (no es un grupo del .env)
// Claves del modelo del asistente que el selector sustituye (no se muestran como texto).
const ASSISTANT_MODEL_KEYS = ['ASSISTANT_PROVIDER', 'ASSISTANT_MODEL']

export function BotSettings() {
  const [entries, setEntries] = useState<SettingEntry[]>([])
  const [original, setOriginal] = useState<Record<string, Val>>({})
  const [edited, setEdited] = useState<Record<string, Val>>({})
  const [status, setStatus] = useState<Status>({ s: 'idle' })
  const [query, setQuery] = useState('')
  const [activeGroup, setActiveGroup] = useState(CONN)
  // Catálogo de proveedores/modelos disponibles (solo los que tienen API key en
  // el .env), igual que el selector de agentes y mesa. Puebla el desplegable del
  // modelo del asistente: el usuario elige sin reescribir la clave.
  const [models, setModels] = useState<Record<string, string[]>>({})

  const load = useCallback(() => {
    setStatus({ s: 'loading' })
    fetch(`${API_URL}/api/settings`, { headers: getApiHeaders() })
      .then(async r => {
        if (r.status === 401) throw new Error('No autorizado: define el API Token en la pestaña «Conexión».')
        if (!r.ok) throw new Error(`El servidor respondió HTTP ${r.status}.`)
        return r.json()
      })
      .then((data: { settings: SettingEntry[] }) => {
        const list = data.settings || []
        setEntries(list)
        // Valor inicial editable: el actual (no secretos); los secretos vacíos.
        const init: Record<string, Val> = {}
        for (const e of list) {
          if (e.secret) init[e.key] = ''
          else if (e.type === 'bool') init[e.key] = Boolean(e.value)
          else init[e.key] = e.value == null ? '' : String(e.value)
        }
        setEdited(init)
        setOriginal(init)
        setStatus({ s: 'ready' })
      })
      .catch(e => setStatus({ s: 'error', msg: e instanceof Error ? e.message : 'error' }))
  }, [])

  useEffect(() => { load() }, [load])

  // Catálogo de modelos para el selector del asistente (mismo endpoint que agentes/mesa).
  useEffect(() => {
    fetch(`${API_URL}/api/models`, { headers: getApiHeaders() })
      .then(r => r.json())
      .then(setModels)
      .catch(() => {})
  }, [])

  const setVal = (key: string, v: Val) => setEdited(prev => ({ ...prev, [key]: v }))

  // Grupos en orden de aparición del esquema.
  const groups = useMemo(() => {
    const g: string[] = []
    for (const e of entries) if (!g.includes(e.group)) g.push(e.group)
    return g
  }, [entries])

  // ¿Cambió un campo respecto al valor cargado? (un secreto es "sucio" si se
  // escribió algo, ya que su valor real no se conoce).
  const isDirty = useCallback((e: SettingEntry) => {
    const v = edited[e.key]
    if (e.secret) return typeof v === 'string' && v.trim() !== ''
    return v !== original[e.key]
  }, [edited, original])

  const dirtyKeys = useMemo(() => entries.filter(isDirty).map(e => e.key), [entries, isDirty])
  const dirtyByGroup = useMemo(() => {
    const m: Record<string, number> = {}
    for (const e of entries) if (isDirty(e)) m[e.group] = (m[e.group] || 0) + 1
    return m
  }, [entries, isDirty])

  const q = query.trim().toLowerCase()
  const matches = useCallback((e: SettingEntry) =>
    !q || e.label.toLowerCase().includes(q) || e.key.toLowerCase().includes(q) || e.help.toLowerCase().includes(q),
  [q])

  // Con búsqueda activa, se ignora la pestaña y se muestran todos los grupos que
  // contengan alguna coincidencia; si no, solo el grupo activo (o todos).
  const visibleGroups = q
    ? groups.filter(g => entries.some(e => e.group === g && matches(e)))
    : activeGroup === ALL ? groups : groups.filter(g => g === activeGroup)

  // La pestaña de conexión del dashboard no es un grupo del .env: tiene su propia
  // UI (URL/token con sus botones). La búsqueda (sobre el .env) la sustituye.
  const showConnection = !q && activeGroup === CONN

  const save = () => {
    // Envía todos los no-secretos; los secretos solo si el usuario escribió algo.
    const updates: Record<string, Val> = {}
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
        if (r.status === 401) throw new Error('No autorizado: define el API Token en la pestaña «Conexión».')
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

  const discard = () => { setEdited(original); setStatus({ s: 'ready' }) }

  const tabBtn = (g: string, label: string) => {
    const active = q ? false : activeGroup === g
    const n = g === ALL ? dirtyKeys.length : dirtyByGroup[g] || 0
    return (
      <button
        key={g}
        onClick={() => { setActiveGroup(g); setQuery('') }}
        className={`px-3 py-1.5 text-sm rounded-full whitespace-nowrap transition-colors border ${
          active
            ? 'bg-blue-600 border-blue-500 text-white'
            : 'bg-gray-800 border-gray-700 text-gray-300 hover:bg-gray-700'
        }`}
      >
        {label}
        {n > 0 && (
          <span className="ml-1.5 text-[10px] px-1.5 py-0.5 rounded-full bg-amber-500 text-gray-900 font-bold align-middle">{n}</span>
        )}
      </button>
    )
  }

  return (
    <div>
      {!showConnection && (
        <>
          <div className="flex items-baseline justify-between flex-wrap gap-2 mb-1">
            <h2 className="text-lg font-semibold text-gray-200">Configuración del bot (<code>.env</code>)</h2>
            <div className="flex items-center gap-3 text-[11px] text-gray-400">
              <span className="flex items-center gap-1"><Hot /> se aplica sin reiniciar</span>
              <span className="flex items-center gap-1"><Restart /> requiere reiniciar</span>
            </div>
          </div>
          <p className="text-sm text-gray-400 mb-4">
            Las contraseñas y tokens solo se guardan si escribes un valor nuevo.
          </p>
        </>
      )}

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

          {/* Buscador + navegación por grupos */}
          <div className="mb-5 space-y-3">
            <div className="relative max-w-sm">
              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500 text-sm">🔍</span>
              <input
                type="text"
                value={query}
                onChange={e => setQuery(e.target.value)}
                placeholder="Buscar ajuste (nombre, clave o descripción)…"
                className="w-full pl-9 pr-8 py-2 rounded-lg bg-gray-900 border border-gray-700 text-white text-sm
                           focus:outline-none focus:border-blue-500"
              />
              {query && (
                <button
                  onClick={() => setQuery('')}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300 text-lg leading-none"
                  aria-label="Limpiar búsqueda"
                >×</button>
              )}
            </div>
            {!q && (
              <div className="flex flex-wrap gap-2">
                {tabBtn(CONN, 'Conexión')}
                {groups.map(g => tabBtn(g, g))}
                {tabBtn(ALL, 'Todos')}
              </div>
            )}
          </div>

          {/* Contenido: la pestaña Conexión usa su propia UI; el resto, tarjetas */}
          {showConnection ? (
            <ConnectionSettings />
          ) : (
            <div className="space-y-6">
              {visibleGroups.map(group => {
                // En el grupo "Asistente", el proveedor y el modelo se eligen con un
                // desplegable (como en agentes/mesa), no con dos campos de texto: se
                // ocultan del grid y se muestra el selector arriba.
                const isAssistant = group === 'Asistente'
                const fields = entries.filter(e =>
                  e.group === group && matches(e) &&
                  !(isAssistant && ASSISTANT_MODEL_KEYS.includes(e.key)))
                const showAssistantSelector = isAssistant && (
                  !q || entries.some(e => ASSISTANT_MODEL_KEYS.includes(e.key) && matches(e)))
                if (fields.length === 0 && !showAssistantSelector) return null
                const provEntry = entries.find(e => e.key === 'ASSISTANT_PROVIDER')
                const modelEntry = entries.find(e => e.key === 'ASSISTANT_MODEL')
                const assistantDirty =
                  (provEntry ? isDirty(provEntry) : false) || (modelEntry ? isDirty(modelEntry) : false)
                return (
                  <div key={group} className="bg-gray-800 rounded-lg border border-gray-700 p-5">
                    <h3 className="text-sm font-bold text-cyan-300 uppercase tracking-wide mb-4">{group}</h3>
                    {showAssistantSelector && (
                      <AssistantModelSelect
                        models={models}
                        provider={String(edited.ASSISTANT_PROVIDER || '') || 'gemini'}
                        model={String(edited.ASSISTANT_MODEL || '')}
                        dirty={assistantDirty}
                        onChange={(p, m) => { setVal('ASSISTANT_PROVIDER', p); setVal('ASSISTANT_MODEL', m) }}
                      />
                    )}
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-x-8 gap-y-5">
                      {fields.map(e => (
                        <Field key={e.key} entry={e} value={edited[e.key]} dirty={isDirty(e)} onChange={v => setVal(e.key, v)} />
                      ))}
                    </div>
                  </div>
                )
              })}
              {q && visibleGroups.length === 0 && (
                <div className="bg-gray-800 text-gray-400 p-8 rounded text-center text-sm">
                  Ningún ajuste coincide con «{query}».
                </div>
              )}
            </div>
          )}
        </>
      )}

      {/* Barra de guardado pegajosa (solo para los ajustes del .env, no Conexión) */}
      {entries.length > 0 && !showConnection && (
        <div className="sticky bottom-0 mt-6 -mx-4 sm:-mx-8 px-4 sm:px-8 py-3 bg-gray-900/95 backdrop-blur border-t border-gray-700
                        flex items-center justify-between gap-3 z-10">
          <span className="text-sm text-gray-400">
            {dirtyKeys.length === 0
              ? 'Sin cambios pendientes'
              : <><span className="text-amber-300 font-semibold">{dirtyKeys.length}</span> cambio(s) sin guardar</>}
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={discard}
              disabled={status.s === 'saving' || dirtyKeys.length === 0}
              className="px-3 py-2 text-sm rounded bg-gray-700 hover:bg-gray-600 disabled:opacity-40"
            >
              Descartar
            </button>
            <button
              onClick={save}
              disabled={status.s === 'saving' || dirtyKeys.length === 0}
              className="px-4 py-2 text-sm rounded bg-blue-600 hover:bg-blue-500 disabled:opacity-40"
            >
              {status.s === 'saving' ? 'Guardando…' : 'Guardar ajustes'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// Selector de proveedor/modelo del asistente. Reusa el catálogo de /api/models
// (mismo que el de agentes y mesa): lista plana "provider/model" y solo muestra
// los proveedores que tienen su API key configurada en el .env, así no hay que
// reescribir la clave. Escribe en ASSISTANT_PROVIDER/ASSISTANT_MODEL del .env por
// el flujo normal de guardado (se aplica en caliente; el asistente relee el env).
function AssistantModelSelect({
  models, provider, model, dirty, onChange,
}: {
  models: Record<string, string[]>
  provider: string
  model: string
  dirty: boolean
  onChange: (provider: string, model: string) => void
}) {
  const current = model ? `${provider}/${model}` : ''
  const options = Object.entries(models).flatMap(([prov, list]) => list.map(m => `${prov}/${m}`))
  // El modelo activo puede no estar en la lista (p. ej. clave retirada): lo añadimos.
  if (current && !options.includes(current)) options.unshift(current)

  return (
    <div className={`pl-3 mb-5 border-l-2 ${dirty ? 'border-amber-400' : 'border-transparent'}`}>
      <div className="flex items-center gap-2 flex-wrap">
        <label className="text-sm font-medium text-gray-200">Proveedor y modelo del asistente</label>
        {dirty && <span className="w-1.5 h-1.5 rounded-full bg-amber-400" title="Modificado" />}
        <code className="text-[10px] text-gray-500">ASSISTANT_PROVIDER / ASSISTANT_MODEL</code>
        <Hot />
      </div>
      <select
        value={current}
        onChange={e => {
          const [p, ...rest] = e.target.value.split('/')
          onChange(p, rest.join('/'))
        }}
        className="w-full max-w-md mt-1 px-3 py-2 rounded bg-gray-900 border border-gray-600 text-white text-sm
                   focus:outline-none focus:border-blue-500"
      >
        {options.length === 0 && <option value="">(sin proveedores: configura una API key abajo)</option>}
        {current === '' && options.length > 0 && <option value="">— elige modelo —</option>}
        {options.map(opt => (
          <option key={opt} value={opt}>{opt.toUpperCase()}</option>
        ))}
      </select>
      <p className="text-xs text-gray-500 mt-0.5">
        Igual que el selector de agentes y mesa: solo aparecen los proveedores con su API key ya
        configurada (abajo). La clave se coge del <code>.env</code>; no hace falta reescribirla.
      </p>
    </div>
  )
}

function Hot() {
  return <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-900 text-emerald-200 align-middle">en caliente</span>
}

function Restart() {
  return <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-700 text-gray-400 align-middle">reinicio</span>
}

function Field({
  entry, value, dirty, onChange,
}: {
  entry: SettingEntry
  value: Val
  dirty: boolean
  onChange: (v: Val) => void
}) {
  const labelRow = (
    <div className="flex items-center gap-2 flex-wrap">
      <label className="text-sm font-medium text-gray-200">{entry.label}</label>
      {dirty && <span className="w-1.5 h-1.5 rounded-full bg-amber-400" title="Modificado" />}
      <code className="text-[10px] text-gray-500">{entry.key}</code>
      {entry.hot ? <Hot /> : <Restart />}
    </div>
  )

  // Borde izquierdo ámbar cuando el campo está modificado.
  const wrap = `pl-3 border-l-2 ${dirty ? 'border-amber-400' : 'border-transparent'}`

  if (entry.type === 'bool') {
    return (
      <div className={`${wrap} flex items-start justify-between gap-4`}>
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
    <div className={wrap}>
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
