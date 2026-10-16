// react-query hooks over api/client. Only ['runs'] (while a run is active) and ['health'] poll.
import { useMutation, useQueries, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import type { CreateRunRequest, RunDetail, RunEvent, RunSummary } from '../gen/events'
import { isActive, isTerminal } from '../lib/runs'
import { SHOWCASE, showcaseKind, type ShowcaseKind } from '../showcase/config'
import { parseReplay } from '../sources/replayFile'
import {
  cancelRun,
  createRun,
  fetchEventsJsonl,
  getCapabilities,
  getHealth,
  getRun,
  getShowcaseMeta,
  isApiError,
  listRuns,
  selectFunctions,
} from './client'

export const HEALTH_KEY = ['health'] as const
export const RUNS_KEY = ['runs'] as const

/** A failed request shows the offline line at once instead of after the next health poll. */
function checkHealth(qc: QueryClient, e: unknown) {
  if (isApiError(e, 'network')) void qc.refetchQueries({ queryKey: HEALTH_KEY })
}

export const useHealth = () =>
  useQuery({
    queryKey: HEALTH_KEY,
    queryFn: ({ signal }) => getHealth(signal),
    // a static file never goes offline
    refetchInterval: SHOWCASE ? false : 10_000,
    retry: false,
    refetchOnWindowFocus: true,
  })

export const useCapabilities = () =>
  useQuery({ queryKey: ['capabilities'], queryFn: ({ signal }) => getCapabilities(signal), staleTime: 60_000 })

export function useRuns() {
  const qc = useQueryClient()
  return useQuery({
    queryKey: RUNS_KEY,
    queryFn: async ({ signal }) => {
      try {
        return await listRuns(signal)
      } catch (e) {
        checkHealth(qc, e)
        throw e
      }
    },
    staleTime: 2_000,
    refetchOnWindowFocus: true,
    refetchInterval: (q) => (q.state.data?.some(isActive) === true ? 2_500 : false),
  })
}

/** Run details for the dashboard. A finished run never changes, so it is fetched once. */
export const useRunDetails = (ids: string[]) =>
  useQueries({
    queries: ids.map((id) => ({
      queryKey: ['run', id],
      queryFn: ({ signal }: { signal: AbortSignal }) => getRun(id, signal),
      staleTime: (q: { state: { data: RunDetail | undefined } }) => (isTerminal(q.state.data?.state) ? Infinity : 0),
    })),
  })

/** A finished run's whole log, for replaying it. */
export const useRunEvents = (id: string, enabled: boolean) =>
  useQuery({
    queryKey: ['run-events', id],
    queryFn: async ({ signal }): Promise<RunEvent[]> => parseReplay(await fetchEventsJsonl(id, signal)).events,
    staleTime: Infinity,
    enabled,
  })

function settle(qc: QueryClient, id: string | null) {
  void qc.invalidateQueries({ queryKey: RUNS_KEY })
  if (id !== null) void qc.invalidateQueries({ queryKey: ['run', id] })
}

export function useCreateRun() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: CreateRunRequest) => createRun(body),
    onSuccess: (summary) => {
      qc.setQueryData<RunSummary[]>(RUNS_KEY, (old) => [summary, ...(old ?? []).filter((r) => r.id !== summary.id)])
    },
    onError: (e) => checkHealth(qc, e),
    onSettled: (summary) => settle(qc, summary?.id ?? null),
  })
}

export function useSelect(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (ids: string[]) => selectFunctions(id, ids),
    onError: (e) => checkHealth(qc, e),
    onSettled: () => settle(qc, id),
  })
}

export function useCancel(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => cancelRun(id),
    onError: (e) => checkHealth(qc, e),
    onSettled: () => settle(qc, id),
  })
}

/**
 * Turns an artifact URL from the log into a download link. The server answers the URL as is;
 * the showcase has a static copy of some files and none of the zip, so a link without one
 * resolves to undefined and is not drawn.
 */
export function useArtifactHref(): (url: string | undefined) => string | undefined {
  const meta = useShowcaseMeta().data
  if (!SHOWCASE) return (url) => url
  const map = meta?.artifacts ?? {}
  return (url) => (url === undefined ? undefined : map[url])
}

function useShowcaseMeta() {
  return useQuery({
    queryKey: ['showcase'],
    queryFn: ({ signal }) => getShowcaseMeta(signal),
    enabled: SHOWCASE,
    staleTime: Infinity,
  })
}

/** Whether the showcase's runs are samples, recordings or both; undefined outside it and while loading. */
export function useShowcaseKind(): ShowcaseKind | undefined {
  const meta = useShowcaseMeta().data
  const runs = useRuns().data
  if (meta === undefined || runs === undefined) return undefined
  return showcaseKind(meta.samples.length, runs.length)
}
