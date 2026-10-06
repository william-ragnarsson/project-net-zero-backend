import { createStore, type StoreApi } from 'zustand/vanilla'
import type { RunEvent } from '../gen/events'
import { initialRunModel, reduce } from './reducer'
import type { RunModel } from './types'

/** One run being watched: a live stream or a replay. */
export interface RunSession {
  state: RunModel
  /** the last seq folded in by `hydrate`, where a live stream resumes */
  hydratedThroughSeq: number
  /** Replaces the state with a fresh reduction of `events` (history load, replay seek). */
  hydrate(events: readonly RunEvent[]): void
  dispatchBatch(events: readonly RunEvent[]): void
}

export type RunSessionStore = StoreApi<RunSession>

export function createRunSession(): RunSessionStore {
  return createStore<RunSession>()((set) => ({
    state: initialRunModel(),
    hydratedThroughSeq: 0,
    hydrate: (events) => {
      const state = reduce(initialRunModel(), events)
      set({ state, hydratedThroughSeq: state.lastSeq })
    },
    // returning the same session object skips the notification when nothing changed
    dispatchBatch: (events) =>
      set((session) => {
        const state = reduce(session.state, events)
        return state === session.state ? session : { state }
      }),
  }))
}

/** Schedules `flush` once and returns a cancel function. */
export type Scheduler = (flush: () => void) => () => void

const nextFrame: Scheduler = (flush) => {
  const id = requestAnimationFrame(flush)
  return () => cancelAnimationFrame(id)
}

export interface EventPump {
  push(events: readonly RunEvent[]): void
  /** Dispatches whatever is queued now. */
  flush(): void
  /** Drops whatever is queued (before a seek re-hydrates the store). */
  clear(): void
}

/**
 * Collects events and dispatches them once per animation frame, so a burst of events (a
 * fast replay, a reconnect backlog) costs one reduction and one render per frame.
 */
export function createEventPump(
  dispatch: (events: readonly RunEvent[]) => void,
  schedule: Scheduler = nextFrame,
): EventPump {
  let queue: RunEvent[] = []
  let cancel: (() => void) | null = null

  const clear = () => {
    cancel?.()
    cancel = null
    queue = []
  }
  const flush = () => {
    const batch = queue
    clear()
    if (batch.length > 0) dispatch(batch)
  }
  return {
    push: (events) => {
      if (events.length === 0) return
      queue.push(...events)
      cancel ??= schedule(flush)
    },
    flush,
    clear,
  }
}
