import { describe, expect, it } from 'vitest'
import { formatClock, formatGrams, formatInt, formatPct, formatUsd, NONE } from './format'

describe('format', () => {
  it('formats durations as a clock', () => {
    expect(formatClock(0)).toBe('0:00')
    expect(formatClock(201_657)).toBe('3:21')
    expect(formatClock(3_600_000 + 65_000)).toBe('1:01:05')
    expect(formatClock(-5)).toBe('0:00')
  })

  it('writes negative numbers with a minus sign', () => {
    expect(formatPct(-78.6)).toBe('−78.6%')
    expect(formatPct(4.25, 2)).toBe('4.25%')
    expect(formatPct(null)).toBe(NONE)
    expect(formatUsd(-1.5)).toBe('−$1.50')
    expect(formatGrams(-0.25)).toBe('−250.0 mg')
  })

  it('picks precision and units that keep numbers short', () => {
    expect(formatUsd(0)).toBe('$0.00')
    expect(formatUsd(0.01234)).toBe('$0.0123')
    expect(formatUsd(12.3)).toBe('$12.30')
    expect(formatGrams(5.5683)).toBe('5.6 g')
    expect(formatGrams(1520)).toBe('1.52 kg')
    expect(formatInt(12345.6)).toBe('12,346')
  })
})
