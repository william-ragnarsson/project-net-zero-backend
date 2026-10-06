// Wires a recorded event log to a run session: driver -> per-frame pump -> store.
import type { RunEvent } from '../gen/events'
import { createEventPump, createRunSession, type RunSessionStore, type Scheduler } from '../state/store'
import { createReplayDriver, SPEEDS, type ReplayDriver, type Speed } from './replayDriver'

/** Where `?at=` opens the replay: the end, right after a seq, or a fraction of the timeline. */
export type ReplayAt = { kind: 'end' } | { kind: 'seq'; seq: number } | { kind: 'fraction'; fraction: number }

/** `end`, an integer seq (`120`), or a fraction written with a point (`0.5`); else null. */
export function parseAt(raw: string | null): ReplayAt | null {
  if (raw === null) return null
  const value = raw.trim()
  if (value === 'end') return { kind: 'end' }
  if (/^\d+$/.test(value)) return { kind: 'seq', seq: Number(value) }
  if (/^\d*\.\d+$/.test(value) && Number(value) <= 1) return { kind: 'fraction', fraction: Number(value) }
  return null
}

export function parseSpeed(raw: string | null): Speed {
  return SPEEDS.find((s) => String(s) === raw?.trim()) ?? 1
}

/** `?query` with its slashes left readable: a query may contain them, and src values are paths. */
export const readableQuery = (params: URLSearchParams): string => `?${params.toString().replaceAll('%2F', '/')}`

export const replayHref = (src: string): string => `/replay${readableQuery(new URLSearchParams({ src }))}`

/**
 * `src` as a path on `origin`, or null when it points elsewhere: a shared link must not
 * pass a third party's file off as a run of this app.
 */
export function sameOriginSrc(src: string, origin: string): string | null {
  try {
    const url = new URL(src, origin)
    return url.origin === origin ? `${url.pathname}${url.search}` : null
  } catch {
    return null
  }
}

export interface Replay {
  session: RunSessionStore
  driver: ReplayDriver
  /** with no `at`, the replay plays from the start; with one it opens paused there */
  autoplay: boolean
  /** Pauses and delivers what is still queued, so the store matches the driver. */
  stop(): void
}

export interface ReplayInit {
  speed: Speed
  at: ReplayAt | null
  schedule?: Scheduler
  now?: () => number
}

/** Builds the session at its opening position. Starts no timers: call `driver.play()`. */
export function createReplay(events: readonly RunEvent[], { speed, at, schedule, now }: ReplayInit): Replay {
  const session = createRunSession()
  const { hydrate, dispatchBatch } = session.getState()
  const pump = createEventPump(dispatchBatch, schedule)
  const driver = createReplayDriver(
    events,
    {
      reset: (batch) => {
        pump.clear()
        hydrate(batch)
      },
      push: (batch) => pump.push(batch),
    },
    now === undefined ? { speed } : { speed, now },
  )
  if (at?.kind === 'end') driver.seekEnd()
  else if (at?.kind === 'seq') driver.seekSeq(at.seq)
  else if (at?.kind === 'fraction') driver.seekFraction(at.fraction)
  return {
    session,
    driver,
    autoplay: at === null,
    stop: () => {
      driver.pause()
      pump.flush()
    },
  }
}
