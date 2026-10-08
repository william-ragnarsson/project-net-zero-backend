// Display formatting. Negative numbers use a true minus sign (U+2212), as the brand does.

/** Stands in for a value that does not exist yet. */
export const NONE = '·'

const MINUS = '−'

const signed = (text: string, negative: boolean): string => (negative ? `${MINUS}${text}` : text)

/** `m:ss`, or `h:mm:ss` from an hour on. */
export function formatClock(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = String(total % 60).padStart(2, '0')
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`
}

export const formatInt = (n: number): string => Math.round(n).toLocaleString('en-US')

/** A percentage change: `−12.4%` for a reduction. */
export const formatPct = (n: number | null, digits = 1): string =>
  n === null ? NONE : signed(`${Math.abs(n).toFixed(digits)}%`, n < 0)

export function formatUsd(n: number): string {
  const digits = n !== 0 && Math.abs(n) < 1 ? 4 : 2
  return signed(`$${Math.abs(n).toFixed(digits)}`, n < 0)
}

/** Grams with a unit that keeps the number short. */
export function formatGrams(g: number): string {
  const abs = Math.abs(g)
  const text = abs >= 1000 ? `${(abs / 1000).toFixed(2)} kg` : abs >= 1 ? `${abs.toFixed(1)} g` : `${(abs * 1000).toFixed(1)} mg`
  return signed(text, g < 0)
}

/** Bytes in the largest unit that keeps a whole number: `592 kB`. */
export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  const units = ['kB', 'MB', 'GB', 'TB']
  let value = n / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value >= 10 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`
}

const MINUTE = 60_000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

/** A date, `Oct 7`, with the year when it isn't this one. `timeZone` is for tests. */
export function formatDate(ts: number, { now = Date.now(), timeZone }: { now?: number; timeZone?: string } = {}): string {
  const tz = timeZone === undefined ? {} : { timeZone }
  const year = (t: number) => new Date(t).toLocaleDateString('en-US', { year: 'numeric', ...tz })
  const withYear = year(ts) === year(now) ? {} : { year: 'numeric' as const }
  return new Date(ts).toLocaleDateString('en-US', { month: 'short', day: 'numeric', ...withYear, ...tz })
}

/** The time of day, `14:05`, on a 24-hour clock. `timeZone` is for tests. */
export function formatTime(ts: number, timeZone?: string): string {
  const tz = timeZone === undefined ? {} : { timeZone }
  return new Date(ts).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', ...tz })
}

/** How long ago, coarsely: `just now`, `12 min ago`, `5 h ago`, `3 d ago`, then the date. */
export function formatAgo(ts: number, now = Date.now()): string {
  const ago = now - ts
  if (ago < MINUTE) return 'just now'
  if (ago < HOUR) return `${Math.floor(ago / MINUTE)} min ago`
  if (ago < DAY) return `${Math.floor(ago / HOUR)} h ago`
  if (ago < 14 * DAY) return `${Math.floor(ago / DAY)} d ago`
  return formatDate(ts, { now })
}
