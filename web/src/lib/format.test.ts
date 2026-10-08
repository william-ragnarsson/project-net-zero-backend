import { describe, expect, it } from 'vitest'
import {
  formatAgo,
  formatBytes,
  formatClock,
  formatDate,
  formatGrams,
  formatInt,
  formatPct,
  formatTime,
  formatUsd,
  NONE,
} from './format'

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

  it('sizes bytes', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(1023)).toBe('1023 B')
    expect(formatBytes(1536)).toBe('1.5 kB')
    expect(formatBytes(606_208)).toBe('592 kB')
    expect(formatBytes(5 * 1024 ** 3)).toBe('5.0 GB')
  })

  it('says how long ago, then the date', () => {
    const now = Date.UTC(2026, 9, 7, 12)
    expect(formatAgo(now - 20_000, now)).toBe('just now')
    expect(formatAgo(now - 12 * 60_000, now)).toBe('12 min ago')
    expect(formatAgo(now - 5 * 3_600_000, now)).toBe('5 h ago')
    expect(formatAgo(now - 3 * 86_400_000, now)).toBe('3 d ago')
    expect(formatAgo(now - 30 * 86_400_000, now)).toMatch(/^Sep \d+$/)
  })

  it('adds the year only when it is not this one', () => {
    const now = Date.UTC(2026, 9, 7, 12)
    expect(formatDate(Date.UTC(2026, 9, 7, 12), { now, timeZone: 'UTC' })).toBe('Oct 7')
    expect(formatDate(Date.UTC(2025, 11, 30, 12), { now, timeZone: 'UTC' })).toBe('Dec 30, 2025')
  })

  it('writes the time of day on a 24-hour clock', () => {
    expect(formatTime(Date.UTC(2026, 9, 7, 14, 5), 'UTC')).toBe('14:05')
    expect(formatTime(Date.UTC(2026, 9, 7, 0, 30), 'UTC')).toBe('00:30')
  })
})
