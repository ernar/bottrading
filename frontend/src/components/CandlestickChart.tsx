import { useEffect, useRef, useState, type MouseEvent } from 'react'
import { formatBrokerShort, brokerToDisplayMs, priceDecimals } from '../utils/format'
import type { Candle, SignalMarker, Position } from '../types/bot'

interface Props {
  candles: Candle[]
  symbol: string
  signals?: SignalMarker[]
  positions?: Position[]
  height?: number
  showSignals?: boolean
  showPosition?: boolean
}

const UP = '#34d399'   // emerald-400
const DOWN = '#f87171' // red-400
const MIN_VISIBLE = 12 // mínimo de velas visibles al hacer zoom

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(v, hi))

// Gráfico de velas (candlestick) en SVG puro, sin librería externa (mismo enfoque
// que PortfolioChart). Eje X equidistante por vela (los huecos de fin de semana
// no deforman ni solapan las velas) y eje Y de precio que se autoescala a lo
// VISIBLE. Encima dibuja, de forma toggleable, los marcadores de señales (▲/▼ en
// su precio/hora) y la posición abierta (líneas de entrada, SL y TP).
//
// ZOOM/PAN: el zoom es una "ventana" de velas [start, end). Se controla con la
// rueda del ratón (centrado en el cursor), arrastrando para desplazar (pan) y con
// los botones +/−/⤢. `span` = nº de velas visibles (null = todas); `offset` = nº
// de velas ocultas por la derecha (0 = pegado a la última). Ambos se derivan del n
// actual en cada render, así que sobreviven al refresco de datos (al llegar una
// vela nueva con offset 0, la vista sigue a la última).
export function CandlestickChart({
  candles, symbol, signals = [], positions = [],
  height = 420, showSignals = true, showPosition = true,
}: Props) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)
  const [hover, setHover] = useState<number | null>(null)
  // Estado de zoom/pan.
  const [span, setSpan] = useState<number | null>(null)
  const [offset, setOffset] = useState(0)
  const [dragging, setDragging] = useState(false)
  const dragRef = useRef({ x: 0, offset: 0 })
  // Espejo de la geometría para el listener nativo de rueda (attach único).
  const stateRef = useRef({ n: 0, mL: 0, plotW: 0, slot: 1, start: 0, count: 0 })

  // Medir ancho del contenedor (el nodo es estable: no hay early-return distinto).
  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const ro = new ResizeObserver(entries => setWidth(entries[0].contentRect.width))
    ro.observe(el)
    setWidth(el.clientWidth)
    return () => ro.disconnect()
  }, [])

  // Reset del zoom al cambiar de símbolo.
  useEffect(() => { setSpan(null); setOffset(0) }, [symbol])

  // Rueda = zoom centrado en el cursor. Listener NATIVO no pasivo (el onWheel de
  // React es pasivo y no deja preventDefault, que evita el scroll de la página).
  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      const st = stateRef.current
      if (!st.n) return
      e.preventDefault()
      const rect = el.getBoundingClientRect()
      const frac = clamp((e.clientX - rect.left - st.mL) / st.plotW, 0, 1)
      const idxCursor = st.start + frac * st.count
      const factor = e.deltaY < 0 ? 0.85 : 1 / 0.85
      const lb = Math.min(MIN_VISIBLE, st.n)
      const newCount = clamp(Math.round(st.count * factor), lb, st.n)
      const newStart = idxCursor - frac * newCount
      setSpan(newCount)
      setOffset(clamp(Math.round(st.n - (newStart + newCount)), 0, st.n - newCount))
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [])

  const data = Array.isArray(candles) ? candles : []
  const n = data.length
  const dec = priceDecimals(symbol)
  const fmtPrice = (p: number) => p.toFixed(dec)
  const ready = width > 0 && n > 0

  // Geometría.
  const mL = 8 + 9 * (dec + 4)   // margen izq. proporcional al nº de dígitos del precio
  const mR = 64                  // margen der. para etiquetas de líneas de posición
  const mT = 12
  const mB = 24
  const plotW = Math.max(1, width - mL - mR)
  const plotH = Math.max(1, height - mT - mB)

  // Ventana visible [start, end) derivada de span/offset y el n actual.
  const lowerBound = Math.min(MIN_VISIBLE, n || MIN_VISIBLE)
  const count = span == null ? n : clamp(span, lowerBound, n || 1)
  const maxOffset = Math.max(0, n - count)
  const off = clamp(offset, 0, maxOffset)
  const end = n - off
  const start = Math.max(0, end - count)
  const view = data.slice(start, end)

  const slot = plotW / Math.max(1, count)
  const bodyW = Math.max(1, Math.min(slot * 0.7, 18))

  const times = data.map(c => brokerToDisplayMs(c.t))

  // Posiciones abiertas del símbolo (TODAS: puede haber varias por pirámide).
  const posList = showPosition ? positions.filter(p => p.symbol === symbol) : []

  // Dominio vertical: extremos de las velas VISIBLES + precios de referencia
  // (entrada/SL/TP de cada posición) para que esas líneas no queden fuera de cuadro.
  const refPrices: number[] = []
  posList.forEach(p => {
    if (Number.isFinite(p.open_price)) refPrices.push(p.open_price)
    if (p.stop_loss && Number.isFinite(p.stop_loss)) refPrices.push(p.stop_loss)
    if (p.take_profit && Number.isFinite(p.take_profit)) refPrices.push(p.take_profit)
  })
  const lows = view.map(c => c.low).concat(refPrices)
  const highs = view.map(c => c.high).concat(refPrices)
  const dataMin = lows.length ? Math.min(...lows) : 0
  const dataMax = highs.length ? Math.max(...highs) : 1
  const pad = (dataMax - dataMin) * 0.06 || Math.abs(dataMax) * 0.001 || 1
  const min = dataMin - pad
  const max = dataMax + pad
  const range = max - min || 1

  // x() recibe índice ABSOLUTO de vela (relativo a `start`); y() un precio.
  const x = (i: number) => mL + (i - start + 0.5) * slot
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

  // Espejo para el listener de rueda (siempre con la geometría vigente).
  stateRef.current = ready
    ? { n, mL, plotW, slot, start, count }
    : { n: 0, mL, plotW, slot, start, count }

  const yTicks = 5
  const tickVals = Array.from({ length: yTicks }, (_, i) => min + (range * i) / (yTicks - 1))
  const xStep = Math.max(1, Math.ceil(count / 6))

  // Señales cuya vela más cercana cae dentro de la ventana visible.
  const visibleSignals = (showSignals ? signals : [])
    .map(s => ({ s, idx: nearestIdx(brokerToDisplayMs(s.t)) }))
    .filter(({ s, idx }) => Number.isFinite(s.price) && idx >= start && idx < end)

  const onDown = (e: MouseEvent<SVGSVGElement>) => {
    dragRef.current = { x: e.clientX, offset: off }
    setDragging(true)
  }

  const onMove = (e: MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    if (dragging) {
      // Pan: arrastrar a la derecha revela velas más antiguas (sube el offset).
      const dCandles = Math.round((e.clientX - dragRef.current.x) / slot)
      setOffset(clamp(dragRef.current.offset + dCandles, 0, maxOffset))
      setHover(null)
      return
    }
    const i = start + Math.round((e.clientX - rect.left - mL) / slot - 0.5)
    setHover(i >= start && i < end ? i : null)
  }

  const endDrag = () => setDragging(false)

  // Zoom centrado (botones +/−). factor<1 acerca, >1 aleja.
  const zoomCenter = (factor: number) => {
    const st = stateRef.current
    if (!st.n) return
    const frac = 0.5
    const idxCursor = st.start + frac * st.count
    const lb = Math.min(MIN_VISIBLE, st.n)
    const newCount = clamp(Math.round(st.count * factor), lb, st.n)
    const newStart = idxCursor - frac * newCount
    setSpan(newCount)
    setOffset(clamp(Math.round(st.n - (newStart + newCount)), 0, st.n - newCount))
  }
  const resetZoom = () => { setSpan(null); setOffset(0) }

  const hc = hover != null ? data[hover] : null
  const zoomed = span != null && count < n
  const btn = 'w-7 h-7 flex items-center justify-center rounded bg-gray-800/90 border border-gray-700 text-gray-300 hover:bg-gray-700 hover:text-white text-sm leading-none'

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-700 overflow-hidden">
      <div ref={wrapRef} className="w-full relative select-none" style={{ height }}>
        {!ready ? (
          <div className="h-full flex items-center justify-center text-gray-600 text-sm">
            {n === 0 ? 'Sin datos de velas (mercado cerrado o EA desconectado).' : ''}
          </div>
        ) : (
          <>
            {/* Controles de zoom (arriba a la derecha) */}
            <div className="absolute z-20 top-2 right-2 flex items-center gap-1">
              {zoomed && (
                <span className="text-[10px] text-gray-500 mr-1">{count}/{n}</span>
              )}
              <button className={btn} onClick={() => zoomCenter(0.7)} title="Acercar">+</button>
              <button className={btn} onClick={() => zoomCenter(1 / 0.7)} title="Alejar">−</button>
              <button className={btn} onClick={resetZoom} title="Ver todo" disabled={!zoomed && off === 0}>⤢</button>
            </div>

            <svg
              width={width}
              height={height}
              className="block"
              style={{ cursor: dragging ? 'grabbing' : 'grab' }}
              onMouseDown={onDown}
              onMouseMove={onMove}
              onMouseUp={endDrag}
              onMouseLeave={() => { setHover(null); endDrag() }}
            >
              {/* Rejilla + etiquetas de precio */}
              {tickVals.map((v, i) => (
                <g key={`y${i}`}>
                  <line x1={mL} y1={y(v)} x2={mL + plotW} y2={y(v)}
                    stroke="#1f2937" strokeWidth={1} />
                  <text x={mL - 6} y={y(v) + 3} textAnchor="end"
                    fontSize={10} fill="#6b7280">{fmtPrice(v)}</text>
                </g>
              ))}

              {/* Etiquetas de tiempo (solo velas visibles) */}
              {view.map((c, j) => (j % xStep === 0 ? (
                <text key={`x${j}`} x={x(start + j)} y={height - 8} textAnchor="middle"
                  fontSize={10} fill="#6b7280">{formatBrokerShort(c.t)}</text>
              ) : null))}

              {/* Velas: mecha (high-low) + cuerpo (open-close) */}
              {view.map((c, j) => {
                const i = start + j
                const col = c.close >= c.open ? UP : DOWN
                const bodyTop = Math.min(y(c.open), y(c.close))
                const bodyH = Math.max(1, Math.abs(y(c.close) - y(c.open)))
                return (
                  <g key={`c${i}`}>
                    <line x1={x(i)} y1={y(c.high)} x2={x(i)} y2={y(c.low)} stroke={col} strokeWidth={1} />
                    <rect x={x(i) - bodyW / 2} y={bodyTop} width={bodyW} height={bodyH} fill={col} />
                  </g>
                )
              })}

              {/* Líneas de CADA posición abierta del símbolo (entrada / SL / TP).
                  Etiquetas ancladas al borde derecho para no salirse del lienzo. */}
              {posList.map((p, pi) => {
                const col = p.profit >= 0 ? UP : DOWN
                return (
                  <g key={`p${p.ticket ?? pi}`}>
                    <line x1={mL} y1={y(p.open_price)} x2={mL + plotW} y2={y(p.open_price)}
                      stroke={col} strokeWidth={1.5} strokeDasharray="5 3" />
                    <text x={width - 4} y={y(p.open_price) - 3} textAnchor="end"
                      fontSize={10} fill={col}>
                      {p.direction} {p.volume} @ {p.open_price.toFixed(dec)}
                    </text>
                    {p.stop_loss && Number.isFinite(p.stop_loss) && (
                      <>
                        <line x1={mL} y1={y(p.stop_loss)} x2={mL + plotW} y2={y(p.stop_loss)}
                          stroke={DOWN} strokeWidth={1} strokeDasharray="2 3" />
                        <text x={width - 4} y={y(p.stop_loss) - 3} textAnchor="end"
                          fontSize={9} fill={DOWN}>SL {p.stop_loss.toFixed(dec)}</text>
                      </>
                    )}
                    {p.take_profit && Number.isFinite(p.take_profit) && (
                      <>
                        <line x1={mL} y1={y(p.take_profit)} x2={mL + plotW} y2={y(p.take_profit)}
                          stroke={UP} strokeWidth={1} strokeDasharray="2 3" />
                        <text x={width - 4} y={y(p.take_profit) - 3} textAnchor="end"
                          fontSize={9} fill={UP}>TP {p.take_profit.toFixed(dec)}</text>
                      </>
                    )}
                  </g>
                )
              })}

              {/* Marcadores de señales (▲ buy / ▼ sell) en su precio/hora */}
              {visibleSignals.map(({ s, idx }, k) => {
                const mx = x(idx)
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
          </>
        )}
      </div>
    </div>
  )
}
