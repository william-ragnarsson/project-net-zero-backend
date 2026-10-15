// Plain helpers over RunSummary for the sidebar, the runs list and the dashboard.
import type { RunSummary } from '../gen/events'
import { formatPct } from './format'

type State = RunSummary['state']

export const TERMINAL: ReadonlySet<State> = new Set(['completed', 'failed', 'cancelled', 'interrupted'])

export const isTerminal = (s: State | undefined): boolean => s !== undefined && TERMINAL.has(s)

export const isActive = (r: RunSummary): boolean => !TERMINAL.has(r.state)

const DEMO = { name: 'Demo repository', owner: null, full: 'Demo repository' } as const

/** A run's display name: the last URL segment, and `owner/name` for titles. */
export function repoOf(r: Pick<RunSummary, 'source'>): { name: string; owner: string | null; full: string } {
  if (r.source.kind === 'demo' || r.source.url === null) return { ...DEMO }
  const parts = r.source.url.replace(/\/+$/, '').split('/')
  const name = (parts.at(-1) ?? '').replace(/\.git$/, '')
  const owner = parts.at(-2) ?? null
  return { name, owner, full: owner === null ? name : `${owner}/${name}` }
}

export type ValueTone = 'ink-2' | 'ink-3' | 'accent' | 'fail'

/** The one quiet value a run shows on the right of its row. */
export function runValue(r: RunSummary): { text: string; tone: ValueTone; live: boolean } {
  switch (r.state) {
    case 'awaiting_selection':
      return { text: 'waiting', tone: 'ink-2', live: true }
    case 'completed':
      if (r.source.kind === 'demo') return { text: 'demo', tone: 'ink-3', live: false }
      if ((r.counts_by_outcome.accepted ?? 0) >= 1) {
        return { text: formatPct(r.mean_reduction_pct, 0), tone: 'accent', live: false }
      }
      return { text: 'no win', tone: 'ink-3', live: false }
    case 'failed':
      return { text: 'failed', tone: 'fail', live: false }
    case 'cancelled':
      return { text: 'cancelled', tone: 'ink-3', live: false }
    case 'interrupted':
      return { text: 'interrupted', tone: 'ink-3', live: false }
    default:
      return { text: 'running', tone: 'ink-2', live: true }
  }
}

export const TONE_CLASS: Record<ValueTone, string> = {
  'ink-2': 'text-ink-2',
  'ink-3': 'text-ink-3',
  accent: 'text-accent',
  fail: 'text-fail',
}

/** Active runs first, then the newest, at most `max`. */
export function recentRuns(runs: RunSummary[], max = 12): RunSummary[] {
  return [...runs]
    .sort((a, b) => Number(isActive(b)) - Number(isActive(a)) || b.created_ts - a.created_ts)
    .slice(0, max)
}

const STATE_LABEL: Record<State, string> = {
  created: 'Finding functions',
  cloning: 'Finding functions',
  installing: 'Finding functions',
  discovering: 'Finding functions',
  triaging: 'Finding functions',
  awaiting_selection: 'Waiting for you',
  optimizing: 'Optimizing',
  finalizing: 'Writing the patch',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
  interrupted: 'Interrupted',
}

export const stateLabel = (s: State): string => STATE_LABEL[s]

/** What an active run is doing, for the dashboard's "Running now" row. */
export function progressText(r: RunSummary): string {
  switch (r.state) {
    case 'awaiting_selection':
      return 'Waiting for you to choose functions'
    case 'optimizing':
      return `Optimizing · ${r.functions_done} of ${r.functions_total} done`
    case 'finalizing':
      return 'Writing the patch'
    default:
      return stateLabel(r.state)
  }
}

export interface Impact {
  /** grams of CO2e saved per 1M calls of each improved function, summed */
  grams: number
  functions: number
  repositories: number
  /** the counted run ids, newest first */
  runs: string[]
  excludedDemo: number
}

const normalizeUrl = (url: string) => url.toLowerCase().replace(/\/+$/, '').replace(/\.git$/, '')

/**
 * The dashboard total. Only completed live runs on a GitHub repository count, and only the newest
 * per repository, so re-running a repository, on any ref, never counts its savings twice.
 */
export function selectImpact(runs: RunSummary[]): Impact {
  const newest = new Map<string, RunSummary>()
  let excludedDemo = 0
  for (const r of runs) {
    if (r.state !== 'completed') continue
    if (r.source.kind === 'demo') {
      excludedDemo += 1
      continue
    }
    if (r.mode !== 'live' || r.source.url === null) continue
    const key = normalizeUrl(r.source.url)
    const seen = newest.get(key)
    if (seen === undefined || r.created_ts > seen.created_ts) newest.set(key, r)
  }
  const kept = [...newest.values()].sort((a, b) => b.created_ts - a.created_ts)
  return {
    grams: kept.reduce((sum, r) => sum + r.g_saved_per_1m_calls, 0),
    functions: kept.reduce((sum, r) => sum + (r.counts_by_outcome.accepted ?? 0), 0),
    repositories: kept.filter((r) => (r.counts_by_outcome.accepted ?? 0) >= 1).length,
    runs: kept.map((r) => r.id),
    excludedDemo,
  }
}

export type RunFilterMode = 'all' | 'live' | 'demo'

export function filterRuns(runs: RunSummary[], q: string, mode: RunFilterMode): RunSummary[] {
  const needle = q.trim().toLowerCase()
  return runs.filter((r) => {
    if (mode === 'live' && !(r.mode === 'live' && r.source.kind === 'github')) return false
    if (mode === 'demo' && r.source.kind !== 'demo') return false
    return needle === '' || repoOf(r).full.toLowerCase().includes(needle)
  })
}

const GITHUB = /^(https?:\/\/)?(www\.)?github\.com\/([\w.-]+)\/([\w.-]+?)(\.git)?\/?$/

/** `https://github.com/owner/repo` from what a person types or pastes, or null. */
export function parseRepoUrl(input: string): string | null {
  const m = GITHUB.exec(input.trim())
  if (m === null) return null
  return `https://github.com/${m[3]}/${m[4]}`
}
