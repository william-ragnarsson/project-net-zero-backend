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

export type StatusTone = 'success' | 'neutral' | 'fail' | 'live' | 'caution'

/** A run's state as a badge: what it is doing, or how it ended. */
export function runStatus(r: RunSummary): { label: string; tone: StatusTone } {
  switch (r.state) {
    case 'awaiting_selection':
      return { label: 'Waiting for you', tone: 'caution' }
    case 'completed':
      return { label: 'Completed', tone: (r.counts_by_outcome.accepted ?? 0) >= 1 ? 'success' : 'neutral' }
    case 'failed':
      return { label: 'Failed', tone: 'fail' }
    case 'cancelled':
    case 'interrupted':
      return { label: stateLabel(r.state), tone: 'neutral' }
    default:
      return { label: stateLabel(r.state), tone: 'live' }
  }
}

/** The four stages a run moves through, for the dashboard's stepper. */
export const STAGES = ['Find functions', 'Choose', 'Optimize', 'Patch'] as const

/** Which of `STAGES` an active run is in. */
export function stageIndex(s: State): number {
  switch (s) {
    case 'awaiting_selection':
      return 1
    case 'optimizing':
      return 2
    case 'finalizing':
      return 3
    default:
      return 0
  }
}

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
  /** the mean reduction across every improved function, or null with none */
  meanPct: number | null
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
  // each run's mean is over its own improved functions, so weight it by how many there were
  let weighted = 0
  let improved = 0
  for (const r of kept) {
    const n = r.counts_by_outcome.accepted ?? 0
    if (n === 0 || r.mean_reduction_pct === null) continue
    weighted += r.mean_reduction_pct * n
    improved += n
  }
  return {
    meanPct: improved === 0 ? null : weighted / improved,
    grams: kept.reduce((sum, r) => sum + r.g_saved_per_1m_calls, 0),
    functions: kept.reduce((sum, r) => sum + (r.counts_by_outcome.accepted ?? 0), 0),
    repositories: kept.filter((r) => (r.counts_by_outcome.accepted ?? 0) >= 1).length,
    runs: kept.map((r) => r.id),
    excludedDemo,
  }
}

export type OutcomeGroup = 'improved' | 'no_win' | 'rejected' | 'skipped' | 'stopped'

const GROUP_OF: Record<keyof RunSummary['counts_by_outcome'], OutcomeGroup> = {
  accepted: 'improved',
  no_significant_win: 'no_win',
  all_rejected: 'rejected',
  reverted: 'rejected',
  skipped_untestable: 'skipped',
  skipped_capture: 'skipped',
  failed: 'stopped',
  cancelled: 'stopped',
}

/** What happened to the functions of these runs, in five groups. */
export function outcomeTotals(runs: RunSummary[]): Record<OutcomeGroup, number> {
  const totals: Record<OutcomeGroup, number> = { improved: 0, no_win: 0, rejected: 0, skipped: 0, stopped: 0 }
  for (const r of runs) {
    for (const [k, n] of Object.entries(r.counts_by_outcome) as [keyof RunSummary['counts_by_outcome'], number][]) {
      totals[GROUP_OF[k]] += n
    }
  }
  return totals
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
