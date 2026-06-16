import { ReactNode, useMemo, useState } from 'react'

// Toolkit de filtrado + ordenación por columna desde la cabecera de cualquier tabla.
//
// - Columnas NO numéricas (símbolo, acción, riesgo, texto, fechas): se filtran con
//   un <FilterText> o <FilterSelect> cableado a `setFilter` (coincidencia de
//   subcadena, sin distinguir mayúsculas; para selects el valor es exacto).
// - Columnas NUMÉRICAS (precio, volumen, P&L, confianza…): NO se filtran por
//   importe; se ORDENAN haciendo clic en su cabecera (<SortHeader>), alternando
//   mayor→menor / menor→mayor. Se declaran en `opts.sort` (clave -> valor numérico).

export type Accessor<T> = (row: T) => unknown
export type Accessors<T> = Record<string, Accessor<T>>
export type SortDir = 'asc' | 'desc'

export interface UseTableOpts<T> {
  exact?: string[]                                   // columnas con coincidencia exacta (selects)
  sort?: Record<string, (row: T) => number | string> // columnas ordenables (valor de orden)
  initialSort?: { key: string; dir: SortDir }
}

export interface TableState<T> {
  filters: Record<string, string>
  setFilter: (key: string, value: string) => void
  clear: () => void
  active: boolean
  filtered: T[]
  sortKey: string | null
  sortDir: SortDir
  toggleSort: (key: string) => void
  isSortable: (key: string) => boolean
}

export function useTableFilter<T>(rows: T[], accessors: Accessors<T>, opts: UseTableOpts<T> = {}): TableState<T> {
  const { exact = [], sort = {}, initialSort } = opts
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [sortKey, setSortKey] = useState<string | null>(initialSort?.key ?? null)
  const [sortDir, setSortDir] = useState<SortDir>(initialSort?.dir ?? 'desc')
  const exactSet = useMemo(() => new Set(exact), [exact.join('|')]) // eslint-disable-line react-hooks/exhaustive-deps

  const setFilter = (key: string, value: string) =>
    setFilters(prev => ({ ...prev, [key]: value }))
  const clear = () => setFilters({})

  const isSortable = (key: string) => !!sort[key]
  const toggleSort = (key: string) => {
    if (!sort[key]) return
    if (sortKey === key) setSortDir(d => (d === 'asc' ? 'desc' : 'asc'))
    else { setSortKey(key); setSortDir('desc') }  // primer clic: mayor → menor
  }

  const activeEntries = Object.entries(filters).filter(([, v]) => v != null && v.trim() !== '')
  const active = activeEntries.length > 0

  const filtered = useMemo(() => {
    let out = rows
    if (activeEntries.length > 0) {
      out = rows.filter(row =>
        activeEntries.every(([key, raw]) => {
          const acc = accessors[key]
          if (!acc) return true
          const cell = String(acc(row) ?? '').toLowerCase()
          const needle = raw.trim().toLowerCase()
          return exactSet.has(key) ? cell === needle : cell.includes(needle)
        })
      )
    }
    const sacc = sortKey ? sort[sortKey] : null
    if (sacc) {
      const dir = sortDir === 'asc' ? 1 : -1
      out = [...out].sort((a, b) => {
        const va = sacc(a), vb = sacc(b)
        if (typeof va === 'number' && typeof vb === 'number') {
          const na = Number.isFinite(va) ? va : -Infinity
          const nb = Number.isFinite(vb) ? vb : -Infinity
          return (na - nb) * dir
        }
        return String(va).localeCompare(String(vb), undefined, { numeric: true }) * dir
      })
    }
    return out
    // accessors/sort son estables por tabla; dependen de rows + filters + orden.
  }, [rows, filters, sortKey, sortDir]) // eslint-disable-line react-hooks/exhaustive-deps

  return { filters, setFilter, clear, active, filtered, sortKey, sortDir, toggleSort, isSortable }
}

const inputCls =
  'w-full px-2 py-1 rounded bg-gray-900/70 border border-gray-700 text-xs font-normal text-gray-200 ' +
  'placeholder-gray-600 focus:outline-none focus:border-blue-500'

// Input de texto para filtrar una columna desde su cabecera.
export function FilterText({
  value, onChange, placeholder = 'filtrar…',
}: {
  value: string | undefined
  onChange: (v: string) => void
  placeholder?: string
}) {
  return (
    <input
      type="text"
      value={value ?? ''}
      onChange={e => onChange(e.target.value)}
      onClick={e => e.stopPropagation()}
      placeholder={placeholder}
      className={inputCls}
    />
  )
}

// Desplegable para columnas de baja cardinalidad (acción, símbolo, riesgo…).
export function FilterSelect({
  value, onChange, options, allLabel = 'Todos',
}: {
  value: string | undefined
  onChange: (v: string) => void
  options: string[]
  allLabel?: string
}) {
  return (
    <select
      value={value ?? ''}
      onChange={e => onChange(e.target.value)}
      onClick={e => e.stopPropagation()}
      className={inputCls}
    >
      <option value="">{allLabel}</option>
      {options.map(o => <option key={o} value={o}>{o}</option>)}
    </select>
  )
}

// Cabecera clicable para ordenar una columna numérica (mayor→menor / menor→mayor).
export function SortHeader({
  label, colKey, sortKey, dir, onClick, align = 'left', className = 'px-4 py-3',
}: {
  label: ReactNode
  colKey: string
  sortKey: string | null
  dir: SortDir
  onClick: (key: string) => void
  align?: 'left' | 'right'
  className?: string
}) {
  const activeCol = sortKey === colKey
  return (
    <th
      onClick={() => onClick(colKey)}
      title="Ordenar por esta columna"
      className={`${className} cursor-pointer select-none hover:text-gray-200 ${align === 'right' ? 'text-right' : 'text-left'} ${activeCol ? 'text-cyan-300' : ''}`}
    >
      {label}
      <span className="ml-1 text-xs opacity-70">{activeCol ? (dir === 'asc' ? '▲' : '▼') : '↕'}</span>
    </th>
  )
}

// Opciones únicas (ordenadas) de una columna, para alimentar un <FilterSelect>.
export function uniqueOptions<T>(rows: T[], accessor: Accessor<T>): string[] {
  const set = new Set<string>()
  for (const r of rows) {
    const v = String(accessor(r) ?? '').trim()
    if (v) set.add(v)
  }
  return Array.from(set).sort((a, b) => a.localeCompare(b, undefined, { numeric: true }))
}

// Barra fina sobre la tabla: "N de M" + botón Limpiar, visible solo al filtrar.
export function FilterBar({
  active, shown, total, onClear,
}: {
  active: boolean
  shown: number
  total: number
  onClear: () => void
}) {
  if (!active) return null
  return (
    <div className="flex items-center justify-end gap-3 mb-2 text-xs text-gray-400">
      <span>Mostrando <span className="text-gray-200">{shown}</span> de {total}</span>
      <button onClick={onClear} className="px-2 py-1 rounded border border-gray-600 hover:bg-gray-700">
        Limpiar filtros
      </button>
    </div>
  )
}
