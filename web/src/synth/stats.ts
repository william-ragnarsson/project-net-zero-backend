// The numbers behind the synthetic run: the same energy model and decision rule as the
// backend's fake pipeline (netzero/pipeline/fake.py), with closed-form approximations
// where the backend uses scipy.
import type { CI, CandidateId, Interval, MeasureStats, PowerInfo } from '../gen/events'

export const mean = (xs: readonly number[]): number => xs.reduce((a, b) => a + b, 0) / xs.length

export function std(xs: readonly number[]): number {
  if (xs.length < 2) return 0
  const m = mean(xs)
  return Math.sqrt(xs.reduce((a, x) => a + (x - m) ** 2, 0) / (xs.length - 1))
}

/** Two-sided 95% Student t quantile (Cornish-Fisher; within 0.1% for df >= 10). */
function tCrit(df: number): number {
  const z = 1.959964
  return z + (z ** 3 + z) / (4 * df) + (5 * z ** 5 + 16 * z ** 3 + 3 * z) / (96 * df ** 2)
}

/** Standard normal CDF (Abramowitz & Stegun 7.1.26). */
function normalCdf(x: number): number {
  const t = 1 / (1 + (0.3275911 * Math.abs(x)) / Math.SQRT2)
  const poly = t * (0.254829592 + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429))))
  const tail = 0.5 * poly * Math.exp(-(x * x) / 2)
  return x < 0 ? tail : 1 - tail
}

function interval(xs: readonly number[]): Interval {
  const m = mean(xs)
  const s = std(xs)
  const half = (tCrit(xs.length - 1) * s) / Math.sqrt(xs.length)
  return { mean: m, ci_low: m - half, ci_high: m + half, std: s }
}

/** n standard-normal draws, standardized to mean 0 and sample std 1. */
function standardNormals(rand: () => number, n: number): number[] {
  const z = Array.from({ length: n }, () => {
    const u = 1 - rand()
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * rand())
  })
  const m = mean(z)
  const s = std(z)
  return z.map((x) => (s > 0 ? (x - m) / s : 0))
}

export interface Arm {
  stats: MeasureStats
  logG: number[]
}

export interface MeasureInput {
  cpuS: number
  sigma: number
  n: number
  calls: number
  power: PowerInfo
}

/** One bench arm under the energy model: CPU and RAM power times measured time. */
export function measure(rand: () => number, { cpuS, sigma, n, calls, power }: MeasureInput): Arm {
  const z = standardNormals(rand, n)
  const w = standardNormals(rand, n)
  const cpu = z.map((zi) => cpuS * Math.exp(sigma * zi))
  const wall = cpu.map((c, i) => c * 1.04 * Math.exp(0.01 * (w[i] ?? 0)))
  const kCpu = cpu.map((c) => (c * power.p_core_w) / 3.6e6)
  const kRam = wall.map((x) => (x * power.p_ram_w) / 3.6e6)
  const kwh = kCpu.map((k, i) => k + (kRam[i] ?? 0))
  const g = kwh.map((k) => k * power.grid.kg_per_kwh * 1000)
  return {
    stats: {
      g_per_call: interval(g),
      kwh_per_call: interval(kwh),
      kwh_cpu_per_call: mean(kCpu),
      kwh_ram_per_call: mean(kRam),
      cpu_s_per_call: interval(cpu),
      wall_s_per_call: interval(wall),
      n_trials: n,
      calls_per_trial: calls,
      trials_g: g,
    },
    logG: g.map(Math.log),
  }
}

export interface Comparison {
  deltaPct: number
  ci: CI
  p: number
}

/** Change in mean g/call with a 95% CI, and a one-sided Welch test on log g. */
export function compare(orig: Arm, cand: Arm): Comparison {
  const n = orig.logG.length
  const ratio = cand.stats.g_per_call.mean / orig.stats.g_per_call.mean
  const va = std(orig.logG) ** 2 / n
  const vb = std(cand.logG) ** 2 / n
  const se = Math.sqrt(va + vb)
  const diff = mean(cand.logG) - mean(orig.logG)
  const df = (va + vb) ** 2 / ((va ** 2 + vb ** 2) / (n - 1))
  const t = tCrit(df)
  return {
    deltaPct: round((ratio - 1) * 100, 2),
    ci: {
      lo: Math.floor((ratio * Math.exp(-t * se) - 1) * 1e4) / 100,
      hi: Math.ceil((ratio * Math.exp(t * se) - 1) * 1e4) / 100,
    },
    p: se === 0 ? (diff < 0 ? 0 : 1) : normalCdf(diff / se),
  }
}

/** Holm step-down adjustment; ties break by candidate id. */
export function holm(pValues: ReadonlyMap<CandidateId, number>): Map<CandidateId, number> {
  const sorted = [...pValues].sort(([ca, pa], [cb, pb]) => pa - pb || ca.localeCompare(cb))
  const out = new Map<CandidateId, number>()
  let running = 0
  sorted.forEach(([cid, p], j) => {
    running = Math.max(running, Math.min(1, (sorted.length - j) * p))
    out.set(cid, running)
  })
  return out
}

export interface Rule {
  alpha: number
  min_effect_pct: number
}

export const isSignificant = (p: number, ci: CI, delta: number, rule: Rule): boolean =>
  p < rule.alpha && ci.hi < 0 && delta <= -rule.min_effect_pct

export function whyNot(p: number, ci: CI, delta: number, rule: Rule): string {
  if (delta > -rule.min_effect_pct) return `below the ${rule.min_effect_pct}% minimum effect`
  if (ci.hi >= 0) return '95% CI includes 0'
  if (p >= rule.alpha) return `${fmtP(p, 'p_holm')} ≥ α=${rule.alpha}`
  return ''
}

export const round = (x: number, digits: number): number => Math.round(x * 10 ** digits) / 10 ** digits

/** `+1.5` / `−71.8`, with a real minus sign. */
export const pct = (x: number): string => `${x < 0 ? '−' : '+'}${Math.abs(x).toFixed(1)}`

/** Python's `format(x, '.{digits}g')`, so synthetic log lines read like the backend's. */
export function fmtG(x: number, digits: number): string {
  if (x === 0) return '0'
  const [mantissa = '', exp = '0'] = x.toExponential(digits - 1).split('e')
  const e = Number(exp)
  if (e < -4 || e >= digits) {
    return `${Number(mantissa)}e${e < 0 ? '-' : '+'}${String(Math.abs(e)).padStart(2, '0')}`
  }
  return String(Number(x.toPrecision(digits)))
}

export const fmtP = (p: number, label = 'p'): string =>
  p < 1e-6 ? `${label}<1e-6` : `${label}=${fmtG(p, 2)}`

/** "−71.8% CO₂/call (95% CI −74.1…−69.3)" */
export const headline = (delta: number, ci: CI): string =>
  `${pct(delta)}% CO₂/call (95% CI ${pct(ci.lo)}…${pct(ci.hi)})`
