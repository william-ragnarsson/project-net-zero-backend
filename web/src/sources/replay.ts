// Wires a recorded event log to a run session: driver -> per-frame pump -> store.
import type { ReplayRef, RunEvent } from '../gen/events'
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

export const replayHref = (id: string): string => `/replays/${encodeURIComponent(id)}`

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

/** The params a replay link carries over to the replay's own page. */
const KEPT = ['speed', 'at', 'node'] as const

/**
 * Where an old `/replay?src=` link goes now: the page of the index entry with that file, with
 * `speed`, `at` and `node` kept, or the list when the file is foreign or not in the index.
 */
export function resolveReplayRedirect(index: readonly ReplayRef[], params: URLSearchParams, origin: string): string {
  const raw = params.get('src')
  const src = raw === null ? null : sameOriginSrc(raw, origin)
  const entry = src === null ? undefined : index.find((r) => sameOriginSrc(r.src, origin) === src)
  if (entry === undefined) return '/replays'
  const rest = new URLSearchParams()
  for (const key of KEPT) {
    const value = params.get(key)
    if (value !== null) rest.set(key, value)
  }
  return `${replayHref(entry.id)}${rest.size === 0 ? '' : readableQuery(rest)}`
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
