// Decimales de precio por símbolo. Los cripto (BTCUSD/ETHUSD) se ven bien con 2;
// EURUSD (forex) necesita más precisión (4) para que el precio no se aplaste.
export function priceDecimals(symbol: string | null | undefined): number {
  return (symbol || '').toUpperCase() === 'EURUSD' ? 4 : 2
}

// --- Horarios ---------------------------------------------------------------
// El backend sella TODAS sus marcas en HORARIO DEL BRÓKER (hora de pared del
// servidor MT, sin zona). El dashboard las muestra sumando este desplazamiento
// fijo (la zona del usuario). Cambiar aquí si el bróker cambia de huso.
export const DISPLAY_OFFSET_HOURS = 2

const OFFSET_MS = DISPLAY_OFFSET_HOURS * 3600 * 1000

// Convierte una marca del backend a epoch-ms ya desplazado (+offset de display),
// ANCLADO en UTC para que el huso del navegador no la vuelva a mover. Acepta
// string "YYYY-MM-DD HH:MM:SS" (hora bróker) o epoch en segundos (open_time crudo
// de MT, ya localizado en la zona del servidor). Devuelve NaN si no es parseable.
export function brokerToDisplayMs(value: string | number | null | undefined): number {
  if (value == null || value === '') return NaN
  let base: number
  if (typeof value === 'number') {
    base = value * 1000
  } else {
    base = Date.parse(value.replace(' ', 'T') + 'Z')
  }
  return Number.isNaN(base) ? NaN : base + OFFSET_MS
}

// `timeZone: 'UTC'` es deliberado: la marca ya viene desplazada en brokerToDisplayMs,
// así que formateamos sus componentes tal cual, sin que el navegador reaplique su huso.
const _fmtFull = new Intl.DateTimeFormat('es-ES', {
  timeZone: 'UTC', day: '2-digit', month: '2-digit', year: 'numeric',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
})
const _fmtClock = new Intl.DateTimeFormat('es-ES', {
  timeZone: 'UTC', hour: '2-digit', minute: '2-digit', hour12: false,
})
const _fmtShort = new Intl.DateTimeFormat('es-ES', {
  timeZone: 'UTC', day: '2-digit', month: '2-digit',
  hour: '2-digit', minute: '2-digit', hour12: false,
})

// Fecha + hora del bróker desplazada a la zona del usuario. 'N/A' si no parsea.
export function formatBrokerTime(value: string | number | null | undefined): string {
  const ms = brokerToDisplayMs(value)
  return Number.isNaN(ms) ? 'N/A' : _fmtFull.format(new Date(ms))
}

// Solo HH:MM (hora bróker + offset).
export function formatBrokerClock(value: string | number | null | undefined): string {
  const ms = brokerToDisplayMs(value)
  return Number.isNaN(ms) ? 'N/A' : _fmtClock.format(new Date(ms))
}

// Día/mes HH:MM compacto (ejes y tooltips). '' si no parsea.
export function formatBrokerShort(value: string | number | null | undefined): string {
  const ms = brokerToDisplayMs(value)
  return Number.isNaN(ms) ? '' : _fmtShort.format(new Date(ms))
}
