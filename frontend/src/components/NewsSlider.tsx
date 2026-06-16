import { useEffect, useState } from 'react'
import { getApiUrl, getApiHeaders } from '../config'

const API_URL = getApiUrl()

interface Headline {
  symbol: string
  title: string
  link: string
  age: string
}

// Slider de titulares: rota automáticamente las noticias más recientes (Yahoo
// Finance RSS vía /api/news) de los símbolos que opera el bot. Se pausa al pasar
// el ratón y deja navegar a mano (flechas + puntos). Fail-safe: si no hay
// titulares (NEWS_ENABLED=false, símbolo sin mapear o feed caído) no renderiza
// nada, así que el dashboard no muestra una sección vacía.
export function NewsSlider() {
  const [headlines, setHeadlines] = useState<Headline[]>([])
  const [enabled, setEnabled] = useState(true)
  const [idx, setIdx] = useState(0)
  const [paused, setPaused] = useState(false)

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

  // Reencaja el índice si la lista encoge tras un refresco.
  useEffect(() => {
    if (idx >= headlines.length) setIdx(0)
  }, [headlines.length, idx])

  // Auto-avance cada 6s, pausado al hacer hover o con menos de 2 titulares.
  useEffect(() => {
    if (paused || headlines.length < 2) return
    const t = setInterval(() => setIdx(i => (i + 1) % headlines.length), 6000)
    return () => clearInterval(t)
  }, [paused, headlines.length])

  if (!enabled || headlines.length === 0) return null

  const current = headlines[Math.min(idx, headlines.length - 1)]
  const go = (delta: number) =>
    setIdx(i => (i + delta + headlines.length) % headlines.length)

  return (
    <section>
      <h2 className="text-xl font-bold mb-4">Noticias destacadas</h2>
      <div
        className="bg-gray-900 rounded-lg border border-gray-700"
        onMouseEnter={() => setPaused(true)}
        onMouseLeave={() => setPaused(false)}
      >
        <div className="flex items-center gap-3 px-3 py-3">
          <button
            onClick={() => go(-1)}
            aria-label="Anterior"
            className="shrink-0 h-7 w-7 grid place-items-center rounded text-lg text-gray-500 hover:text-gray-200 hover:bg-gray-800 transition-colors"
          >
            ‹
          </button>

          <a
            href={current.link || undefined}
            target="_blank"
            rel="noopener noreferrer"
            title={current.title}
            className="group flex-1 min-w-0 flex items-center gap-3"
          >
            <span className="shrink-0 px-2 py-0.5 rounded text-[11px] font-semibold tracking-wide bg-emerald-500/15 text-emerald-400">
              {current.symbol}
            </span>
            <span className="flex-1 min-w-0 truncate text-sm text-gray-200 group-hover:text-white transition-colors">
              {current.title}
            </span>
            {current.age && (
              <span className="shrink-0 text-xs text-gray-500">{current.age}</span>
            )}
          </a>

          <button
            onClick={() => go(1)}
            aria-label="Siguiente"
            className="shrink-0 h-7 w-7 grid place-items-center rounded text-lg text-gray-500 hover:text-gray-200 hover:bg-gray-800 transition-colors"
          >
            ›
          </button>
        </div>

        {headlines.length > 1 && (
          <div className="flex items-center justify-center flex-wrap gap-1.5 pb-2.5">
            {headlines.map((_, i) => (
              <button
                key={i}
                onClick={() => setIdx(i)}
                aria-label={`Titular ${i + 1}`}
                className={`h-1.5 rounded-full transition-all ${
                  i === idx ? 'w-4 bg-emerald-400' : 'w-1.5 bg-gray-600 hover:bg-gray-500'
                }`}
              />
            ))}
          </div>
        )}
      </div>
    </section>
  )
}
