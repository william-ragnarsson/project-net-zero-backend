// A run as it happens: hydrate from events.jsonl, then follow the SSE stream from there.
// The log on the server is the source of truth, so every path back from a gap re-reads it
// (`?after=`, `Last-Event-ID`) and the reducer drops whatever arrives twice.
import { eventsUrl, eventStreamUrl } from '../api/client'
import type { Heartbeat, RunDetail, RunEvent } from '../gen/events'
import { parseJsonl } from '../lib/jsonl'
import { SHOWCASE } from '../showcase/config'
import { createEventPump, type RunSessionStore, type Scheduler } from '../state/store'
import { isEventLike } from './replayFile'

export type LiveStatus =
  | { kind: 'loading' }
  | { kind: 'streaming' }
  | { kind: 'reconnecting'; attempt: number }
  | { kind: 'ended' }
  | { kind: 'not_found' }
  | { kind: 'not_ready'; retryInMs: number }
  | { kind: 'error'; message: string }

/** The part of EventSource the live source uses. */
export interface EventSourceLike {
  readonly readyState: number
  close(): void
  addEventListener(type: string, listener: (e: MessageEvent) => void): void
}

export interface LiveDeps {
  fetch?: typeof fetch
  EventSource?: new (url: string) => EventSourceLike
  /** when the pump delivers; one animation frame by default */
  schedule?: Scheduler
  setTimeout?: (fn: () => void, ms: number) => unknown
  clearTimeout?: (id: unknown) => void
  /** the browser's clock, for `serverNow` */
  now?: () => number
}

export interface LiveConnection {
  getStatus(): LiveStatus
  subscribe(fn: () => void): () => void
  /**
   * The server's clock now, as far as the stream has shown it: the newest heartbeat or live
   * event, carried forward by the browser's clock. Null until one arrives; a hydrated log
   * says nothing about now.
   */
  serverNow(): number | null
  /** Closes everything. Safe to call twice; callbacks that land later do nothing. */
  stop(): void
}

const CONNECTING = 0
const CLOSED = 2

const TERMINAL_TYPES: ReadonlySet<string> = new Set(['run.completed', 'run.failed', 'run.cancelled', 'run.interrupted'])
const TERMINAL_STATES: ReadonlySet<string> = new Set(['completed', 'failed', 'cancelled', 'interrupted'])

function parseHeartbeat(data: unknown): Heartbeat | null {
  try {
    const v: unknown = JSON.parse(String(data))
    if (typeof v !== 'object' || v === null) return null
    const hb = v as Partial<Heartbeat>
    return typeof hb.run_id === 'string' && typeof hb.last_seq === 'number' && typeof hb.server_ts === 'number'
      ? (hb as Heartbeat)
      : null
  } catch {
    return null
  }
}

/** 1s, 2s, 4s, then 10s at most. */
export const backoffMs = (attempt: number): number => Math.min(1000 * 2 ** attempt, 10_000)

/** A non-OK answer, with the server's code when it sent one. */
class HttpFailure extends Error {
  readonly status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function failure(res: Response): Promise<HttpFailure> {
  let detail = res.statusText || `HTTP ${res.status}`
  try {
    const body: unknown = await res.json()
    if (typeof body === 'object' && body !== null && typeof (body as { detail?: unknown }).detail === 'string') {
      detail = (body as { detail: string }).detail
    }
  } catch {
    // not JSON
  }
  return new HttpFailure(res.status, detail)
}

export function connectLive(session: RunSessionStore, runId: string, deps: LiveDeps = {}): LiveConnection {
  // globals are read when used, so a test can stub them after import
  const doFetch: typeof fetch = (input, init) => (deps.fetch ?? globalThis.fetch)(input, init)
  const later = (fn: () => void, ms: number) => (deps.setTimeout ?? globalThis.setTimeout)(fn, ms)
  const cancelLater = (id: unknown) => (deps.clearTimeout ?? globalThis.clearTimeout)(id as number)
  const now = () => (deps.now ?? Date.now)()

  let status: LiveStatus = { kind: 'loading' }
  const listeners = new Set<() => void>()
  let stopped = false
  let es: EventSourceLike | null = null
  let abort: AbortController | null = null
  let timer: unknown = null
  /** the newest seq accepted here, queued or dispatched; the stream resumes after it */
  let lastSeq = 0
  /** server clock minus browser clock; every sighting is a lower bound, so the largest wins */
  let serverOffset: number | null = null
  let opens = 0
  let failures = 0
  let discontinuousNext = false

  const sawServerTime = (serverTs: number) => {
    const offset = serverTs - now()
    if (serverOffset === null || offset > serverOffset) serverOffset = offset
  }

  const setStatus = (next: LiveStatus) => {
    if (stopped) return
    status = next
    for (const fn of [...listeners]) fn()
  }

  // an all-duplicate batch leaves the state alone, so the flag waits for one that changes it
  const pump = createEventPump((batch) => {
    const changed = session.getState().dispatchBatch(batch, { discontinuous: discontinuousNext })
    if (changed) discontinuousNext = false
  }, deps.schedule)

  const schedule = (fn: () => void, ms: number) => {
    if (timer !== null) cancelLater(timer)
    timer = later(() => {
      timer = null
      if (!stopped) fn()
    }, ms)
  }

  const isTerminal = () => session.getState().state.terminal !== null

  async function hydrate(final = false) {
    abort?.abort()
    const ac = new AbortController()
    abort = ac
    let text: string
    try {
      const res = await doFetch(eventsUrl(runId), { signal: ac.signal })
      if (!res.ok) throw await failure(res)
      text = await res.text()
    } catch (e) {
      if (stopped || ac.signal.aborted) return
      if (e instanceof HttpFailure && e.status === 404) return setStatus({ kind: 'not_found' })
      if (e instanceof HttpFailure && e.status !== 412) return setStatus({ kind: 'error', message: e.message })
      // 412 (the server is still starting) or no answer at all: try again shortly
      const wait = backoffMs(failures++)
      setStatus({ kind: 'not_ready', retryInMs: wait })
      return schedule(() => void hydrate(final), wait)
    }
    if (stopped) return
    const events = parseJsonl(text)
      .values.filter((v): v is RunEvent => isEventLike(v) && v.run_id === runId)
      .sort((a, b) => a.seq - b.seq)
    pump.clear()
    session.getState().hydrate(events)
    failures = 0
    lastSeq = session.getState().hydratedThroughSeq
    // a finished run's stream would only answer 204, and the showcase has no stream
    if (final || isTerminal() || SHOWCASE) return setStatus({ kind: 'ended' })
    if (status.kind === 'not_ready') setStatus({ kind: 'loading' })
    open()
  }

  function open() {
    if (stopped) return
    const Source = deps.EventSource ?? (globalThis.EventSource as unknown as new (url: string) => EventSourceLike)
    const source = new Source(eventStreamUrl(runId, lastSeq))
    es = source
    source.addEventListener('open', () => {
      if (stopped || es !== source) return
      // after any reconnect, events are backlog until a heartbeat says otherwise; backlog must not animate
      if (opens++ > 0) discontinuousNext = true
      failures = 0
      setStatus({ kind: 'streaming' })
    })
    source.addEventListener('message', (msg) => {
      if (stopped || es !== source) return
      let value: unknown
      try {
        value = JSON.parse(String(msg.data))
      } catch {
        return
      }
      if (!isEventLike(value) || value.run_id !== runId || value.seq <= lastSeq) return
      lastSeq = value.seq
      sawServerTime(value.ts)
      pump.push([value])
      if (TERMINAL_TYPES.has(value.type)) {
        source.close()
        es = null
        pump.flush()
        setStatus({ kind: 'ended' })
      }
    })
    // the server sends one right after the backlog, then one per quiet spell
    source.addEventListener('heartbeat', (msg) => {
      if (stopped || es !== source) return
      const hb = parseHeartbeat(msg.data)
      if (hb === null || hb.run_id !== runId) return
      sawServerTime(hb.server_ts)
      if (hb.last_seq > lastSeq) return
      // caught up: what is queued ends the backlog, and whatever comes next is live
      pump.flush()
      discontinuousNext = false
    })
    source.addEventListener('error', () => {
      if (stopped || es !== source) return
      if (source.readyState === CONNECTING) {
        // the browser retries by itself and resumes with Last-Event-ID
        setStatus({ kind: 'reconnecting', attempt: ++failures })
        return
      }
      if (source.readyState === CLOSED) {
        es = null
        void probe()
      }
    })
  }

  /** The browser gave up: a 204, a 404 and a 5xx all look the same, so ask the server. */
  async function probe() {
    abort?.abort()
    const ac = new AbortController()
    abort = ac
    let detail: RunDetail | null = null
    let missing = false
    try {
      const res = await doFetch(`/api/runs/${encodeURIComponent(runId)}`, { signal: ac.signal })
      if (res.status === 404) missing = true
      else if (res.ok) detail = (await res.json()) as RunDetail
    } catch {
      // the server is down: back off below
    }
    if (stopped || ac.signal.aborted) return
    if (missing) return setStatus({ kind: 'not_found' })
    if (detail !== null && TERMINAL_STATES.has(detail.state) && lastSeq >= detail.last_seq) {
      pump.flush()
      if (isTerminal()) return setStatus({ kind: 'ended' })
      return void hydrate(true)
    }
    const attempt = ++failures
    setStatus({ kind: 'reconnecting', attempt })
    schedule(open, backoffMs(attempt - 1))
  }

  void hydrate()

  return {
    getStatus: () => status,
    subscribe: (fn) => {
      listeners.add(fn)
      return () => listeners.delete(fn)
    },
    serverNow: () => (serverOffset === null ? null : now() + serverOffset),
    stop: () => {
      if (stopped) return
      stopped = true
      abort?.abort()
      es?.close()
      es = null
      if (timer !== null) cancelLater(timer)
      timer = null
      pump.clear()
      listeners.clear()
    },
  }
}
