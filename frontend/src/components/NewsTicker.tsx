import { useEffect, useRef, useState } from 'react'
import { getApiUrl, getApiHeaders } from '../config'

const API_URL = getApiUrl()

interface Headline {
  symbol: string
  title: string
  link: string
  age: string
  source: string
}

// Velocidad del desplazamiento en px/s. Fija (no depende del nº de titulares):
// la duración se calcula desde el ancho real del contenido, como un teletipo.
const SPEED_PX_PER_S = 60

// Teletipo de noticias (estilo banner de CNN): los titulares de los símbolos que
// opera el bot (Yahoo Finance RSS vía /api/news) se desplazan en horizontal de
// forma continua. Se pausa al pasar el ratón; cada titular es un enlace a la
// noticia. Fail-safe: si no hay titulares (NEWS_ENABLED=false, símbolo sin mapear
// o feed caído) no renderiza nada, así que el dashboard no muestra hueco vacío.
export function NewsTicker() {
  const [headlines, setHeadlines] = useState<Headline[]>([])
  const [enabled, setEnabled] = useState(true)
  // Ancho de UNA copia del contenido: la pista lleva dos copias y se anima a
  // -50% para un bucle sin costuras, así que la duración = ancho copia / vel.
  const copyRef = useRef<HTMLDivElement>(null)
  const [duration, setDuration] = useState(40)

  useEffect(() => {
    let alive = true
    const load = () => {
      fetch(`${API_URL}/api/news`, { headers: getApiHeaders() })
        .then(r => r.json())
        .then(d => {
          if (!alive) return
          setEnabled(d?.enabled !== false)
          setHeadlines(Array.isArray(d?.headlines) ? d.headlines : [])
        })
        .catch(() => {})
    }
    load()
    // El backend cachea 15 min; refrescamos cada 5 para captar nuevas noticias.
    const interval = setInterval(load, 5 * 60 * 1000)
    return () => { alive = false; clearInterval(interval) }
  }, [])

  // Mide el ancho de una copia para fijar una velocidad de scroll constante,
  // independiente del número y la longitud de los titulares.
  useEffect(() => {
    const el = copyRef.current
    if (!el) return
    const measure = () => {
      const w = el.scrollWidth
      if (w > 0) setDuration(Math.max(15, w / SPEED_PX_PER_S))
    }
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [headlines])

  if (!enabled || headlines.length === 0) return null

  // Una copia del listado (badge del símbolo + titular enlazado + antigüedad,
  // separados por un punto). Se renderiza dos veces para el bucle continuo.
  const Track = ({ withRef }: { withRef?: boolean }) => (
    <div
      ref={withRef ? copyRef : undefined}
      aria-hidden={!withRef}
      className="flex shrink-0 items-center"
    >
      {headlines.map((h, i) => (
        <a
          key={i}
          href={h.link || undefined}
          target="_blank"
          rel="noopener noreferrer"
          title={h.title}
          className="group/item inline-flex items-center gap-2 whitespace-nowrap"
        >
          <span className="px-2 py-0.5 rounded text-[11px] font-semibold tracking-wide bg-emerald-500/15 text-emerald-400">
            {h.symbol}
          </span>
          <span className="text-sm text-gray-200 group-hover/item:text-white transition-colors">
            {h.title}
          </span>
          {h.source && <span className="text-xs text-gray-600">· {h.source}</span>}
          {h.age && <span className="text-xs text-gray-500">{h.age}</span>}
          <span className="mx-5 text-gray-700 select-none">•</span>
        </a>
      ))}
    </div>
  )

  return (
    <section>
      <h2 className="text-xl font-bold mb-4">Noticias destacadas</h2>
      <div className="group/ticker bg-gray-900 rounded-lg border border-gray-700 overflow-hidden">
        <div className="relative flex overflow-hidden py-3">
          {/* Keyframe local: desplaza la pista (dos copias) media anchura para
              que la segunda copia caiga justo donde empezó la primera. */}
          <style>{`@keyframes news-ticker{from{transform:translateX(0)}to{transform:translateX(-50%)}}`}</style>
          <div
            className="flex w-max will-change-transform group-hover/ticker:[animation-play-state:paused]"
            style={{ animation: `news-ticker ${duration}s linear infinite` }}
          >
            <Track withRef />
            <Track />
          </div>
        </div>
      </div>
    </section>
  )
}
