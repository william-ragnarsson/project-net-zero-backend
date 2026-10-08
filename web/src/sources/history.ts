// /api/history: what Postgres keeps of every run. The server copies new events from runs/ into
// the database every couple of seconds, so the hooks poll while the page is visible.
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import type { ActivityPage, FunctionDiff, HistoryStatus, ProjectDetail, ProjectSummary } from '../gen/events'
import { readableQuery } from './replay'

const BASE = '/api/history'
const POLL_MS = 5_000

/** A non-2xx answer; `code` is the ApiError code when the body has one (`no_database`, ...). */
export class ApiFailure extends Error {
  readonly status: number
  readonly code: string | null
  constructor(message: string, status: number, code: string | null) {
    super(message)
    this.status = status
    this.code = code
  }
}

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null

async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(url, signal === undefined ? {} : { signal })
  // with no backend behind the dev proxy, or a static host, /api answers with index.html
  if (res.headers.get('content-type')?.startsWith('text/html')) {
    throw new ApiFailure('the net-zero server is not answering on /api', res.status, null)
  }
  const body: unknown = await res.json().catch(() => null)
  if (!res.ok) {
    const detail = isRecord(body) && typeof body.detail === 'string' ? body.detail : `${res.status} ${res.statusText}`
    const code = isRecord(body) && typeof body.code === 'string' ? body.code : null
    throw new ApiFailure(detail, res.status, code)
  }
  return body as T
}

/** The stored log of one run, for /replay?src=. */
export const storedRunSrc = (runId: string): string => `${BASE}/runs/${encodeURIComponent(runId)}/events.jsonl`

/** The replay of a stored run: played from the start, or opened paused right after `seq`. */
export function storedReplayHref(runId: string, seq?: number): string {
  const params = new URLSearchParams({ src: storedRunSrc(runId) })
  if (seq !== undefined) params.set('at', String(seq))
  return `/replay${readableQuery(params)}`
}

export const useHistoryStatus = () =>
  useQuery({
    queryKey: ['history', 'status'],
    queryFn: ({ signal }) => getJson<HistoryStatus>(`${BASE}/status`, signal),
    refetchInterval: POLL_MS,
  })

export const useProjects = (enabled: boolean) =>
  useQuery({
    queryKey: ['history', 'projects'],
    queryFn: ({ signal }) => getJson<ProjectSummary[]>(`${BASE}/projects`, signal),
    refetchInterval: POLL_MS,
    enabled,
  })

export const useProject = (projectId: string) =>
  useQuery({
    queryKey: ['history', 'project', projectId],
    queryFn: ({ signal }) => getJson<ProjectDetail>(`${BASE}/projects/${encodeURIComponent(projectId)}`, signal),
    refetchInterval: POLL_MS,
    retry: (count, error) => !(error instanceof ApiFailure && error.status === 404) && count < 1,
  })

export const useActivity = (enabled: boolean, project?: string) =>
  useInfiniteQuery({
    queryKey: ['history', 'activity', project ?? null],
    queryFn: ({ pageParam, signal }) => {
      const params = new URLSearchParams({ limit: '25' })
      if (project !== undefined) params.set('project', project)
      if (pageParam !== null) params.set('before', pageParam)
      return getJson<ActivityPage>(`${BASE}/activity?${params}`, signal)
    },
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next,
    refetchInterval: POLL_MS,
    enabled,
  })

/** A diff never changes once stored. */
export const useFunctionDiff = (runId: string, functionId: string, enabled: boolean) =>
  useQuery({
    queryKey: ['history', 'diff', runId, functionId],
    queryFn: ({ signal }) =>
      getJson<FunctionDiff>(
        `${BASE}/runs/${encodeURIComponent(runId)}/diff?${new URLSearchParams({ function_id: functionId })}`,
        signal,
      ),
    staleTime: Infinity,
    enabled,
  })
