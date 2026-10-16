import { createStore, type StoreApi } from 'zustand/vanilla'
import type { RunEvent } from '../gen/events'
import { initialRunModel, reduce } from './reducer'
import type { RunModel } from './types'

/** One run being watched: a live stream or a replay. */
export interface RunSession {
  state: RunModel
  /** the last seq folded in by `hydrate`, where a live stream resumes */
  hydratedThroughSeq: number
  /**
   * Bumped by `hydrate` and by a discontinuous dispatch that changed the state. A view must
   * not animate across an epoch change: the state jumped (a seek, a reconnect backlog).
   */
  epoch: number
  /** Replaces the state with a fresh reduction of `events` (history load, replay seek). */
  hydrate(events: readonly RunEvent[]): void
  /**
   * Folds `events` in. Returns whether the state changed. With `discontinuous`, the epoch is
   * bumped in the same update, but only when the state changed: an all-duplicate batch leaves
   * the session untouched and returns false, so the caller keeps the flag for the next batch.
   */
  dispatchBatch(events: readonly RunEvent[], opts?: DispatchOptions): boolean
}

export interface DispatchOptions {
  discontinuous?: boolean
}

export type RunSessionStore = StoreApi<RunSession>

export function createRunSession(): RunSessionStore {
  return createStore<RunSession>()((set) => ({
    state: initialRunModel(),
    hydratedThroughSeq: 0,
    epoch: 0,
    hydrate: (events) => {
      const state = reduce(initialRunModel(), events)
      set((session) => ({ state, hydratedThroughSeq: state.lastSeq, epoch: session.epoch + 1 }))
    },
    // returning the same session object skips the notification when nothing changed
    dispatchBatch: (events, opts) => {
      let changed = false
      set((session) => {
        const state = reduce(session.state, events)
        if (state === session.state) return session
        changed = true
        return opts?.discontinuous === true ? { state, epoch: session.epoch + 1 } : { state }
      })
      return changed
    },
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
