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
