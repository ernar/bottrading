import { useEffect, useRef, useState, type MouseEvent } from 'react'
import { formatBrokerShort, brokerToDisplayMs, priceDecimals } from '../utils/format'
import type { Candle, SignalMarker, Position } from '../types/bot'

interface Props {
  candles: Candle[]
  symbol: string
  signals?: SignalMarker[]
  position?: Position | null
  height?: number
  showSignals?: boolean
  showPosition?: boolean
}

const HOUR_MS = 3600 * 1000
const UP = '#34d399'   // emerald-400
const DOWN = '#f87171' // red-400

// Gráfico de velas (candlestick) en SVG puro, sin librería externa (mismo enfoque
// que PortfolioChart). Eje X equidistante por vela (los huecos de fin de semana
// no deforman ni solapan las velas) y eje Y de precio. Encima dibuja, de forma
// toggleable, los marcadores de señales (▲/▼ en su precio/hora) y la posición
// abierta (líneas de entrada, SL y TP). Las marcas temporales se llevan todas a
// brokerToDisplayMs, igual que el resto del dashboard, para alinear con las velas.
export function CandlestickChart({
  candles, symbol, signals = [], position = null,
  height = 420, showSignals = true, showPosition = true,
}: Props) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)
  const [hover, setHover] = useState<number | null>(null)

  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const ro = new ResizeObserver(entries => setWidth(entries[0].contentRect.width))
    ro.observe(el)
    setWidth(el.clientWidth)
    return () => ro.disconnect()
  }, [])

  const data = Array.isArray(candles) ? candles : []
  const n = data.length
  const dec = priceDecimals(symbol)
  const fmtPrice = (p: number) => p.toFixed(dec)

  // Sin ancho medido aún o sin datos: placeholder (no reventamos el render).
  if (width === 0 || n === 0) {
    return (
      <div ref={wrapRef} className="w-full bg-gray-900 rounded-lg border border-gray-700"
        style={{ height }}>
        <div className="h-full flex items-center justify-center text-gray-600 text-sm">
          {n === 0 ? 'Sin datos de velas (mercado cerrado o EA desconectado).' : ''}
        </div>
      </div>
    )
  }

  // Geometría.
  const mL = 8 + 9 * (dec + 4)   // margen izq. proporcional al nº de dígitos del precio
  const mR = 64                  // margen der. para etiquetas de líneas de posición
  const mT = 12
  const mB = 24
  const plotW = Math.max(1, width - mL - mR)
  const plotH = Math.max(1, height - mT - mB)
  const slot = plotW / n
  const bodyW = Math.max(1, Math.min(slot * 0.7, 18))

  const times = data.map(c => brokerToDisplayMs(c.t))
  const firstT = times[0]
  const lastT = times[n - 1]

  // Dominio vertical: extremos de las velas + precios de referencia visibles
  // (entrada/SL/TP de la posición) para que esas líneas no queden fuera de cuadro.
  const refPrices: number[] = []
  if (showPosition && position && position.symbol === symbol) {
    if (Number.isFinite(position.open_price)) refPrices.push(position.open_price)
    if (position.stop_loss && Number.isFinite(position.stop_loss)) refPrices.push(position.stop_loss)
    if (position.take_profit && Number.isFinite(position.take_profit)) refPrices.push(position.take_profit)
  }
  const lows = data.map(c => c.low).concat(refPrices)
  const highs = data.map(c => c.high).concat(refPrices)
  const dataMin = Math.min(...lows)
  const dataMax = Math.max(...highs)
  const pad = (dataMax - dataMin) * 0.06 || Math.abs(dataMax) * 0.001 || 1
  const min = dataMin - pad
  const max = dataMax + pad
  const range = max - min || 1

  const x = (i: number) => mL + (i + 0.5) * slot
  const y = (price: number) => mT + (1 - (price - min) / range) * plotH

  // Vela más cercana (por tiempo) a una marca ms — para anclar señales/posición.
  const nearestIdx = (ms: number) => {
    let best = 0, bd = Infinity
    for (let i = 0; i < n; i++) {
      const d = Math.abs(times[i] - ms)
      if (d < bd) { bd = d; best = i }
    }
    return best
  }

  // Ticks del eje Y (precio) e índices etiquetados del eje X (tiempo).
  const yTicks = 5
  const tickVals = Array.from({ length: yTicks }, (_, i) => min + (range * i) / (yTicks - 1))
  const xStep = Math.max(1, Math.ceil(n / 6))

  // Señales dentro de la ventana visible (con 1h de tolerancia en los bordes).
  const visibleSignals = (showSignals ? signals : [])
    .map(s => ({ s, ms: brokerToDisplayMs(s.t) }))
    .filter(({ ms, s }) => Number.isFinite(ms) && Number.isFinite(s.price)
      && ms >= firstT - HOUR_MS && ms <= lastT + HOUR_MS)

  const pos = showPosition && position && position.symbol === symbol ? position : null
  const posColor = pos ? (pos.profit >= 0 ? UP : DOWN) : UP

  const onMove = (e: MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const mx = e.clientX - rect.left
    const i = Math.round((mx - mL) / slot - 0.5)
    setHover(i >= 0 && i < n ? i : null)
  }

  const hc = hover != null ? data[hover] : null

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-700 overflow-hidden">
      <div ref={wrapRef} className="w-full relative" style={{ height }}>
        <svg width={width} height={height} className="block"
          onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
          {/* Rejilla + etiquetas de precio */}
          {tickVals.map((v, i) => (
            <g key={`y${i}`}>
              <line x1={mL} y1={y(v)} x2={mL + plotW} y2={y(v)}
                stroke="#1f2937" strokeWidth={1} />
              <text x={mL - 6} y={y(v) + 3} textAnchor="end"
                fontSize={10} fill="#6b7280">{fmtPrice(v)}</text>
            </g>
          ))}

          {/* Etiquetas de tiempo */}
          {data.map((c, i) => (i % xStep === 0 ? (
            <text key={`x${i}`} x={x(i)} y={height - 8} textAnchor="middle"
              fontSize={10} fill="#6b7280">{formatBrokerShort(c.t)}</text>
          ) : null))}

          {/* Velas: mecha (high-low) + cuerpo (open-close) */}
          {data.map((c, i) => {
            const upBar = c.close >= c.open
            const col = upBar ? UP : DOWN
            const yHigh = y(c.high)
            const yLow = y(c.low)
            const yOpen = y(c.open)
            const yClose = y(c.close)
            const bodyTop = Math.min(yOpen, yClose)
            const bodyH = Math.max(1, Math.abs(yClose - yOpen))
            return (
              <g key={`c${i}`}>
                <line x1={x(i)} y1={yHigh} x2={x(i)} y2={yLow} stroke={col} strokeWidth={1} />
                <rect x={x(i) - bodyW / 2} y={bodyTop} width={bodyW} height={bodyH}
                  fill={col} />
              </g>
            )
          })}

          {/* Líneas de la posición abierta (entrada / SL / TP). Las etiquetas se
              anclan al borde derecho (textAnchor=end) para no salirse del lienzo. */}
          {pos && (
            <g>
              <line x1={mL} y1={y(pos.open_price)} x2={mL + plotW} y2={y(pos.open_price)}
                stroke={posColor} strokeWidth={1.5} strokeDasharray="5 3" />
              <text x={width - 4} y={y(pos.open_price) - 3} textAnchor="end"
                fontSize={10} fill={posColor}>
                {pos.direction} {pos.open_price.toFixed(dec)}
              </text>
              {pos.stop_loss && Number.isFinite(pos.stop_loss) && (
                <>
                  <line x1={mL} y1={y(pos.stop_loss)} x2={mL + plotW} y2={y(pos.stop_loss)}
                    stroke={DOWN} strokeWidth={1} strokeDasharray="2 3" />
                  <text x={width - 4} y={y(pos.stop_loss) - 3} textAnchor="end"
                    fontSize={9} fill={DOWN}>SL {pos.stop_loss.toFixed(dec)}</text>
                </>
              )}
              {pos.take_profit && Number.isFinite(pos.take_profit) && (
                <>
                  <line x1={mL} y1={y(pos.take_profit)} x2={mL + plotW} y2={y(pos.take_profit)}
                    stroke={UP} strokeWidth={1} strokeDasharray="2 3" />
                  <text x={width - 4} y={y(pos.take_profit) - 3} textAnchor="end"
                    fontSize={9} fill={UP}>TP {pos.take_profit.toFixed(dec)}</text>
                </>
              )}
            </g>
          )}

          {/* Marcadores de señales (▲ buy / ▼ sell) en su precio/hora */}
          {visibleSignals.map(({ s, ms }, k) => {
            const mx = x(nearestIdx(ms))
            const my = y(s.price)
            const col = s.action === 'buy' ? UP : DOWN
            const sz = 5
            const path = s.action === 'buy'
              ? `M${mx},${my - sz} L${mx - sz},${my + sz} L${mx + sz},${my + sz} Z`
              : `M${mx},${my + sz} L${mx - sz},${my - sz} L${mx + sz},${my - sz} Z`
            const conf = s.confidence != null ? ` · ${(s.confidence * 100).toFixed(0)}%` : ''
            return (
              <path key={`s${k}`} d={path} fill={col} stroke="#0b0f17" strokeWidth={1}>
                <title>{`${s.action.toUpperCase()} @ ${s.price.toFixed(dec)}${conf}\n${formatBrokerShort(s.t)}${s.reason ? `\n${s.reason}` : ''}`}</title>
              </path>
            )
          })}

          {/* Crosshair vertical de hover */}
          {hover != null && (
            <line x1={x(hover)} y1={mT} x2={x(hover)} y2={mT + plotH}
              stroke="#64748b" strokeWidth={1} strokeDasharray="3 3" />
          )}
        </svg>

        {/* Tooltip OHLC de la vela bajo el ratón */}
        {hc && (
          <div className="pointer-events-none absolute z-10 top-2 left-2 rounded-md border border-gray-700 bg-gray-900/95 px-2 py-1 text-xs shadow-lg">
            <div className="text-gray-400">{formatBrokerShort(hc.t)}</div>
            <div className="flex gap-2">
              <span className="text-gray-500">O</span><span className="text-white">{fmtPrice(hc.open)}</span>
              <span className="text-gray-500">H</span><span className="text-white">{fmtPrice(hc.high)}</span>
            </div>
            <div className="flex gap-2">
              <span className="text-gray-500">L</span><span className="text-white">{fmtPrice(hc.low)}</span>
              <span className="text-gray-500">C</span>
              <span className={hc.close >= hc.open ? 'text-emerald-400' : 'text-red-400'}>{fmtPrice(hc.close)}</span>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
