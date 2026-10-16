import { formatCI, formatPct } from '../lib/format'

export interface CiRow {
  key: string
  /** drawn beside the bar when the chart has more than one row */
  label: string
  delta: number
  ci: { lo: number; hi: number } | null
  /** the interval clears the gate, so it draws in the accent */
  clears: boolean
}

const ROW = 16
const TOP = 22

/**
 * Change in CO2e per call with its confidence interval, against zero and the gate. A reduction
 * is negative, so wins sit left of zero. Positions are percentages, so the chart fills any width
 * without stretching its text.
 */
export function CiChart({ rows, gate }: { rows: readonly CiRow[]; gate: number }) {
  if (rows.length === 0) return null
  const values = [0, -gate, ...rows.flatMap((r) => (r.ci === null ? [r.delta] : [r.delta, r.ci.lo, r.ci.hi]))]
  const lo = Math.min(...values)
  const hi = Math.max(...values)
  const span = Math.max(1, hi - lo)
  const min = lo - span * 0.08
  const max = hi + span * 0.12
  const x = (v: number) => `${(((v - min) / (max - min)) * 100).toFixed(2)}%`
  const height = Math.max(56, TOP + rows.length * ROW + 10)
  const label = rows
    .map((r) => `${r.label} ${formatPct(r.delta)}${r.ci === null ? '' : `, interval ${formatCI(r.ci)}`}`)
    .join('; ')
  return (
    <svg width="100%" height={height} role="img" aria-label={`${label}; gate ${formatPct(-gate)}`} className="block">
      <line x1={x(0)} x2={x(0)} y1={14} y2={height} style={{ stroke: 'var(--nz-line)' }} />
      {gate > 0 && (
        <line x1={x(-gate)} x2={x(-gate)} y1={14} y2={height} strokeDasharray="2 3" style={{ stroke: 'var(--nz-ink-3)' }} />
      )}
      <text x={x(0)} y={10} textAnchor="middle" className="fill-current text-xs text-ink-3">
        0
      </text>
      {gate > 0 && (
        <text x={x(-gate)} y={10} textAnchor="middle" className="fill-current text-xs text-ink-3">
          {formatPct(-gate, 0)}
        </text>
      )}
      {rows.map((r, i) => {
        const y = TOP + i * ROW + ROW / 2
        const colour = { fill: r.clears ? 'var(--nz-accent)' : 'var(--nz-ink-3)' }
        const end = r.ci?.hi ?? r.delta
        return (
          <g key={r.key}>
            {r.ci !== null && (
              <rect
                x={x(r.ci.lo)}
                width={`${(((r.ci.hi - r.ci.lo) / (max - min)) * 100).toFixed(2)}%`}
                y={y - 1}
                height={2}
                style={colour}
              />
            )}
            <circle cx={x(r.delta)} cy={y} r={3} style={colour} />
            {rows.length > 1 && (
              <text x={x(end)} dx={8} y={y + 4} className="fill-current text-xs text-ink-3">
                {r.label}
              </text>
            )}
          </g>
        )
      })}
    </svg>
  )
}
