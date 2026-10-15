import { describe, expect, it } from 'vitest'
import { initialRunModel, LOG_CAP, reduce } from '../state/reducer'
import { makeEvent, makeLog } from '../test/events'
import { selectLogLines } from './log'

const created = makeEvent(1, 'run.created', {
  schema_version: 1,
  netzero_version: '0.2.0',
  mode: 'demo',
  source: { kind: 'demo', url: null, ref: null },
  models: { triage: 'm', tests: 'm', tests_repair: 'm', rewrite: 'm', rewrite_repair: 'm' },
  settings: {
    max_functions: 20,
    preselect: 8,
    n_trials: 16,
    trial_target_s: 0.75,
    alpha: 0.05,
    min_effect_pct: 5,
    candidates: ['A', 'B', 'C'],
    country_iso: null,
  },
  assumptions: {
    eur_per_kwh: 0.2,
    eu_ets_eur_per_t: 70,
    vcm_eur_per_t: 15,
    default_calls_per_year: 1_000_000,
    notes: [],
  },
})

describe('selectLogLines', () => {
  it('merges every scope back into seq order, one line each', () => {
    const m = reduce(initialRunModel(), [
      created,
      makeLog(2, 'run one'),
      makeLog(3, 'fn one', { function_id: 'f' }),
      makeEvent(4, 'log', { level: 'error', source: 'bench', stream: 'stderr', lines: ['cand a', 'cand b'], truncated: false }, {
        function_id: 'f',
        candidate_id: 'A',
      }),
      makeLog(5, 'run two'),
    ])
    const { lines, truncated } = selectLogLines(m, 100)
    expect(lines.map((l) => l.text)).toEqual(['run one', 'fn one', 'cand a', 'cand b', 'run two'])
    expect(lines[2]).toEqual({ seq: 4, source: 'bench', level: 'error', stream: 'stderr', text: 'cand a' })
    expect(truncated).toBe(false)
  })

  it('keeps the newest lines and says it cut some', () => {
    const m = reduce(initialRunModel(), [created, ...[2, 3, 4, 5].map((s) => makeLog(s, `line ${s}`))])
    const { lines, truncated } = selectLogLines(m, 2)
    expect(lines.map((l) => l.text)).toEqual(['line 4', 'line 5'])
    expect(truncated).toBe(true)
  })

  it('says so when a buffer dropped lines', () => {
    const logs = Array.from({ length: LOG_CAP + 3 }, (_, i) => makeLog(i + 2, `line ${i}`))
    const m = reduce(initialRunModel(), [created, ...logs])
    const { lines, truncated } = selectLogLines(m, 10_000)
    expect(lines).toHaveLength(LOG_CAP)
    expect(lines[0]?.text).toBe('line 3')
    expect(truncated).toBe(true)
  })

  it('is empty before any log', () => {
    expect(selectLogLines(initialRunModel(), 10)).toEqual({ lines: [], truncated: false })
  })
})
