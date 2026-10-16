import { describe, expect, it } from 'vitest'
import {
  formatBytes,
  formatCI,
  formatClock,
  formatDate,
  formatGrams,
  formatInt,
  formatMass,
  formatP,
  formatPct,
  formatTime,
  formatUsd,
  formatWhen,
  formatAgo,
  massUnit,
  NANOGRAMS,
  NONE,
  sig3,
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

  it('writes three significant figures', () => {
    expect(sig3(0)).toBe('0')
    expect(sig3(123.4)).toBe('123')
    expect(sig3(12.34)).toBe('12.3')
    expect(sig3(1.234)).toBe('1.23')
    expect(sig3(0.01234)).toBe('0.0123')
    expect(sig3(-1.5)).toBe('−1.50')
  })

  it('formats grams per call in µg, or ng for tiny baselines', () => {
    expect(formatMass(12.34e-6)).toBe('12.3 µg')
    expect(massUnit(12e-6).unit).toBe('µg')
    expect(massUnit(0.05e-6)).toBe(NANOGRAMS)
    expect(massUnit(null).unit).toBe('µg')
    expect(formatMass(0.05e-6, NANOGRAMS)).toBe('50.0 ng')
  })

  it('formats intervals and p-values', () => {
    expect(formatCI({ lo: -12, hi: -4.06 })).toBe('−12.0% to −4.1%')
    expect(formatCI(null)).toBe(NONE)
    expect(formatP(null)).toBe(NONE)
    expect(formatP(1e-8)).toBe('< 1e-6')
    expect(formatP(0.00042)).toBe('4.2e-4')
    expect(formatP(0.0042)).toBe('0.0042')
    expect(formatP(0.042)).toBe('0.042')
  })

  it('writes when something happened, with the year only when it differs', () => {
    // local-time dates, so the test holds in any time zone
    const now = new Date(2026, 9, 13, 9, 0).getTime()
    expect(formatWhen(new Date(2026, 9, 12, 22, 9).getTime(), now)).toBe('12 Oct 22:09')
    expect(formatWhen(new Date(2026, 0, 3, 7, 5).getTime(), now)).toBe('3 Jan 07:05')
    expect(formatWhen(new Date(2025, 9, 12, 22, 9).getTime(), now)).toBe('12 Oct 2025')
  })

  it('says how long ago, in words or short', () => {
    const now = new Date(2026, 9, 13, 9, 0).getTime()
    const ago = (ms: number, short = false) => formatAgo(now - ms, now, short)
    expect(ago(20_000)).toBe('just now')
    expect(ago(5 * 60_000)).toBe('5 min ago')
    expect(ago(5 * 60_000, true)).toBe('5m')
    expect(ago(3 * 3_600_000)).toBe('3 h ago')
    expect(ago(26 * 3_600_000)).toBe('yesterday')
    expect(ago(3 * 86_400_000)).toBe('3 days ago')
    expect(ago(3 * 86_400_000, true)).toBe('3d')
    expect(formatAgo(new Date(2026, 8, 1, 12, 0).getTime(), now)).toBe('1 Sep')
    // a clock a little ahead of the server's never reads as the future
    expect(formatAgo(now + 5_000, now)).toBe('just now')
  })

  it('writes a day, and the time of day on a 24-hour clock', () => {
    const now = new Date(2026, 9, 13, 9, 0).getTime()
    expect(formatDate(new Date(2026, 9, 7, 12).getTime(), now)).toBe('7 Oct')
    expect(formatDate(new Date(2025, 11, 30, 12).getTime(), now)).toBe('30 Dec 2025')
    expect(formatTime(new Date(2026, 9, 7, 14, 5).getTime())).toBe('14:05')
    expect(formatTime(new Date(2026, 9, 7, 0, 30).getTime())).toBe('00:30')
  })

  it('sizes bytes', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(1023)).toBe('1023 B')
    expect(formatBytes(1536)).toBe('1.5 kB')
    expect(formatBytes(606_208)).toBe('592 kB')
    expect(formatBytes(5 * 1024 ** 3)).toBe('5.0 GB')
  })
})
