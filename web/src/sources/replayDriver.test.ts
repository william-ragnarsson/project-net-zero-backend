import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { RunEvent } from '../gen/events'
import { makeLog } from '../test/events'
import { createReplayDriver, MAX_GAP_MS, timeline, type ReplaySink } from './replayDriver'

const T0 = 1_767_225_600_000
// gaps of 100, 5000 (capped to 1500), 200 and 0 ms: offsets 0, 100, 1600, 1800, 1800
const events = [0, 100, 5100, 5300, 5300].map((ts, i) => makeLog(i + 1, `line ${i + 1}`, { ts: T0 + ts }))

/** A sink that keeps what a store would hold: the seqs delivered since the last reset. */
function recorder() {
  let shown: RunEvent[] = []
  const sink: ReplaySink = {
    reset: (batch) => {
      shown = [...batch]
    },
    push: (batch) => {
      shown.push(...batch)
    },
  }
  return { sink, seqs: () => shown.map((e) => e.seq) }
}

function setup(speed: 1 | 4 | 16 = 1) {
  const { sink, seqs } = recorder()
  const driver = createReplayDriver(events, sink, { speed, now: () => Date.now() })
  return { driver, seqs }
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('timeline', () => {
  it('keeps real gaps up to MAX_GAP_MS and never runs backwards', () => {
    expect(timeline(events)).toEqual([0, 100, 100 + MAX_GAP_MS, 1800, 1800])
    const shuffled = [events[1], events[0]].filter((e) => e !== undefined)
    expect(timeline(shuffled)).toEqual([0, 0])
  })
})

describe('createReplayDriver', () => {
  it('delivers events as the virtual clock reaches them', () => {
    const { driver, seqs } = setup()
    expect(driver.getStatus()).toMatchObject({ playing: false, cursor: 0, seq: 0, total: 5, durationMs: 1800 })
    driver.play()
    expect(seqs()).toEqual([1])
    vi.advanceTimersByTime(99)
    expect(seqs()).toEqual([1])
    vi.advanceTimersByTime(1)
    expect(seqs()).toEqual([1, 2])
    vi.advanceTimersByTime(1499)
    expect(seqs()).toEqual([1, 2])
    vi.advanceTimersByTime(1)
    expect(seqs()).toEqual([1, 2, 3])
    vi.advanceTimersByTime(200)
    expect(seqs()).toEqual([1, 2, 3, 4, 5])
    expect(driver.getStatus()).toMatchObject({ playing: false, cursor: 5, seq: 5, positionMs: 1800 })
  })

  it('plays faster at higher speeds', () => {
    const { driver, seqs } = setup(16)
    driver.play()
    vi.advanceTimersByTime(100)
    expect(seqs()).toEqual([1, 2, 3])
    vi.advanceTimersByTime(13)
    expect(seqs()).toEqual([1, 2, 3, 4, 5])
  })

  it('changes speed without jumping', () => {
    const { driver, seqs } = setup()
    driver.play()
    vi.advanceTimersByTime(50)
    driver.setSpeed(4)
    expect(driver.position()).toBe(50)
    vi.advanceTimersByTime(12)
    expect(seqs()).toEqual([1])
    vi.advanceTimersByTime(1)
    expect(seqs()).toEqual([1, 2])
    expect(driver.getStatus().speed).toBe(4)
  })

  it('holds its position while paused', () => {
    const { driver, seqs } = setup()
    driver.play()
    vi.advanceTimersByTime(50)
    driver.pause()
    vi.advanceTimersByTime(10_000)
    expect(seqs()).toEqual([1])
    expect(driver.getStatus()).toMatchObject({ playing: false, positionMs: 50 })
    driver.play()
    vi.advanceTimersByTime(49)
    expect(seqs()).toEqual([1])
    vi.advanceTimersByTime(1)
    expect(seqs()).toEqual([1, 2])
  })

  it('seeks by seq, fraction and step, rebuilding from the start', () => {
    const { driver, seqs } = setup()
    driver.seekSeq(2)
    expect(seqs()).toEqual([1, 2])
    expect(driver.getStatus()).toMatchObject({ playing: false, cursor: 2, seq: 2, positionMs: 100 })
    driver.seekFraction(0.5)
    expect(seqs()).toEqual([1, 2])
    expect(driver.getStatus().positionMs).toBe(900)
    driver.seekFraction(0.9)
    expect(seqs()).toEqual([1, 2, 3])
    driver.seekEnd()
    expect(seqs()).toEqual([1, 2, 3, 4, 5])
    driver.step(-1)
    expect(seqs()).toEqual([1, 2, 3, 4])
    driver.step(-10)
    expect(seqs()).toEqual([])
    driver.seekFraction(0)
    expect(seqs()).toEqual([1])
    driver.seekStart()
    expect(seqs()).toEqual([])
    expect(driver.getStatus()).toMatchObject({ cursor: 0, positionMs: 0 })
    driver.seekSeq(99)
    expect(seqs()).toEqual([1, 2, 3, 4, 5])
    vi.advanceTimersByTime(10_000)
    expect(driver.getStatus().playing).toBe(false)
  })

  it('keeps playing from where a seek lands', () => {
    const { driver, seqs } = setup()
    driver.play()
    driver.seekSeq(3)
    expect(seqs()).toEqual([1, 2, 3])
    expect(driver.getStatus().playing).toBe(true)
    vi.advanceTimersByTime(199)
    expect(seqs()).toEqual([1, 2, 3])
    vi.advanceTimersByTime(1)
    expect(seqs()).toEqual([1, 2, 3, 4, 5])
  })

  it('restarts from the beginning when played at the end', () => {
    const { driver, seqs } = setup()
    driver.seekEnd()
    driver.play()
    expect(seqs()).toEqual([1])
    expect(driver.getStatus()).toMatchObject({ playing: true, cursor: 1 })
  })

  it('notifies subscribers with a new status only when it changes', () => {
    const { driver } = setup()
    const listener = vi.fn()
    const unsubscribe = driver.subscribe(listener)
    const before = driver.getStatus()
    expect(driver.getStatus()).toBe(before)
    driver.seekSeq(1)
    expect(listener).toHaveBeenCalledTimes(1)
    expect(driver.getStatus()).not.toBe(before)
    unsubscribe()
    driver.seekEnd()
    expect(listener).toHaveBeenCalledTimes(1)
  })
})
