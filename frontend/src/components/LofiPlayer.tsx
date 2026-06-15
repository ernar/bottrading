import { useEffect, useRef, useState } from 'react'

// Emisora LO-FI por defecto (radio en streaming, gratuita). Cámbiala aquí si
// quieres otra fuente; debe ser una URL de stream de audio reproducible (mp3/aac).
const STREAM_URL = 'https://lofi.stream.laut.fm/lofi'

const KEY_PLAY = 'lofiPlaying'
const KEY_VOL = 'lofiVolume'

export function LofiPlayer() {
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const [playing, setPlaying] = useState(false)
  const [blocked, setBlocked] = useState(false) // autoplay bloqueado por el navegador
  const [volume, setVolume] = useState(() => {
    const v = parseFloat(localStorage.getItem(KEY_VOL) || '')
    return Number.isFinite(v) ? v : 0.45
  })

  // Crea el elemento de audio una sola vez.
  useEffect(() => {
    const audio = new Audio(STREAM_URL)
    audio.loop = true
    audio.preload = 'none'
    audio.volume = volume
    audioRef.current = audio
    return () => { audio.pause(); audio.src = '' }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const play = () => {
    const audio = audioRef.current
    if (!audio) return Promise.reject()
    return audio.play()
      .then(() => { setPlaying(true); setBlocked(false); localStorage.setItem(KEY_PLAY, '1') })
  }

  const pause = () => {
    audioRef.current?.pause()
    setPlaying(false)
    localStorage.setItem(KEY_PLAY, '0')
  }

  const toggle = () => { if (playing) pause(); else play().catch(() => setBlocked(true)) }

  // Autoarranque al entrar en la web (salvo que el usuario lo dejara pausado).
  // Los navegadores bloquean el audio con sonido sin interacción previa: si la
  // reproducción se rechaza, la armamos para que empiece al primer gesto.
  useEffect(() => {
    if (localStorage.getItem(KEY_PLAY) === '0') return
    let armed = false
    const startOnGesture = () => {
      if (armed) return
      armed = true
      play().catch(() => {})
      cleanup()
    }
    const cleanup = () => {
      window.removeEventListener('pointerdown', startOnGesture)
      window.removeEventListener('keydown', startOnGesture)
      window.removeEventListener('touchstart', startOnGesture)
    }
    play().catch(() => {
      setBlocked(true)
      window.addEventListener('pointerdown', startOnGesture)
      window.addEventListener('keydown', startOnGesture)
      window.addEventListener('touchstart', startOnGesture)
    })
    return cleanup
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Aplica volumen y lo persiste.
  useEffect(() => {
    if (audioRef.current) audioRef.current.volume = volume
    localStorage.setItem(KEY_VOL, String(volume))
  }, [volume])

  return (
    <div className="fixed bottom-5 left-5 z-40 flex items-center gap-2 bg-gray-900/90 backdrop-blur border border-gray-700 rounded-full pl-2 pr-3 py-1.5 shadow-lg shadow-black/40">
      <button
        onClick={toggle}
        title={playing ? 'Pausar LO-FI' : blocked ? 'Pulsa para activar la música' : 'Reproducir LO-FI'}
        className={`w-8 h-8 rounded-full flex items-center justify-center text-sm transition ${
          playing ? 'bg-emerald-600 hover:bg-emerald-500' : 'bg-gray-700 hover:bg-gray-600'
        } ${blocked && !playing ? 'animate-pulse' : ''}`}
      >
        {playing ? '⏸' : '▶'}
      </button>

      {/* Ecualizador animado mientras suena */}
      <div className="flex items-end gap-[2px] h-4 w-5" aria-hidden>
        {[0, 1, 2, 3].map(i => (
          <span
            key={i}
            className="flex-1 bg-emerald-400 rounded-sm origin-bottom"
            style={{
              height: '100%',
              animation: playing ? `eq-bounce 0.9s ease-in-out ${i * 0.15}s infinite` : 'none',
              transform: playing ? undefined : 'scaleY(0.25)',
              opacity: playing ? 1 : 0.4,
            }}
          />
        ))}
      </div>

      <span className="text-[10px] uppercase tracking-wider text-gray-400 select-none">lo-fi</span>

      <input
        type="range"
        min={0}
        max={1}
        step={0.01}
        value={volume}
        onChange={e => setVolume(parseFloat(e.target.value))}
        title="Volumen"
        className="w-16 h-1 accent-emerald-500 cursor-pointer"
      />
    </div>
  )
}
