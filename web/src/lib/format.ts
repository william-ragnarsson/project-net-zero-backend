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
export function formatPct(n: number | null, digits = 1): string {
  if (n === null) return NONE
  const text = Math.abs(n).toFixed(digits)
  // a change that rounds to zero carries no sign
  return signed(`${text}%`, n < 0 && Number(text) !== 0)
}

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

/** Three significant figures, without exponent notation for the sizes the tree shows. */
export function sig3(v: number): string {
  if (v === 0) return '0'
  const abs = Math.abs(v)
  const text = abs >= 100 ? abs.toFixed(0) : abs >= 10 ? abs.toFixed(1) : abs >= 1 ? abs.toFixed(2) : abs.toPrecision(3)
  return signed(text, v < 0)
}

/** A unit for grams per call: µg, or ng when the numbers would be tiny. */
export interface MassUnit {
  factor: number
  unit: 'µg' | 'ng'
}

export const MICROGRAMS: MassUnit = { factor: 1e6, unit: 'µg' }
export const NANOGRAMS: MassUnit = { factor: 1e9, unit: 'ng' }

/** ng when the baseline is below 0.1 µg per call, else µg. */
export const massUnit = (baselineGPerCall: number | null | undefined): MassUnit =>
  baselineGPerCall !== null && baselineGPerCall !== undefined && baselineGPerCall > 0 && baselineGPerCall * 1e6 < 0.1
    ? NANOGRAMS
    : MICROGRAMS

/** Grams as `12.3 µg` (or ng). */
export const formatMass = (g: number, unit: MassUnit = MICROGRAMS): string => `${sig3(g * unit.factor)} ${unit.unit}`

/** A confidence interval of percentages: `−12.0% to −4.1%`. */
export const formatCI = (ci: { lo: number; hi: number } | null, digits = 1): string =>
  ci === null ? NONE : `${formatPct(ci.lo, digits)} to ${formatPct(ci.hi, digits)}`

/** A p-value with as many digits as it needs. */
export function formatP(p: number | null): string {
  if (p === null) return NONE
  if (p < 1e-6) return '< 1e-6'
  if (p < 1e-3) return p.toExponential(1)
  return p < 0.01 ? p.toFixed(4) : p.toFixed(3)
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

const pad2 = (n: number) => String(n).padStart(2, '0')

/**
 * When something happened, in local time: `12 Oct 22:09`, or `12 Oct 2025` from another year.
 * Built by hand rather than with Intl, so the output is the same in every locale and test.
 */
export function formatWhen(ts: number, now: number = Date.now()): string {
  const d = new Date(ts)
  const day = `${d.getDate()} ${MONTHS[d.getMonth()]}`
  return d.getFullYear() === new Date(now).getFullYear()
    ? `${day} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`
    : `${day} ${d.getFullYear()}`
}

/** A file size: `812 B`, `14.2 kB`, `1.3 MB`. */
export function formatBytes(n: number): string {
  if (n < 1000) return `${Math.round(n)} B`
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)} kB`
  return `${(n / 1_000_000).toFixed(1)} MB`
}
