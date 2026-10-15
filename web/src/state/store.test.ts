import { describe, expect, it, vi } from 'vitest'
import type { RunEvent } from '../gen/events'
import { buildSyntheticRun } from '../synth/scenario'
import { initialRunModel, reduce } from './reducer'
import { createEventPump, createRunSession } from './store'

const { events } = buildSyntheticRun()

/** A scheduler driven by the test: `frame()` runs what is scheduled, like one animation frame. */
function manualScheduler() {
  let pending: (() => void) | null = null
  const schedule = vi.fn((flush: () => void) => {
    pending = flush
    return () => {
      pending = null
    }
  })
  const frame = () => {
    const run = pending
    pending = null
    run?.()
  }
  return { schedule, frame, isPending: () => pending !== null }
}

describe('createRunSession', () => {
  it('folds batches and notifies once per change', () => {
    const session = createRunSession()
    const listener = vi.fn()
    session.subscribe(listener)
    session.getState().dispatchBatch(events.slice(0, 50))
    session.getState().dispatchBatch(events.slice(0, 50))
    expect(listener).toHaveBeenCalledTimes(1)
    expect(session.getState().state).toEqual(reduce(initialRunModel(), events.slice(0, 50)))
  })

  it('hydrate replaces the state and records where a stream resumes', () => {
    const session = createRunSession()
    session.getState().dispatchBatch(events.slice(0, 200))
    session.getState().hydrate(events.slice(0, 30))
    expect(session.getState().state.lastSeq).toBe(events[29]?.seq)
    expect(session.getState().hydratedThroughSeq).toBe(events[29]?.seq)
    expect(session.getState().state).toEqual(reduce(initialRunModel(), events.slice(0, 30)))
  })
})

describe('RunSession.epoch', () => {
  it('increments on hydrate and on a discontinuous batch that changes state, not on a normal one', () => {
    const session = createRunSession()
    expect(session.getState().epoch).toBe(0)
    expect(session.getState().dispatchBatch(events.slice(0, 10))).toBe(true)
    expect(session.getState().epoch).toBe(0)
    session.getState().hydrate(events.slice(0, 5))
    expect(session.getState().epoch).toBe(1)
    expect(session.getState().dispatchBatch(events.slice(5, 20), { discontinuous: true })).toBe(true)
    expect(session.getState().epoch).toBe(2)
    expect(session.getState().state).toEqual(reduce(initialRunModel(), events.slice(0, 20)))
  })

  it('an all-duplicate discontinuous batch leaves the session identical and reports the flag unconsumed', () => {
    const session = createRunSession()
    session.getState().dispatchBatch(events.slice(0, 20))
    const before = session.getState()
    const listener = vi.fn()
    session.subscribe(listener)
    expect(session.getState().dispatchBatch(events.slice(10, 20), { discontinuous: true })).toBe(false)
    expect(session.getState()).toBe(before)
    expect(listener).not.toHaveBeenCalled()
    expect(session.getState().dispatchBatch(events.slice(10, 21), { discontinuous: true })).toBe(true)
    expect(session.getState().epoch).toBe(1)
  })
})

describe('createEventPump', () => {
  it('dispatches everything pushed within a frame as one batch', () => {
    const { schedule, frame } = manualScheduler()
    const batches: RunEvent[][] = []
    const pump = createEventPump((b) => batches.push([...b]), schedule)
    pump.push(events.slice(0, 3))
    pump.push(events.slice(3, 5))
    pump.push([])
    expect(schedule).toHaveBeenCalledTimes(1)
    expect(batches).toEqual([])
    frame()
    expect(batches).toEqual([events.slice(0, 5)])
    pump.push(events.slice(5, 6))
    frame()
    expect(batches).toEqual([events.slice(0, 5), events.slice(5, 6)])
  })

  it('flush dispatches now and cancels the frame', () => {
    const { schedule, isPending } = manualScheduler()
    const dispatch = vi.fn()
    const pump = createEventPump(dispatch, schedule)
    pump.push(events.slice(0, 2))
    pump.flush()
    expect(dispatch).toHaveBeenCalledWith(events.slice(0, 2))
    expect(isPending()).toBe(false)
    pump.flush()
    expect(dispatch).toHaveBeenCalledTimes(1)
  })

  it('clear drops what is queued', () => {
    const { schedule, frame } = manualScheduler()
    const dispatch = vi.fn()
    const pump = createEventPump(dispatch, schedule)
    pump.push(events.slice(0, 2))
    pump.clear()
    frame()
    expect(dispatch).not.toHaveBeenCalled()
  })
})
