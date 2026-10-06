import { afterEach, describe, expect, it, vi } from 'vitest'
import { formatJsonl } from '../lib/jsonl'
import { makeLog } from '../test/events'
import { fetchReplay, isEventLike, parseReplay } from './replayFile'

describe('parseReplay', () => {
  it('keeps event-shaped lines, including types this build does not know', () => {
    const known = makeLog(1, 'hello')
    const unknown = { ...makeLog(2, ''), type: 'run.teleported' }
    expect(parseReplay(formatJsonl([known, unknown])).events).toEqual([known, unknown])
  })

  it('counts lines it cannot use: bad JSON, non-events and a cut-off last line', () => {
    const text = [
      JSON.stringify(makeLog(1, 'a')),
      '{not json',
      JSON.stringify({ seq: 2, type: 'log' }),
      JSON.stringify([1, 2]),
      '{"seq":3,"ts":',
    ].join('\n')
    const replay = parseReplay(text)
    expect(replay.events.map((e) => e.seq)).toEqual([1])
    expect(replay.skipped).toBe(4)
  })

  it('checks the envelope fields the reducer relies on', () => {
    const event = makeLog(1, 'a')
    expect(isEventLike(event)).toBe(true)
    expect(isEventLike({ ...event, seq: 1.5 })).toBe(false)
    expect(isEventLike({ ...event, ts: '0' })).toBe(false)
    expect(isEventLike({ ...event, data: null })).toBe(false)
    expect(isEventLike(null)).toBe(false)
  })
})

describe('fetchReplay', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  const serve = (body: string, init: ResponseInit) => vi.stubGlobal('fetch', async () => new Response(body, init))

  it('parses a replay served as a file', async () => {
    const event = makeLog(1, 'a')
    serve(formatJsonl([event]), { headers: { 'content-type': 'application/x-ndjson' } })
    expect(await fetchReplay('/replays/x.events.jsonl')).toEqual({ events: [event], skipped: 0 })
  })

  it('fails on a missing file, including one answered with the app page', async () => {
    serve('', { status: 404, statusText: 'Not Found' })
    await expect(fetchReplay('/replays/x.events.jsonl')).rejects.toThrow('/replays/x.events.jsonl: 404 Not Found')
    serve('<!doctype html>', { headers: { 'content-type': 'text/html; charset=utf-8' } })
    await expect(fetchReplay('/replays/x.events.jsonl')).rejects.toThrow('/replays/x.events.jsonl: not found')
  })
})
