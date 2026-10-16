// The REST half of the backend (README "HTTP API"). The event stream itself is read by the store.
import type { ApiError as ApiErrorBody, Capabilities, CreateRunRequest, Health, RunDetail, RunSummary } from '../gen/events'

export type ApiErrorCode = ApiErrorBody['code'] | 'http' | 'network'

export class ApiError extends Error {
  /** 0 for a network failure. */
  readonly status: number
  readonly code: ApiErrorCode
  readonly detail: string
  /** The run in the way, on a 409 `run_active`. */
  readonly activeRun: RunSummary | null

  constructor(status: number, code: ApiErrorCode, detail: string, activeRun: RunSummary | null = null) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.detail = detail
    this.activeRun = activeRun
  }
}

export const isApiError = (e: unknown, code?: ApiErrorCode): e is ApiError =>
  e instanceof ApiError && (code === undefined || e.code === code)

const isErrorBody = (v: unknown): v is ApiErrorBody =>
  typeof v === 'object' &&
  v !== null &&
  typeof (v as { detail?: unknown }).detail === 'string' &&
  typeof (v as { code?: unknown }).code === 'string'

interface RequestInit {
  method?: 'GET' | 'POST'
  body?: unknown
  signal?: AbortSignal | undefined
}

async function send(path: string, init: RequestInit = {}): Promise<Response> {
  const options: globalThis.RequestInit = { method: init.method ?? 'GET' }
  if (init.signal !== undefined) options.signal = init.signal
  if (init.body !== undefined) {
    options.headers = { 'content-type': 'application/json' }
    options.body = JSON.stringify(init.body)
  }
  let res: Response
  try {
    res = await fetch(path, options)
  } catch (e) {
    // an abort is the caller's own doing, not an outage
    if (init.signal?.aborted === true) throw e
    throw new ApiError(0, 'network', 'Can’t reach the net-zero server')
  }
  if (!res.ok) {
    let body: unknown = null
    try {
      body = await res.json()
    } catch {
      // not JSON: a proxy page or an empty body
    }
    if (isErrorBody(body)) throw new ApiError(res.status, body.code, body.detail, body.active_run ?? null)
    throw new ApiError(res.status, 'http', res.statusText || 'Request failed')
  }
  return res
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await send(path, init)
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

const enc = encodeURIComponent

export const getHealth = (signal?: AbortSignal) => request<Health>('/api/health', { signal })

export const getCapabilities = (signal?: AbortSignal) => request<Capabilities>('/api/capabilities', { signal })

/** Newest first. */
export const listRuns = (signal?: AbortSignal) => request<RunSummary[]>('/api/runs', { signal })

export const getRun = (id: string, signal?: AbortSignal) => request<RunDetail>(`/api/runs/${enc(id)}`, { signal })

export const createRun = (body: CreateRunRequest) => request<RunSummary>('/api/runs', { method: 'POST', body })

export const selectFunctions = (id: string, ids: string[]) =>
  request<RunSummary>(`/api/runs/${enc(id)}/select`, { method: 'POST', body: { function_ids: ids } })

/** Blocks for up to about 5s while the run winds down; a finished run answers 200 as well. */
export const cancelRun = (id: string) => request<RunSummary>(`/api/runs/${enc(id)}/cancel`, { method: 'POST' })

export const eventsUrl = (id: string) => `/api/runs/${enc(id)}/events.jsonl`

export const eventStreamUrl = (id: string, after: number) => `/api/runs/${enc(id)}/events?after=${after}`

export async function fetchEventsJsonl(id: string, signal?: AbortSignal): Promise<string> {
  const res = await send(eventsUrl(id), { signal })
  return res.text()
}
