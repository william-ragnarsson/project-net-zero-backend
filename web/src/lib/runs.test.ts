import { describe, expect, it } from 'vitest'
import type { RunSummary } from '../gen/events'
import {
  filterRuns,
  outcomeTotals,
  parseRepoUrl,
  progressText,
  recentRuns,
  repoOf,
  runStatus,
  runValue,
  selectImpact,
  stageIndex,
  stateLabel,
} from './runs'

const github = (url: string, ref: string | null = null): RunSummary['source'] => ({ kind: 'github', url, ref })
const DEMO: RunSummary['source'] = { kind: 'demo', url: null, ref: null }

let next = 0
function summary(over: Partial<RunSummary> = {}): RunSummary {
  next += 1
  return {
    id: `run-${next}`,
    created_ts: 1_000 + next,
    updated_ts: 1_000 + next,
    state: 'completed',
    source: github('https://github.com/example/textkit'),
    mode: 'live',
    functions_total: 8,
    functions_done: 8,
    counts_by_outcome: { accepted: 6 },
    mean_reduction_pct: -70.8,
    g_saved_per_1m_calls: 100,
    last_seq: 545,
    ...over,
  }
}

// GET /api/runs from the fake backend's seed data, 2026-10-12, newest first
const SEED: RunSummary[] = [
  summary({
    id: '20261012-220928-example-textkit',
    created_ts: 1791857368385,
    source: github('https://github.com/example/textkit'),
    counts_by_outcome: { accepted: 6, all_rejected: 1, no_significant_win: 1 },
    mean_reduction_pct: -70.81666666666666,
    g_saved_per_1m_calls: 121.69588052614841,
  }),
  summary({
    id: '20261012-220903-example-csvtools',
    created_ts: 1791857343284,
    state: 'cancelled',
    source: github('https://github.com/example/csvtools'),
    functions_done: 4,
    counts_by_outcome: { accepted: 3, cancelled: 1 },
    mean_reduction_pct: -91.16666666666667,
    g_saved_per_1m_calls: 119.47816179606637,
  }),
  summary({
    id: '20261012-220815-demo',
    created_ts: 1791857295849,
    source: DEMO,
    mode: 'demo',
    counts_by_outcome: { accepted: 6, all_rejected: 1, no_significant_win: 1 },
    mean_reduction_pct: -70.81666666666666,
    g_saved_per_1m_calls: 121.69588052614841,
  }),
  summary({
    id: '20261012-220728-acme-geo-utils',
    created_ts: 1791857248299,
    source: github('https://github.com/acme/geo-utils'),
    counts_by_outcome: { accepted: 6, all_rejected: 1, no_significant_win: 1 },
    mean_reduction_pct: -70.81666666666666,
    g_saved_per_1m_calls: 121.69588052614841,
  }),
  summary({
    id: '20261012-220640-example-textkit',
    created_ts: 1791857200565,
    source: github('https://github.com/example/textkit'),
    counts_by_outcome: { accepted: 6, all_rejected: 1, no_significant_win: 1 },
    mean_reduction_pct: -70.81666666666666,
    g_saved_per_1m_calls: 121.69588052614841,
  }),
]

describe('repoOf', () => {
  it('names a GitHub run by its last URL segment', () => {
    expect(repoOf(summary({ source: github('https://github.com/example/textkit') }))).toEqual({
      name: 'textkit',
      owner: 'example',
      full: 'example/textkit',
    })
    expect(repoOf(summary({ source: github('https://github.com/acme/geo-utils.git') })).full).toBe('acme/geo-utils')
    expect(repoOf(summary({ source: github('https://github.com/acme/geo-utils/') })).name).toBe('geo-utils')
  })

  it('calls a demo run the demo repository', () => {
    expect(repoOf(summary({ source: DEMO }))).toEqual({ name: 'Demo repository', owner: null, full: 'Demo repository' })
  })
})

describe('runValue', () => {
  const value = (over: Partial<RunSummary>) => runValue(summary(over))

  it('marks a run that waits for the user', () => {
    expect(value({ state: 'awaiting_selection' })).toEqual({ text: 'waiting', tone: 'ink-2', live: true })
  })

  it('marks every other active state as running', () => {
    for (const state of ['created', 'cloning', 'installing', 'discovering', 'triaging', 'optimizing', 'finalizing'] as const) {
      expect(value({ state })).toEqual({ text: 'running', tone: 'ink-2', live: true })
    }
  })

  it('shows a completed run’s mean reduction, or why there is none', () => {
    expect(value({ source: DEMO, mode: 'demo' })).toEqual({ text: 'demo', tone: 'ink-3', live: false })
    expect(value({ mean_reduction_pct: -70.8 })).toEqual({ text: '−71%', tone: 'accent', live: false })
    expect(value({ counts_by_outcome: { no_significant_win: 2 }, mean_reduction_pct: null })).toEqual({
      text: 'no win',
      tone: 'ink-3',
      live: false,
    })
    expect(value({ counts_by_outcome: { accepted: 0 } }).text).toBe('no win')
  })

  it('names the other endings', () => {
    expect(value({ state: 'failed' })).toEqual({ text: 'failed', tone: 'fail', live: false })
    expect(value({ state: 'cancelled' })).toEqual({ text: 'cancelled', tone: 'ink-3', live: false })
    expect(value({ state: 'interrupted' })).toEqual({ text: 'interrupted', tone: 'ink-3', live: false })
  })
})

describe('recentRuns', () => {
  it('puts the active run first, then the newest, capped', () => {
    const old = summary({ id: 'old', created_ts: 1 })
    const live = summary({ id: 'live', created_ts: 2, state: 'optimizing' })
    const newer = summary({ id: 'newer', created_ts: 3 })
    expect(recentRuns([old, newer, live]).map((r) => r.id)).toEqual(['live', 'newer', 'old'])

    const many = Array.from({ length: 20 }, (_, i) => summary({ id: `r${i}`, created_ts: i }))
    const recent = recentRuns(many)
    expect(recent).toHaveLength(12)
    expect(recent[0]?.id).toBe('r19')
    expect(recentRuns(many, 3).map((r) => r.id)).toEqual(['r19', 'r18', 'r17'])
  })
})

describe('labels', () => {
  it('maps states to words', () => {
    expect(stateLabel('cloning')).toBe('Finding functions')
    expect(stateLabel('awaiting_selection')).toBe('Waiting for you')
    expect(stateLabel('finalizing')).toBe('Writing the patch')
    expect(progressText(summary({ state: 'optimizing', functions_done: 3, functions_total: 6 }))).toBe(
      'Optimizing · 3 of 6 done',
    )
    expect(progressText(summary({ state: 'awaiting_selection' }))).toBe('Waiting for you to choose functions')
    expect(progressText(summary({ state: 'triaging' }))).toBe('Finding functions')
  })

  it('names every setup state the same, and each ending on its own', () => {
    for (const state of ['created', 'cloning', 'installing', 'discovering', 'triaging'] as const) {
      expect(stateLabel(state)).toBe('Finding functions')
    }
    expect(stateLabel('optimizing')).toBe('Optimizing')
    expect(stateLabel('completed')).toBe('Completed')
    expect(stateLabel('failed')).toBe('Failed')
    expect(stateLabel('cancelled')).toBe('Cancelled')
    expect(stateLabel('interrupted')).toBe('Interrupted')
  })

  it('describes what an active run is doing', () => {
    expect(progressText(summary({ state: 'created' }))).toBe('Finding functions')
    expect(progressText(summary({ state: 'optimizing', functions_done: 0, functions_total: 1 }))).toBe(
      'Optimizing · 0 of 1 done',
    )
    expect(progressText(summary({ state: 'finalizing' }))).toBe('Writing the patch')
  })
})

describe('selectImpact', () => {
  it('counts completed live GitHub runs only', () => {
    const impact = selectImpact([
      summary({ id: 'a', source: github('https://github.com/o/a'), g_saved_per_1m_calls: 10 }),
      summary({ id: 'demo', source: DEMO, mode: 'demo', g_saved_per_1m_calls: 50 }),
      summary({ id: 'record', source: github('https://github.com/o/r'), mode: 'record', g_saved_per_1m_calls: 50 }),
      summary({ id: 'cancelled', state: 'cancelled', source: github('https://github.com/o/c'), g_saved_per_1m_calls: 50 }),
      summary({ id: 'failed', state: 'failed', source: github('https://github.com/o/f'), g_saved_per_1m_calls: 50 }),
      summary({ id: 'running', state: 'optimizing', source: github('https://github.com/o/x'), g_saved_per_1m_calls: 50 }),
    ])
    expect(impact).toEqual({ grams: 10, functions: 6, repositories: 1, meanPct: -70.8, runs: ['a'], excludedDemo: 1 })
  })

  it('keeps the newest run per repository, whatever its ref', () => {
    const impact = selectImpact([
      summary({ id: 'old', created_ts: 1, source: github('https://github.com/Example/textkit'), g_saved_per_1m_calls: 10 }),
      summary({ id: 'branch', created_ts: 2, source: github('https://github.com/example/textkit', 'dev'), g_saved_per_1m_calls: 5 }),
      summary({ id: 'new', created_ts: 3, source: github('https://github.com/example/textkit.git/'), g_saved_per_1m_calls: 7 }),
      summary({ id: 'other', created_ts: 2, source: github('https://github.com/example/geo'), g_saved_per_1m_calls: 4 }),
    ])
    // main and dev mostly share code, so counting both would count the same savings twice
    expect(impact.runs).toEqual(['new', 'other'])
    expect(impact.grams).toBe(11)
  })

  it('does not count a kept run with no accepted function as an improved repository', () => {
    const impact = selectImpact([
      summary({ id: 'a', created_ts: 1, source: github('https://github.com/o/a'), g_saved_per_1m_calls: 10 }),
      summary({ id: 'b', created_ts: 2, source: github('https://github.com/o/b'), g_saved_per_1m_calls: 0, counts_by_outcome: {} }),
    ])
    expect(impact.runs).toEqual(['b', 'a'])
    expect(impact.functions).toBe(6)
    expect(impact.repositories).toBe(1)
  })

  it('matches the seed data', () => {
    const impact = selectImpact(SEED)
    expect(impact.repositories).toBe(2)
    expect(impact.functions).toBe(12)
    expect(impact.grams).toBeCloseTo(243.39, 2)
    expect(impact.runs).toEqual(['20261012-220928-example-textkit', '20261012-220728-acme-geo-utils'])
    expect(impact.excludedDemo).toBe(1)
  })
})

describe('selectImpact meanPct', () => {
  it('weights each run’s mean by its improved functions', () => {
    const impact = selectImpact([
      summary({ id: 'a', source: github('https://github.com/o/a'), counts_by_outcome: { accepted: 3 }, mean_reduction_pct: -60 }),
      summary({ id: 'b', source: github('https://github.com/o/b'), counts_by_outcome: { accepted: 1 }, mean_reduction_pct: -20 }),
      summary({ id: 'c', source: github('https://github.com/o/c'), counts_by_outcome: {}, mean_reduction_pct: null }),
    ])
    expect(impact.meanPct).toBe(-50)
  })

  it('is null with nothing improved', () => {
    expect(selectImpact([summary({ counts_by_outcome: {}, mean_reduction_pct: null })]).meanPct).toBeNull()
  })
})

describe('runStatus and stageIndex', () => {
  it('names what a run is doing or how it ended', () => {
    expect(runStatus(summary({ state: 'awaiting_selection' }))).toEqual({ label: 'Waiting for you', tone: 'caution' })
    expect(runStatus(summary({ state: 'optimizing' }))).toEqual({ label: 'Optimizing', tone: 'live' })
    expect(runStatus(summary())).toEqual({ label: 'Completed', tone: 'success' })
    expect(runStatus(summary({ counts_by_outcome: {} }))).toEqual({ label: 'Completed', tone: 'neutral' })
    expect(runStatus(summary({ state: 'failed' }))).toEqual({ label: 'Failed', tone: 'fail' })
  })

  it('places an active run in one of four stages', () => {
    expect(['cloning', 'triaging', 'awaiting_selection', 'optimizing', 'finalizing'].map((s) => stageIndex(s as RunSummary['state']))).toEqual([
      0, 0, 1, 2, 3,
    ])
  })
})

describe('outcomeTotals', () => {
  it('groups outcomes across runs', () => {
    const totals = outcomeTotals([
      summary({ counts_by_outcome: { accepted: 2, no_significant_win: 1, reverted: 1 } }),
      summary({ counts_by_outcome: { all_rejected: 1, skipped_capture: 2, failed: 1, cancelled: 1 } }),
    ])
    expect(totals).toEqual({ improved: 2, no_win: 1, rejected: 2, skipped: 2, stopped: 2 })
  })
})

describe('filterRuns', () => {
  it('filters by mode and by owner or name', () => {
    const ids = (q: string, mode: 'all' | 'live' | 'demo') => filterRuns(SEED, q, mode).map((r) => r.id)
    expect(ids('', 'all')).toHaveLength(5)
    expect(ids('', 'demo')).toEqual(['20261012-220815-demo'])
    expect(ids('', 'live')).not.toContain('20261012-220815-demo')
    expect(ids('ACME', 'all')).toEqual(['20261012-220728-acme-geo-utils'])
    expect(ids(' textkit ', 'live')).toEqual(['20261012-220928-example-textkit', '20261012-220640-example-textkit'])
    expect(ids('demo', 'all')).toEqual(['20261012-220815-demo'])
    expect(ids('textkit', 'demo')).toEqual([])
  })

  it('leaves recorded runs out of Live', () => {
    const recorded = summary({ id: 'rec', mode: 'record' })
    expect(filterRuns([recorded], '', 'live')).toEqual([])
    expect(filterRuns([recorded], '', 'all')).toEqual([recorded])
  })
})

describe('parseRepoUrl', () => {
  it('normalizes what people paste', () => {
    expect(parseRepoUrl('https://github.com/example/textkit')).toBe('https://github.com/example/textkit')
    expect(parseRepoUrl('  github.com/example/textkit ')).toBe('https://github.com/example/textkit')
    expect(parseRepoUrl('http://www.github.com/example/textkit.git')).toBe('https://github.com/example/textkit')
    expect(parseRepoUrl('https://github.com/acme/geo-utils/')).toBe('https://github.com/acme/geo-utils')
  })

  it('rejects other hosts and deeper paths', () => {
    expect(parseRepoUrl('https://gitlab.com/example/textkit')).toBeNull()
    expect(parseRepoUrl('https://github.com/example/textkit/tree/main')).toBeNull()
    expect(parseRepoUrl('https://github.com/example')).toBeNull()
    expect(parseRepoUrl('')).toBeNull()
  })
})
