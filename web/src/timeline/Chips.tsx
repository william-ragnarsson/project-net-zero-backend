// A node's metrics, as small mono chips under its label.
import { Co2 } from '../components/Co2'
import { formatGrams, formatInt, formatPct } from '../lib/format'
import type { Chip } from './layout'
import { FAIL, NEON } from './theme'

const muted = '#6b6b6b'

function ChipBody({ chip }: { chip: Chip }) {
  switch (chip.kind) {
    case 'tests':
      return (
        <span style={{ color: chip.passed < chip.total ? FAIL : '#a3a3a3' }}>
          {chip.passed}/{chip.total}
          <span style={{ color: muted }}> pass</span>
        </span>
      )
    case 'count':
      return (
        <span className="text-[#a3a3a3]">
          {formatInt(chip.n)}
          <span style={{ color: muted }}> {chip.noun}</span>
        </span>
      )
    case 'grams':
      return (
        <span style={{ color: chip.g < 0 ? NEON : '#a3a3a3' }}>
          {formatGrams(chip.g)}
          <span style={{ color: muted }}>
            {' '}
            <Co2 />
            /1M
          </span>
        </span>
      )
    case 'delta':
      return (
        <span style={{ color: !chip.significant ? '#8a8a8a' : chip.pct < 0 ? NEON : FAIL }}>
          {formatPct(chip.pct)}
          {!chip.significant && <span style={{ color: muted }}> ns</span>}
        </span>
      )
  }
}

/** Words for a screen reader, matching what the chip shows. */
export function chipText(chip: Chip): string {
  switch (chip.kind) {
    case 'tests':
      return `${chip.passed} of ${chip.total} tests pass`
    case 'count':
      return `${formatInt(chip.n)} ${chip.noun}`
    case 'grams':
      return `${formatGrams(chip.g)} CO2 per 1M calls`
    case 'delta':
      return `${formatPct(chip.pct)}${chip.significant ? '' : ', not significant'}`
  }
}

export function Chips({ chips }: { chips: readonly Chip[] }) {
  if (chips.length === 0) return null
  return (
    <span className="mt-1 flex gap-1">
      {chips.map((chip) => (
        <span
          key={chip.kind}
          className="whitespace-nowrap rounded-[4px] border border-[#1f1f1f] bg-[#0f0f0f] px-1.5 py-px font-mono text-[10px] leading-[15px] tabular-nums"
        >
          <ChipBody chip={chip} />
        </span>
      ))}
    </span>
  )
}
