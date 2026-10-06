import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { initialRunModel, reduce } from '../state/reducer'
import type { Scheduler } from '../state/store'
import { buildSyntheticRun } from '../synth/scenario'
import { createReplay, parseAt, parseSpeed, readableQuery, replayHref, sameOriginSrc } from './replay'

const { events } = buildSyntheticRun()

describe('replay URL parameters', () => {
  it('reads ?at= as the end, a seq or a fraction', () => {
    expect(parseAt(null)).toBeNull()
    expect(parseAt('end')).toEqual({ kind: 'end' })
    expect(parseAt('120')).toEqual({ kind: 'seq', seq: 120 })
    expect(parseAt('0.5')).toEqual({ kind: 'fraction', fraction: 0.5 })
    expect(parseAt('.25')).toEqual({ kind: 'fraction', fraction: 0.25 })
    for (const bad of ['', '1.5', '-3', 'start', '12abc']) expect(parseAt(bad)).toBeNull()
  })

  it('reads ?speed= as one of the offered speeds, else 1', () => {
    expect(parseSpeed('16')).toBe(16)
    expect(parseSpeed('4')).toBe(4)
    expect(parseSpeed('3')).toBe(1)
    expect(parseSpeed(null)).toBe(1)
  })

  it('links to a replay with a readable src', () => {
    expect(replayHref('/replays/a b&c.events.jsonl')).toBe('/replay?src=/replays/a+b%26c.events.jsonl')
    expect(readableQuery(new URLSearchParams({ src: '/replays/x.jsonl', speed: '4' }))).toBe(
      '?src=/replays/x.jsonl&speed=4',
    )
  })

  it('only loads replays from its own origin', () => {
    const origin = 'http://127.0.0.1:8000'
    expect(sameOriginSrc('/replays/x.jsonl', origin)).toBe('/replays/x.jsonl')
    expect(sameOriginSrc('replays/x.jsonl?v=2', origin)).toBe('/replays/x.jsonl?v=2')
    expect(sameOriginSrc('http://127.0.0.1:8000/replays/x.jsonl', origin)).toBe('/replays/x.jsonl')
    expect(sameOriginSrc('https://evil.example/x.jsonl', origin)).toBeNull()
    expect(sameOriginSrc('//evil.example/x.jsonl', origin)).toBeNull()
  })
})

describe('createReplay', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  const fold = (n: number) => reduce(initialRunModel(), events.slice(0, n))

  it('opens at ?at= without starting playback', () => {
    const atEnd = createReplay(events, { speed: 1, at: { kind: 'end' } })
    expect(atEnd.autoplay).toBe(false)
    expect(atEnd.session.getState().state).toEqual(fold(events.length))
    expect(atEnd.driver.getStatus().playing).toBe(false)

    const atSeq = createReplay(events, { speed: 1, at: { kind: 'seq', seq: 42 } })
    expect(atSeq.session.getState().state.lastSeq).toBe(42)

    const fromStart = createReplay(events, { speed: 1, at: null })
    expect(fromStart.autoplay).toBe(true)
    expect(fromStart.session.getState().state.lastSeq).toBe(0)
  })

  it('feeds the store once per frame and catches up on stop', () => {
    const frames: (() => void)[] = []
    const schedule: Scheduler = (flush) => {
      frames.push(flush)
      return () => frames.splice(frames.indexOf(flush), 1)
    }
    const replay = createReplay(events, { speed: 16, at: null, schedule, now: () => Date.now() })
    replay.driver.play()
    vi.advanceTimersByTime(60_000)
    const delivered = replay.driver.getStatus().cursor
    expect(delivered).toBeGreaterThan(1)
    expect(replay.session.getState().state.lastSeq).toBe(0)
    expect(frames).toHaveLength(1)

    frames.shift()?.()
    expect(replay.session.getState().state).toEqual(fold(delivered))

    vi.advanceTimersByTime(1_000)
    replay.stop()
    expect(replay.driver.getStatus().playing).toBe(false)
    expect(replay.session.getState().state).toEqual(fold(replay.driver.getStatus().cursor))
  })

  it('drops queued events when a seek rebuilds the state', () => {
    const frames = new Set<() => void>()
    const schedule: Scheduler = (flush) => {
      frames.add(flush)
      return () => frames.delete(flush)
    }
    const replay = createReplay(events, { speed: 16, at: null, schedule, now: () => Date.now() })
    replay.driver.play()
    vi.advanceTimersByTime(60_000)
    expect(replay.driver.getStatus().seq).toBeGreaterThan(10)
    replay.driver.seekSeq(10)
    frames.forEach((flush) => flush())
    expect(replay.session.getState().state).toEqual(fold(10))
  })
})
