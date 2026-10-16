import { describe, expect, it } from 'vitest'
import { COLLAPSE_MS, COLLAPSE_MS_REDUCED, deadline, expandedFid, holdMs, IDLE, observe, tick, type Obs } from './frontierHold'

const obs = (o: Partial<Obs> & { done?: string[] } = {}): Obs => {
  const done = o.done ?? []
  return {
    epoch: 0,
    speed: 1,
    lastSeq: 10,
    frontierFid: 'a',
    doneCount: done.length,
    doneFids: new Set(done),
    ...o,
  }
}

describe('holdMs', () => {
  it('shortens the hold as the replay speeds up', () => {
    expect([1, 2, 4, 16].map(holdMs)).toEqual([1200, 1000, 850, 700])
  })
})

describe('observe', () => {
  const before = obs({ frontierFid: 'a' })
  const after = obs({ lastSeq: 11, frontierFid: null, done: ['a'] })

  it('holds the layer that just finished', () => {
    const h = observe(IDLE, before, after, 1000)
    expect(h).toEqual({ kind: 'hold', fid: 'a', until: 1000 + 1200 })
    expect(expandedFid(h, after)).toBe('a')
    expect(deadline(h)).toBe(2200)
  })

  it('holds even when the next function started in the same batch', () => {
    const next = obs({ lastSeq: 12, frontierFid: 'b', done: ['a'] })
    const h = observe(IDLE, before, next, 0)
    expect(expandedFid(h, next)).toBe('a')
  })

  it('replaces a running hold when the next layer finishes', () => {
    const held = observe(IDLE, before, after, 0)
    const p = obs({ frontierFid: 'b', done: ['a'] })
    const n = obs({ lastSeq: 30, frontierFid: null, done: ['a', 'b'] })
    expect(observe(held, p, n, 500)).toEqual({ kind: 'hold', fid: 'b', until: 500 + 1200 })
  })

  it('shows the new state at once after a jump: first render, epoch, speed', () => {
    const held = observe(IDLE, before, after, 0)
    expect(observe(held, null, after, 0)).toEqual(IDLE)
    expect(observe(held, after, { ...after, epoch: 1 }, 0)).toEqual(IDLE)
    expect(observe(held, after, { ...after, speed: 4 }, 0)).toEqual(IDLE)
  })

  it('does not hold when a batch finished more than one function', () => {
    const next = obs({ lastSeq: 80, frontierFid: 'c', done: ['a', 'b'] })
    expect(observe(IDLE, before, next, 0)).toEqual(IDLE)
    expect(expandedFid(IDLE, next)).toBe('c')
  })

  it('gives no hold to a function that started and finished inside one batch', () => {
    const p = obs({ frontierFid: null })
    const n = obs({ lastSeq: 40, frontierFid: null, done: ['a'] })
    expect(observe(IDLE, p, n, 0)).toEqual(IDLE)
  })

  it('keeps the hold through events that finish nothing', () => {
    const held = observe(IDLE, before, after, 0)
    const n = obs({ lastSeq: 12, frontierFid: 'b', done: ['a'] })
    expect(observe(held, after, n, 100)).toBe(held)
  })
})

describe('tick', () => {
  it('folds a hold, then goes idle', () => {
    const h = { kind: 'hold' as const, fid: 'a', until: 100 }
    expect(tick(h, 99, false)).toBe(h)
    const c = tick(h, 100, false)
    expect(c).toEqual({ kind: 'collapse', fid: 'a', until: 100 + COLLAPSE_MS })
    expect(expandedFid(c, obs({ frontierFid: 'b' }))).toBeNull()
    expect(tick(c, 100 + COLLAPSE_MS, false)).toEqual(IDLE)
    expect(deadline(IDLE)).toBeNull()
  })

  it('folds faster with reduced motion', () => {
    expect(tick({ kind: 'hold', fid: 'a', until: 0 }, 0, true)).toEqual({
      kind: 'collapse',
      fid: 'a',
      until: COLLAPSE_MS_REDUCED,
    })
  })
})
