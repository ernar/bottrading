import { useEffect, useRef, useState } from 'react'

export interface EquityPoint {
  t: string
  equity: number
  balance?: number
}

interface Props {
  points: EquityPoint[]
  height?: number
}

// Gráfico de evolución de la cartera: línea verde luminosa sobre fondo oscuro,
// estilo "terminal". SVG puro (sin librería) para controlar el efecto glow.
// Mide su ancho con ResizeObserver y dibuja en píxeles para que el grosor del
// trazo y el resplandor no se deformen al estirar el contenedor.
export function PortfolioChart({ points, height = 220 }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)
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

  const values = points.map(p => p.equity)
  const first = values[0] ?? 0
  const last = values[values.length - 1] ?? 0
  const change = last - first
  const changePct = first > 0 ? (change / first) * 100 : 0
  const up = change >= 0
  const stroke = up ? '#34d399' : '#f87171' // emerald-400 / red-400

  // Geometría del trazo en píxeles.
  const padTop = 12
  const padBottom = 12
  const min = values.length ? Math.min(...values) : 0
  const max = values.length ? Math.max(...values) : 1
  const range = max - min || 1
  const innerH = height - padTop - padBottom

  const xy = (i: number) => {
    const x = values.length > 1 ? (i / (values.length - 1)) * width : 0
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

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-700 overflow-hidden">
      <div className="flex items-center justify-between px-4 pt-3 text-xs sm:text-sm">
        <div className="flex items-baseline gap-2">
          <span className="text-gray-400 uppercase tracking-wide">Equity</span>
          <span className="font-semibold text-white">{fmt(last)}</span>
        </div>
        <div className={up ? 'text-emerald-400' : 'text-red-400'}>
          {up ? '▲' : '▼'} {signed(change)} ({up ? '+' : ''}{changePct.toFixed(2)}%)
        </div>
      </div>

      <div ref={wrapRef} className="w-full" style={{ height }}>
        {values.length < 2 ? (
          <div className="h-full flex items-center justify-center text-gray-600 text-sm">
            Acumulando datos de la cartera…
          </div>
        ) : (
          <svg width={width} height={height} className="block">
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
          </svg>
        )}
      </div>
    </div>
  )
}
