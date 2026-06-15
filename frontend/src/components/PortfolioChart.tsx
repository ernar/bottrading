import { useEffect, useRef, useState, type MouseEvent } from 'react'

export interface EquityPoint {
  t: string
  equity: number
  balance?: number
}

interface Props {
  points: EquityPoint[]
  height?: number
  // Texto del rango temporal activo (p. ej. "últimas 1h") para aclarar a qué
  // periodo se refiere el cambio mostrado.
  rangeLabel?: string
  // Campo de la cartera a dibujar: equity (incluye P/L flotante) o balance
  // (solo operaciones cerradas). Por defecto equity.
  field?: 'equity' | 'balance'
  // Etiqueta de la cabecera (por defecto, derivada del campo).
  title?: string
}

// Gráfico de evolución de la cartera: línea verde luminosa sobre fondo oscuro,
// estilo "terminal". SVG puro (sin librería) para controlar el efecto glow.
// Mide su ancho con ResizeObserver y dibuja en píxeles para que el grosor del
// trazo y el resplandor no se deformen al estirar el contenedor.
export function PortfolioChart({ points, height = 220, rangeLabel, field = 'equity', title }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)
  // Índice del punto bajo el ratón (null = sin hover) para el tooltip/crosshair.
  const [hover, setHover] = useState<number | null>(null)
  // id único por instancia para no colisionar los <defs> si hubiera varios.
  const gid = useRef(`pc-${Math.random().toString(36).slice(2)}`).current

  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const ro = new ResizeObserver(entries => {
      setWidth(entries[0].contentRect.width)
    })
    ro.observe(el)
    setWidth(el.clientWidth)
    return () => ro.disconnect()
  }, [])

  // Defensa: si la API devolviera algo que no es un array (backend viejo, error
  // parseado como JSON…), no reventamos el render. Además, solo conservamos los
  // puntos con el campo elegido finito (p. ej. el punto en vivo de balance puede
  // faltar si aún no hay account_info).
  const data = (Array.isArray(points) ? points : [])
    .filter(p => Number.isFinite(Number(p[field])))
  const values = data.map(p => Number(p[field]))
  const heading = title ?? (field === 'balance' ? 'Balance' : 'Equity')
  const first = values[0] ?? 0
  const last = values[values.length - 1] ?? 0
  const change = last - first
  const changePct = first > 0 ? (change / first) * 100 : 0
  const up = change >= 0
  const stroke = up ? '#34d399' : '#f87171' // emerald-400 / red-400

  // Geometría del trazo en píxeles.
  const padTop = 12
  const padBottom = 12
  const innerH = height - padTop - padBottom

  // Dominio vertical con SPAN MÍNIMO: si se escala al mín/máx exactos, una
  // micro-oscilación del P/L flotante (±$1 en una cuenta pequeña) llenaría todo
  // el alto y parecería un pico irreal. Forzamos un span mínimo (~6% del valor
  // medio) y añadimos margen, para que los cambios pequeños se vean pequeños.
  const dataMin = values.length ? Math.min(...values) : 0
  const dataMax = values.length ? Math.max(...values) : 1
  const mid = (dataMin + dataMax) / 2
  const minSpan = Math.max(Math.abs(mid) * 0.06, 0.01)
  const span = Math.max(dataMax - dataMin, minSpan)
  const pad = span * 0.15
  const min = mid - span / 2 - pad
  const max = mid + span / 2 + pad
  const range = max - min || 1

  // Eje X proporcional al TIEMPO real (no al índice): si el bot se detuvo y
  // reanudó, los huecos temporales deben verse como huecos, no como tramos
  // equiespaciados que deforman la curva. Fallback a índice si no hay timestamps.
  const times = data.map(p => {
    if (p.t === 'now') return Date.now()
    const ms = new Date(p.t.replace(' ', 'T')).getTime()
    return Number.isFinite(ms) ? ms : NaN
  })
  const tValid = times.every(t => Number.isFinite(t))
  const t0 = tValid ? times[0] : 0
  const tSpan = tValid ? (times[times.length - 1] - t0) || 1 : 1

  const xy = (i: number) => {
    let frac: number
    if (values.length <= 1) frac = 0
    else if (tValid) frac = (times[i] - t0) / tSpan
    else frac = i / (values.length - 1)
    const x = frac * width
    const y = padTop + (1 - (values[i] - min) / range) * innerH
    return [x, y] as const
  }

  const linePath = values.map((_, i) => {
    const [x, y] = xy(i)
    return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')

  // Área bajo la curva para el degradado de relleno.
  const areaPath = values.length
    ? `${linePath} L${width.toFixed(1)},${height} L0,${height} Z`
    : ''

  const fmt = (n: number) =>
    `$${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
  // Money con signo delante del símbolo: +$50.00 / -$50.00.
  const signed = (n: number) => `${n >= 0 ? '+' : '-'}${fmt(Math.abs(n))}`
  // Fecha legible de un punto para el tooltip ("now" = momento actual).
  const fmtDate = (i: number) => {
    const t = times[i]
    if (!Number.isFinite(t)) return ''
    return new Date(t).toLocaleString('es-ES', {
      day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
    })
  }

  // Localiza el punto cuyo X en píxeles es el más cercano a la posición del
  // ratón. Buscar por X (no por índice) respeta el eje temporal proporcional.
  const onMove = (e: MouseEvent<SVGSVGElement>) => {
    if (values.length < 2) return
    const rect = e.currentTarget.getBoundingClientRect()
    const mx = e.clientX - rect.left
    let best = 0
    let bestDist = Infinity
    for (let i = 0; i < values.length; i++) {
      const d = Math.abs(xy(i)[0] - mx)
      if (d < bestDist) { bestDist = d; best = i }
    }
    setHover(best)
  }

  const hoverPt = hover != null && hover < values.length ? xy(hover) : null
  // Mantiene el tooltip dentro del ancho del gráfico (se voltea cerca del borde).
  const tipW = 150
  const tipLeft = hoverPt
    ? Math.min(Math.max(hoverPt[0] - tipW / 2, 4), Math.max(width - tipW - 4, 4))
    : 0

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-700 overflow-hidden">
      <div className="flex items-center justify-between px-4 pt-3 text-xs sm:text-sm">
        <div className="flex items-baseline gap-2">
          <span className="text-gray-400 uppercase tracking-wide">{heading}</span>
          <span className="font-semibold text-white">{fmt(last)}</span>
        </div>
        <div className="flex items-baseline gap-2">
          {rangeLabel && (
            <span className="text-gray-500 normal-case tracking-normal">{rangeLabel}</span>
          )}
          <span className={up ? 'text-emerald-400' : 'text-red-400'}>
            {up ? '▲' : '▼'} {signed(change)} ({up ? '+' : ''}{changePct.toFixed(2)}%)
          </span>
        </div>
      </div>

      <div ref={wrapRef} className="w-full relative" style={{ height }}>
        {values.length < 2 ? (
          <div className="h-full flex items-center justify-center text-gray-600 text-sm">
            Acumulando datos de la cartera…
          </div>
        ) : (
          <svg
            width={width}
            height={height}
            className="block"
            onMouseMove={onMove}
            onMouseLeave={() => setHover(null)}
          >
            <defs>
              {/* Resplandor: difumina una copia del trazo bajo el trazo nítido. */}
              <filter id={`${gid}-glow`} x="-20%" y="-50%" width="140%" height="200%">
                <feGaussianBlur stdDeviation="4" result="blur" />
                <feMerge>
                  <feMergeNode in="blur" />
                  <feMergeNode in="SourceGraphic" />
                </feMerge>
              </filter>
              <linearGradient id={`${gid}-fill`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={stroke} stopOpacity="0.25" />
                <stop offset="100%" stopColor={stroke} stopOpacity="0" />
              </linearGradient>
            </defs>

            <path d={areaPath} fill={`url(#${gid}-fill)`} />
            <path
              d={linePath}
              fill="none"
              stroke={stroke}
              strokeWidth={2}
              strokeLinejoin="round"
              strokeLinecap="round"
              filter={`url(#${gid}-glow)`}
            />
            {hoverPt && (
              <g>
                {/* Línea vertical de cruce + punto resaltado en el dato. */}
                <line
                  x1={hoverPt[0]} y1={padTop} x2={hoverPt[0]} y2={height}
                  stroke="#64748b" strokeWidth={1} strokeDasharray="3 3"
                />
                <circle cx={hoverPt[0]} cy={hoverPt[1]} r={4} fill={stroke}
                  stroke="#0b0f17" strokeWidth={2} />
              </g>
            )}
          </svg>
        )}
        {hoverPt && hover != null && (
          <div
            className="pointer-events-none absolute z-10 rounded-md border border-gray-700 bg-gray-900/95 px-2 py-1 text-xs shadow-lg"
            style={{ left: tipLeft, top: 6, width: tipW }}
          >
            <div className="text-gray-400">{fmtDate(hover)}</div>
            <div className="font-semibold text-white">{fmt(values[hover])}</div>
          </div>
        )}
      </div>
    </div>
  )
}
