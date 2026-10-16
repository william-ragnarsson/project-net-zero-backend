import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, cancelRun, createRun, eventStreamUrl, eventsUrl, getRun, isApiError, listRuns, selectFunctions } from './client'

const json = (status: number, body: unknown, statusText = '') =>
  new Response(JSON.stringify(body), { status, statusText, headers: { 'content-type': 'application/json' } })

function stubFetch(answer: (url: string, init: RequestInit) => Response | Promise<Response>) {
  const fetch = vi.fn((url: string, init: RequestInit) => Promise.resolve(answer(url, init)))
  vi.stubGlobal('fetch', fetch)
  return fetch
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('api client', () => {
  it('turns an error body into an ApiError with its code and detail', async () => {
    stubFetch(() => json(404, { detail: 'no run nope', code: 'not_found', active_run: null }, 'Not Found'))
    const error = await getRun('nope').catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 404, code: 'not_found', detail: 'no run nope', activeRun: null })
    expect(isApiError(error, 'not_found')).toBe(true)
    expect(isApiError(error, 'run_active')).toBe(false)
  })

  it('keeps the active run of a 409', async () => {
    const active = { id: 'r1' }
    stubFetch(() => json(409, { detail: 'a run is active', code: 'run_active', active_run: active }))
    const error = await createRun({ demo: true }).catch((e: unknown) => e)
    expect(error).toMatchObject({ status: 409, code: 'run_active', activeRun: active })
  })

  it('falls back to http when the error is not JSON', async () => {
    stubFetch(() => new Response('<html>Bad gateway</html>', { status: 502, statusText: 'Bad Gateway' }))
    await expect(listRuns()).rejects.toMatchObject({ status: 502, code: 'http', detail: 'Bad Gateway' })
    stubFetch(() => new Response('', { status: 500 }))
    await expect(listRuns()).rejects.toMatchObject({ status: 500, code: 'http', detail: 'Request failed' })
  })

  it('reports a network failure with status 0', async () => {
    stubFetch(() => Promise.reject(new TypeError('Failed to fetch')))
    const error = await listRuns().catch((e: unknown) => e)
    expect(isApiError(error, 'network')).toBe(true)
    expect(error).toMatchObject({ status: 0, detail: 'Can’t reach the net-zero server' })
  })

  it('passes an abort through as it is', async () => {
    const controller = new AbortController()
    controller.abort()
    stubFetch(() => Promise.reject(new DOMException('aborted', 'AbortError')))
    const error = await listRuns(controller.signal).catch((e: unknown) => e)
    expect(isApiError(error)).toBe(false)
    expect(error).toBeInstanceOf(DOMException)
  })

  it('returns undefined for a 204', async () => {
    stubFetch(() => new Response(null, { status: 204 }))
    await expect(cancelRun('r1')).resolves.toBeUndefined()
  })

  it('sends a POST body as JSON and encodes path segments', async () => {
    const fetch = stubFetch(() => json(200, { id: 'a b' }))
    await expect(selectFunctions('a b', ['f1', 'f2'])).resolves.toEqual({ id: 'a b' })
    const [url, init] = fetch.mock.calls[0] ?? []
    expect(url).toBe('/api/runs/a%20b/select')
    expect(init?.method).toBe('POST')
    expect(init?.headers).toEqual({ 'content-type': 'application/json' })
    expect(JSON.parse(String(init?.body))).toEqual({ function_ids: ['f1', 'f2'] })
  })

  it('sends a GET without a body', async () => {
    const fetch = stubFetch(() => json(200, []))
    await expect(listRuns()).resolves.toEqual([])
    const [url, init] = fetch.mock.calls[0] ?? []
    expect(url).toBe('/api/runs')
    expect(init).toEqual({ method: 'GET' })
  })

  it('builds the event URLs', () => {
    expect(eventsUrl('a/b')).toBe('/api/runs/a%2Fb/events.jsonl')
    expect(eventStreamUrl('r1', 42)).toBe('/api/runs/r1/events?after=42')
  })
})
