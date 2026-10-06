// Plays a recorded event log back on a virtual clock taken from the events' timestamps.
import type { RunEvent } from '../gen/events'

export const SPEEDS = [1, 4, 16] as const
export type Speed = (typeof SPEEDS)[number]

/** Idle stretches (an LLM call, the user reading the triage) play back at most this long. */
export const MAX_GAP_MS = 1500

/** Where the driver sends events: `reset` rebuilds the state from zero, `push` appends. */
export interface ReplaySink {
  reset(events: readonly RunEvent[]): void
  push(events: readonly RunEvent[]): void
}

export interface ReplayStatus {
  playing: boolean
  speed: Speed
  /** events delivered so far */
  cursor: number
  /** seq of the last delivered event; 0 before the first */
  seq: number
  /** position on the replay's timeline, in virtual ms */
  positionMs: number
  durationMs: number
  total: number
}

export interface ReplayDriver {
  getStatus(): ReplayStatus
  /** The live position in virtual ms; the status only refreshes when events arrive. */
  position(): number
  subscribe(listener: () => void): () => void
  play(): void
  pause(): void
  setSpeed(speed: Speed): void
  /** Shows the run as it was right after `seq`. */
  seekSeq(seq: number): void
  /** Moves `delta` events forward (or back, when negative). */
  step(delta: number): void
  /** Jumps to a point on the timeline, 0 to 1. */
  seekFraction(fraction: number): void
  /** Before the first event, unlike `seekFraction(0)`, which shows the events stamped at 0 ms. */
  seekStart(): void
  seekEnd(): void
}

export interface ReplayOptions {
  speed?: Speed
  now?: () => number
}

/** Each event's offset on the replay timeline: real gaps, capped at MAX_GAP_MS. */
export function timeline(events: readonly RunEvent[]): number[] {
  let at = 0
  return events.map((e, i) => {
    const prev = events[i - 1]
    if (prev !== undefined) at += Math.min(Math.max(e.ts - prev.ts, 0), MAX_GAP_MS)
    return at
  })
}

export function createReplayDriver(
  events: readonly RunEvent[],
  sink: ReplaySink,
  { speed: initialSpeed = 1, now = () => performance.now() }: ReplayOptions = {},
): ReplayDriver {
  const offsets = timeline(events)
  const durationMs = offsets.at(-1) ?? 0
  const listeners = new Set<() => void>()

  let speed: Speed = initialSpeed
  let cursor = 0
  let playing = false
  // while playing, the position is anchorMs plus the wall time since anchorWall, scaled
  let anchorMs = 0
  let anchorWall = 0
  let timer: ReturnType<typeof setTimeout> | null = null
  let status = snapshot()

  function position(): number {
    return playing ? Math.min(anchorMs + (now() - anchorWall) * speed, durationMs) : anchorMs
  }

  function snapshot(): ReplayStatus {
    return {
      playing,
      speed,
      cursor,
      seq: events[cursor - 1]?.seq ?? 0,
      positionMs: position(),
      durationMs,
      total: events.length,
    }
  }

  function notify(): void {
    status = snapshot()
    listeners.forEach((listener) => listener())
  }

  function stopTimer(): void {
    if (timer !== null) clearTimeout(timer)
    timer = null
  }

  function anchor(ms: number): void {
    anchorMs = ms
    anchorWall = now()
  }

  /** Delivers every event due by now, then sleeps until the next one. */
  function tick(): void {
    timer = null
    const at = position()
    let next = cursor
    while (next < events.length && (offsets[next] ?? 0) <= at) next++
    if (next > cursor) {
      sink.push(events.slice(cursor, next))
      cursor = next
    }
    if (cursor >= events.length) {
      anchor(durationMs)
      playing = false
    } else {
      // rounded up, so the clock has reached the event when the timer fires
      timer = setTimeout(tick, Math.max(1, Math.ceil(((offsets[cursor] ?? at) - at) / speed)))
    }
    notify()
  }

  /** Rebuilds the state from the first `index` events and parks the clock at `ms`. */
  function seekTo(index: number, ms: number): void {
    stopTimer()
    cursor = index
    sink.reset(events.slice(0, cursor))
    anchor(ms)
    if (playing && cursor < events.length) tick()
    else {
      playing = false
      notify()
    }
  }

  /** Parks the clock on the `index`-th event, so it is the last one shown. */
  function seekIndex(index: number): void {
    const k = Math.max(0, Math.min(index, events.length))
    seekTo(k, k === 0 ? 0 : (offsets[k - 1] ?? 0))
  }

  return {
    getStatus: () => status,
    position,
    subscribe(listener) {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    play() {
      if (playing) return
      if (cursor >= events.length) seekTo(0, 0)
      playing = true
      anchor(anchorMs)
      tick()
    },
    pause() {
      if (!playing) return
      anchor(position())
      playing = false
      stopTimer()
      notify()
    },
    setSpeed(next) {
      anchor(position())
      speed = next
      if (playing) {
        stopTimer()
        tick()
      } else notify()
    },
    seekSeq(seq) {
      const index = events.findIndex((e) => e.seq > seq)
      seekIndex(index < 0 ? events.length : index)
    },
    step(delta) {
      seekIndex(cursor + delta)
    },
    seekFraction(fraction) {
      const ms = Math.min(Math.max(fraction, 0), 1) * durationMs
      const index = offsets.findIndex((o) => o > ms)
      seekTo(index < 0 ? events.length : index, ms)
    },
    seekStart() {
      seekTo(0, 0)
    },
    seekEnd() {
      seekTo(events.length, durationMs)
    },
  }
}
