import { afterEach, describe, expect, it, vi } from 'vitest'
import type { RunEvent } from '../gen/events'
import { createRunSession } from '../state/store'
import { buildSyntheticRun } from '../synth/scenario'
import { backoffMs, connectLive, type EventSourceLike, type LiveDeps } from './live'

const { events } = buildSyntheticRun()
const RUN = events[0]!.run_id
const jsonl = (es: readonly RunEvent[]) => es.map((e) => JSON.stringify(e)).join('\n') + '\n'
const at = (seq: number) => events.find((e) => e.seq === seq)!
/** the selection is made: the run is optimizing from here */
const LIVE_UNTIL = 30

class FakeSource implements EventSourceLike {
  static all: FakeSource[] = []
  readonly url: string
  readyState = 0
  private listeners = new Map<string, ((e: MessageEvent) => void)[]>()
  constructor(url: string) {
    this.url = url
    FakeSource.all.push(this)
  }
  addEventListener(type: string, fn: (e: MessageEvent) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn])
  }
  close() {
    this.readyState = 2
  }
  emit(type: string, data?: string) {
    for (const fn of this.listeners.get(type) ?? []) fn({ data } as MessageEvent)
  }
  open() {
    this.readyState = 1
    this.emit('open')
  }
  send(...es: RunEvent[]) {
    for (const e of es) this.emit('message', JSON.stringify(e))
  }
  fail(readyState: 0 | 2) {
    this.readyState = readyState
    this.emit('error')
  }
}

const last = () => FakeSource.all.at(-1)!

/** what the server sends after the backlog on every connect, and after each quiet spell */
const heartbeat = (es: FakeSource, lastSeq: number, serverTs = at(LIVE_UNTIL).ts, runId = RUN) =>
  es.emit(
    'heartbeat',
    JSON.stringify({ type: 'heartbeat', run_id: runId, last_seq: lastSeq, state: 'optimizing', server_ts: serverTs }),
  )

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

function manualTimers() {
  let queue: { id: number; fn: () => void; ms: number }[] = []
  let next = 1
  return {
    setTimeout: (fn: () => void, ms: number) => {
      queue.push({ id: next, fn, ms })
      return next++
    },
    clearTimeout: (id: unknown) => {
      queue = queue.filter((t) => t.id !== id)
    },
    pending: () => queue.map((t) => t.ms),
    run: () => {
      const due = queue
      queue = []
      for (const t of due) t.fn()
    },
  }
}

type Route = Response | (() => Response) | Error

/** answers by path; each route is a queue whose last answer repeats */
function fakeFetch(routes: Record<string, Route[]>) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input)
    const queue = routes[path]
    if (queue === undefined || queue.length === 0) return new Response(null, { status: 404 })
    const answer = queue.length > 1 ? queue.shift()! : queue[0]!
    if (answer instanceof Error) throw answer
    return typeof answer === 'function' ? answer() : answer.clone()
  })
}

const LOG = `/api/runs/${RUN}/events.jsonl`
const DETAIL = `/api/runs/${RUN}`
const ok = (text: string) => new Response(text, { status: 200 })
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })

/** lets the fetch promises and their `.text()` settle */
const settle = () => new Promise((r) => setTimeout(r, 0))

function setup(routes: Record<string, Route[]>, extra: Partial<LiveDeps> = {}) {
  FakeSource.all = []
  const session = createRunSession()
  const sched = manualScheduler()
  const timers = manualTimers()
  const fetch = fakeFetch(routes)
  const conn = connectLive(session, RUN, {
    fetch: fetch as unknown as typeof globalThis.fetch,
    EventSource: FakeSource,
    schedule: sched.schedule,
    setTimeout: timers.setTimeout,
    clearTimeout: timers.clearTimeout,
    now: () => 5_000,
    ...extra,
  })
  return { session, sched, timers, fetch, conn }
}

let stops: (() => void)[] = []
afterEach(() => {
  for (const s of stops) s()
  stops = []
})

function start(routes: Record<string, Route[]>, extra: Partial<LiveDeps> = {}) {
  const s = setup(routes, extra)
  stops.push(s.conn.stop)
  return s
}

describe('backoffMs', () => {
  it('doubles from 1s and stops at 10s', () => {
    expect([0, 1, 2, 3, 4, 9].map(backoffMs)).toEqual([1000, 2000, 4000, 8000, 10_000, 10_000])
  })
})

describe('connectLive', () => {
  it('hydrates from the log, then opens the stream after its last seq', async () => {
    const { session, conn } = start({ [LOG]: [ok(jsonl(events.slice(0, LIVE_UNTIL)))] })
    expect(conn.getStatus().kind).toBe('loading')
    await settle()
    expect(session.getState().state.lastSeq).toBe(LIVE_UNTIL)
    expect(session.getState().hydratedThroughSeq).toBe(LIVE_UNTIL)
    expect(session.getState().epoch).toBe(1)
    // a hydrated log says when things happened, not what time it is now
    expect(conn.serverNow()).toBeNull()
    expect(FakeSource.all).toHaveLength(1)
    expect(last().url).toBe(`/api/runs/${RUN}/events?after=${LIVE_UNTIL}`)
    last().open()
    expect(conn.getStatus().kind).toBe('streaming')
  })

  it('delivers stream events only when the pump flushes', async () => {
    const { session, sched } = start({ [LOG]: [ok(jsonl(events.slice(0, LIVE_UNTIL)))] })
    await settle()
    last().open()
    last().send(at(31), at(32), at(33))
    expect(session.getState().state.lastSeq).toBe(LIVE_UNTIL)
    expect(sched.schedule).toHaveBeenCalledTimes(1)
    sched.frame()
    expect(session.getState().state.lastSeq).toBe(33)
    // a live batch is continuous: no epoch change
    expect(session.getState().epoch).toBe(1)
  })

  it('ignores duplicates, other runs and junk', async () => {
    const { session, sched } = start({ [LOG]: [ok(jsonl(events.slice(0, LIVE_UNTIL)))] })
    await settle()
    const es = last()
    es.open()
    es.emit('heartbeat', '')
    es.send(at(LIVE_UNTIL - 1), at(LIVE_UNTIL))
    es.send({ ...at(31), run_id: 'someone-else' })
    es.emit('message', 'not json')
    es.emit('message', JSON.stringify({ hello: 1 }))
    expect(sched.isPending()).toBe(false)
    es.send(at(31), at(31))
    sched.frame()
    expect(session.getState().state.lastSeq).toBe(31)
  })

  it('closes the stream on the terminal event and ends', async () => {
    const { session, sched, conn } = start({ [LOG]: [ok(jsonl(events.slice(0, LIVE_UNTIL)))] })
    await settle()
    const es = last()
    es.open()
    es.send(...events.slice(LIVE_UNTIL))
    // the terminal event flushes at once rather than waiting for a frame
    expect(sched.isPending()).toBe(false)
    expect(session.getState().state.terminal?.type).toBe('run.completed')
    expect(es.readyState).toBe(2)
    expect(conn.getStatus().kind).toBe('ended')
  })

  it('opens no stream for a finished run', async () => {
    const { session, conn } = start({ [LOG]: [ok(jsonl(events))] })
    await settle()
    expect(session.getState().state.terminal?.type).toBe('run.completed')
    expect(FakeSource.all).toHaveLength(0)
    expect(conn.getStatus().kind).toBe('ended')
  })

  it('reports a run that does not exist', async () => {
    const { conn } = start({})
    await settle()
    expect(conn.getStatus().kind).toBe('not_found')
    expect(FakeSource.all).toHaveLength(0)
  })

  it('retries a log that is not ready, backing off', async () => {
    const { session, timers, conn } = start({
      [LOG]: [json({ code: 'not_ready', detail: 'starting' }, 412), new TypeError('offline'), ok(jsonl(events.slice(0, LIVE_UNTIL)))],
    })
    await settle()
    expect(conn.getStatus()).toEqual({ kind: 'not_ready', retryInMs: 1000 })
    expect(timers.pending()).toEqual([1000])
    timers.run()
    await settle()
    expect(conn.getStatus()).toEqual({ kind: 'not_ready', retryInMs: 2000 })
    timers.run()
    await settle()
    expect(session.getState().state.lastSeq).toBe(LIVE_UNTIL)
    expect(conn.getStatus().kind).toBe('loading')
    last().open()
    expect(conn.getStatus().kind).toBe('streaming')
  })

  it('shows any other failure with its detail', async () => {
    const { conn } = start({ [LOG]: [json({ code: 'http', detail: 'disk on fire' }, 500)] })
    await settle()
    expect(conn.getStatus()).toEqual({ kind: 'error', message: 'disk on fire' })
  })

  it('says reconnecting while the browser retries, and marks the backlog discontinuous', async () => {
    const { session, sched, conn } = start({ [LOG]: [ok(jsonl(events.slice(0, LIVE_UNTIL)))] })
    await settle()
    const es = last()
    es.open()
    es.send(at(31))
    sched.frame()
    expect(session.getState().epoch).toBe(1)
    es.fail(0)
    expect(conn.getStatus()).toEqual({ kind: 'reconnecting', attempt: 1 })
    // the browser reconnects by itself and the server resends from Last-Event-ID
    es.open()
    expect(conn.getStatus().kind).toBe('streaming')
    es.send(at(31), at(32), at(33))
    sched.frame()
    expect(session.getState().state.lastSeq).toBe(33)
    expect(session.getState().epoch).toBe(2)
    es.send(at(34))
    sched.frame()
    expect(session.getState().epoch).toBe(2)
  })

  it('dispatches the backlog as one discontinuous batch when a heartbeat ends it', async () => {
    const { session, sched } = start({ [LOG]: [ok(jsonl(events.slice(0, LIVE_UNTIL)))] })
    await settle()
    const es = last()
    es.open()
    es.fail(0)
    es.open()
    es.send(at(31), at(32))
    heartbeat(es, 32)
    // at once, without waiting for a frame
    expect(sched.isPending()).toBe(false)
    expect(session.getState().state.lastSeq).toBe(32)
    expect(session.getState().epoch).toBe(2)
    es.send(at(33))
    sched.frame()
    expect(session.getState().epoch).toBe(2)
  })

  it('animates the first event after a quiet reconnect', async () => {
    const { session, sched } = start({ [LOG]: [ok(jsonl(events.slice(0, LIVE_UNTIL)))] })
    await settle()
    const es = last()
    es.open()
    es.fail(0)
    es.open()
    // nothing was missed, and the heartbeat after the (empty) backlog says so
    heartbeat(es, LIVE_UNTIL)
    es.send(at(31))
    sched.frame()
    expect(session.getState().state.lastSeq).toBe(31)
    expect(session.getState().epoch).toBe(1)
  })

  it('keeps events as backlog while a heartbeat shows more to come', async () => {
    const { session, sched } = start({ [LOG]: [ok(jsonl(events.slice(0, LIVE_UNTIL)))] })
    await settle()
    const es = last()
    es.open()
    es.fail(0)
    es.open()
    heartbeat(es, 35)
    heartbeat(es, LIVE_UNTIL, undefined, 'someone-else')
    es.emit('heartbeat', 'not json')
    es.send(at(31))
    sched.frame()
    expect(session.getState().epoch).toBe(2)
  })

  it('reads the server clock from heartbeats and live events', async () => {
    let clock = 5_000
    const { conn } = start({ [LOG]: [ok(jsonl(events.slice(0, LIVE_UNTIL)))] }, { now: () => clock })
    await settle()
    const es = last()
    es.open()
    const serverTs = at(LIVE_UNTIL).ts + 60_000
    heartbeat(es, LIVE_UNTIL, serverTs)
    expect(conn.serverNow()).toBe(serverTs)
    clock += 2_000
    expect(conn.serverNow()).toBe(serverTs + 2_000)
    // an event that took a while to arrive says less than what is already known
    es.send({ ...at(31), ts: serverTs - 30_000 })
    expect(conn.serverNow()).toBe(serverTs + 2_000)
    es.send({ ...at(32), ts: serverTs + 10_000 })
    expect(conn.serverNow()).toBe(serverTs + 10_000)
  })

  it('keeps the discontinuous mark through a batch that changes nothing', async () => {
    const { session, sched } = start({ [LOG]: [ok(jsonl(events.slice(0, LIVE_UNTIL)))] })
    await settle()
    const es = last()
    es.open()
    es.fail(0)
    es.open()
    es.send(at(31))
    // the batch reaches the store after a hydrate that already folded it in
    session.getState().hydrate(events.slice(0, 31))
    expect(session.getState().epoch).toBe(2)
    sched.frame()
    expect(session.getState().epoch).toBe(2)
    es.send(at(32))
    sched.frame()
    expect(session.getState().epoch).toBe(3)
  })

  it('probes the run when the browser gives up, and ends a finished one', async () => {
    const { session, fetch, conn } = start({
      [LOG]: [ok(jsonl(events.slice(0, LIVE_UNTIL))), ok(jsonl(events))],
      [DETAIL]: [json({ state: 'completed', last_seq: LIVE_UNTIL })],
    })
    await settle()
    // a 204: the server thinks the client has every event, but the model has no terminal
    last().fail(2)
    await settle()
    expect(fetch).toHaveBeenCalledWith(DETAIL, expect.anything())
    expect(session.getState().state.terminal?.type).toBe('run.completed')
    expect(conn.getStatus().kind).toBe('ended')
    expect(FakeSource.all).toHaveLength(1)
  })

  it('reports a run deleted while streaming', async () => {
    const { conn } = start({ [LOG]: [ok(jsonl(events.slice(0, LIVE_UNTIL)))] })
    await settle()
    last().fail(2)
    await settle()
    expect(conn.getStatus().kind).toBe('not_found')
  })

  it('reopens after its last seq when the run is still going', async () => {
    const { session, sched, timers, conn } = start({
      [LOG]: [ok(jsonl(events.slice(0, LIVE_UNTIL)))],
      [DETAIL]: [json({ state: 'optimizing', last_seq: 40 })],
    })
    await settle()
    const first = last()
    first.open()
    first.send(at(31))
    first.fail(2)
    await settle()
    expect(conn.getStatus()).toEqual({ kind: 'reconnecting', attempt: 1 })
    expect(timers.pending()).toEqual([1000])
    timers.run()
    expect(FakeSource.all).toHaveLength(2)
    expect(last().url).toBe(`/api/runs/${RUN}/events?after=31`)
    // the old source is dead: a late message from it does nothing
    first.send(at(32))
    last().open()
    last().send(at(32))
    sched.frame()
    expect(session.getState().state.lastSeq).toBe(32)
    expect(session.getState().epoch).toBe(2)
  })

  it('backs off when the probe itself fails', async () => {
    const { timers, conn } = start({
      [LOG]: [ok(jsonl(events.slice(0, LIVE_UNTIL)))],
      [DETAIL]: [new TypeError('offline')],
    })
    await settle()
    last().fail(2)
    await settle()
    expect(conn.getStatus()).toEqual({ kind: 'reconnecting', attempt: 1 })
    timers.run()
    last().fail(2)
    await settle()
    expect(conn.getStatus()).toEqual({ kind: 'reconnecting', attempt: 2 })
    expect(timers.pending()).toEqual([2000])
  })

  it('stops for good, and twice is fine', async () => {
    const { session, sched, timers, conn } = start({ [LOG]: [ok(jsonl(events.slice(0, LIVE_UNTIL)))] })
    const seen = vi.fn()
    conn.subscribe(seen)
    await settle()
    const es = last()
    es.open()
    seen.mockClear()
    es.send(at(31))
    conn.stop()
    conn.stop()
    expect(es.readyState).toBe(2)
    expect(sched.isPending()).toBe(false)
    es.send(at(32))
    es.fail(0)
    sched.frame()
    timers.run()
    expect(session.getState().state.lastSeq).toBe(LIVE_UNTIL)
    expect(seen).not.toHaveBeenCalled()
  })

  it('does nothing when stopped before the log arrives', async () => {
    const { session, conn } = setup({ [LOG]: [ok(jsonl(events.slice(0, LIVE_UNTIL)))] })
    conn.stop()
    await settle()
    expect(session.getState().state.lastSeq).toBe(0)
    expect(FakeSource.all).toHaveLength(0)
  })

  it('leaves out lines from another run in the log', async () => {
    const mixed = [...events.slice(0, LIVE_UNTIL), { ...at(31), run_id: 'someone-else' }]
    const { session } = start({ [LOG]: [ok(jsonl(mixed))] })
    await settle()
    expect(session.getState().state.lastSeq).toBe(LIVE_UNTIL)
  })
})
