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
