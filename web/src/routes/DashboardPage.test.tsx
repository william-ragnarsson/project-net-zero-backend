// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { FunctionRecord, RunSummary } from '../gen/events'
import { formatGrams, NONE } from '../lib/format'
import { selectImpact } from '../lib/runs'
import { DashboardPage } from './DashboardPage'

const github = (url: string): RunSummary['source'] => ({ kind: 'github', url, ref: null })
const DEMO: RunSummary['source'] = { kind: 'demo', url: null, ref: null }

function run(over: Partial<RunSummary>): RunSummary {
  return {
    id: 'r',
    created_ts: 1_791_857_368_385,
    updated_ts: 1_791_857_368_385,
    state: 'completed',
    source: github('https://github.com/example/textkit'),
    mode: 'live',
    functions_total: 8,
    functions_done: 8,
    counts_by_outcome: { accepted: 6, no_significant_win: 2 },
    mean_reduction_pct: -70.8,
    g_saved_per_1m_calls: 121.7,
    last_seq: 545,
    ...over,
  }
}

// newest first, as GET /api/runs returns them
const RUNS: RunSummary[] = [
  run({
    id: 'other-live',
    created_ts: 1_791_857_400_000,
    state: 'optimizing',
    source: github('https://github.com/acme/parsers'),
    functions_done: 2,
    counts_by_outcome: { accepted: 1 },
    g_saved_per_1m_calls: 50,
  }),
  run({ id: 'textkit-new', created_ts: 1_791_857_368_385, g_saved_per_1m_calls: 121.7 }),
  run({
    id: 'csvtools',
    created_ts: 1_791_857_343_284,
    state: 'cancelled',
    source: github('https://github.com/example/csvtools'),
    counts_by_outcome: { accepted: 3, cancelled: 1 },
    g_saved_per_1m_calls: 119.5,
  }),
  run({ id: 'demo', created_ts: 1_791_857_295_849, source: DEMO, mode: 'demo' }),
  run({
    id: 'geo',
    created_ts: 1_791_857_248_299,
    source: github('https://github.com/acme/geo-utils'),
    counts_by_outcome: { accepted: 4 },
    g_saved_per_1m_calls: 33.3,
  }),
  run({ id: 'textkit-old', created_ts: 1_791_857_200_565, g_saved_per_1m_calls: 500 }),
]

function fn(over: Partial<FunctionRecord>): FunctionRecord {
  return {
    function_id: 'mod:f',
    qualname: 'f',
    outcome: 'accepted',
    winner: 'A',
    delta_pct: -50,
    delta_ci_pct: null,
    g_saved_per_1m_calls: 1,
    reason: '',
    ...over,
  }
}

// The details' function savings deliberately do not add up to the summaries': the total must not use them.
const DETAILS: Record<string, FunctionRecord[]> = {
  'textkit-new': [
    fn({ function_id: 'algos.pairs:has_pair_with_sum', qualname: 'has_pair_with_sum', delta_pct: -97.1, g_saved_per_1m_calls: 78.5 }),
    fn({ function_id: 'textkit.freq:word_frequencies', qualname: 'word_frequencies', delta_pct: -46.9, g_saved_per_1m_calls: null }),
    fn({ function_id: 'textkit.fields:join_fields', qualname: 'join_fields', outcome: 'no_significant_win', delta_pct: null, g_saved_per_1m_calls: null }),
  ],
  geo: [fn({ function_id: 'geo.dist:haversine', qualname: 'haversine', delta_pct: -61.8, g_saved_per_1m_calls: 9.9 })],
}

function stubServer() {
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string) => {
      const id = /^\/api\/runs\/([^/]+)$/.exec(url)?.[1]
      const summary = RUNS.find((r) => r.id === id)
      if (summary !== undefined) return Promise.resolve(Response.json({ ...summary, functions: DETAILS[summary.id] ?? [] }))
      if (url === '/api/health') return Promise.resolve(Response.json({ ok: true, version: '0.2.0' }))
      return Promise.resolve(new Response(null, { status: 404 }))
    }),
  )
}

function renderDashboard(runs: RunSummary[]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  client.setQueryData(['runs'], runs)
  client.setQueryData(['health'], { ok: true, version: '0.2.0' })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const region = (name: string) => screen.getByRole('region', { name })
const valueOf = (name: string) => region(name).querySelector('p.text-xl')?.textContent

describe('DashboardPage', () => {
  it('shows the selectImpact total and no other', async () => {
    stubServer()
    renderDashboard(RUNS)
    const impact = selectImpact(RUNS)
    // newest textkit + geo-utils; the running, cancelled, demo and older textkit runs do not count
    expect(impact).toMatchObject({ functions: 10, repositories: 2, excludedDemo: 1 })

    expect(valueOf('CO2e saved')).toBe(formatGrams(impact.grams))
    expect(formatGrams(impact.grams)).toBe('155.0 g')
    expect(within(region('CO2e saved')).getByText('per 1M calls of each improved function')).toBeTruthy()
    expect(within(region('CO2e saved')).getByText('Demo runs are not counted')).toBeTruthy()
    expect(valueOf('Functions improved')).toBe('10')
    expect(within(region('Functions improved')).getByText('in 2 repositories')).toBeTruthy()
    expect(valueOf('Average reduction')).toBe('−70.8%')
    expect(valueOf('Runs')).toBe('6')
    expect(within(region('Runs')).getByText('1 running, 4 completed')).toBeTruthy()

    // the details have loaded and the total still comes from the summaries
    await screen.findByText('Top improvements')
    expect(valueOf('CO2e saved')).toBe('155.0 g')
  })

  it('projects a year from the calls the user types', () => {
    stubServer()
    renderDashboard(RUNS)
    const year = region('Yearly estimate')
    expect(within(year).getByText('155.0 g')).toBeTruthy()
    fireEvent.change(within(year).getByLabelText('Calls per year for each function'), { target: { value: '2,000,000' } })
    expect(within(year).getByText('310.0 g')).toBeTruthy()
  })

  it('uses the singular for one function in one repository', () => {
    stubServer()
    renderDashboard([run({ id: 'one', counts_by_outcome: { accepted: 1 }, g_saved_per_1m_calls: 2 })])
    expect(valueOf('Functions improved')).toBe('1')
    expect(within(region('Functions improved')).getByText('in 1 repository')).toBeTruthy()
    expect(screen.queryByText('Demo runs are not counted')).toBeNull()
  })

  it('links the running run and lists the best recent improvements', async () => {
    stubServer()
    renderDashboard(RUNS)

    const section = screen.getByRole('heading', { name: 'Running now' }).closest('section')!
    const running = within(section).getByRole('link')
    expect(running.getAttribute('href')).toBe('/runs/other-live')
    expect(running.textContent).toContain('acme/parsers')
    expect(running.textContent).toContain('Optimizing · 2 of 8 done')
    expect(within(section).getByRole('listitem', { current: 'step' }).textContent).toBe('Optimize')

    await screen.findByText('Top improvements')
    const table = screen.getByText('has_pair_with_sum').closest('table')!
    const rows = [...table.querySelectorAll('tbody tr')] as HTMLElement[]
    expect(rows.map((r) => within(r).getByRole('link').textContent)).toEqual([
      'has_pair_with_sum',
      'haversine',
      'word_frequencies',
    ])
    expect(within(rows[0]!).getByRole('link').getAttribute('href')).toBe(
      `/runs/textkit-new?tab=changes&fn=${encodeURIComponent('algos.pairs:has_pair_with_sum')}`,
    )
    expect(rows[0]!.textContent).toContain('−97.1%')
    expect(rows[0]!.textContent).toContain('78.5 g per 1M')
    expect(rows[2]!.textContent).toContain('·')
    expect(screen.queryByText('join_fields')).toBeNull()
  })

  it('shows the newest five runs and a link to all of them', () => {
    stubServer()
    renderDashboard(RUNS)
    const recent = screen.getByRole('heading', { name: 'Recent runs' }).closest('section')!
    expect(within(recent).getAllByRole('listitem')).toHaveLength(5)
    expect(within(recent).getByRole('link', { name: 'All runs' }).getAttribute('href')).toBe('/runs')
  })

  it('says when nothing from the user’s own repositories has improved yet', () => {
    stubServer()
    renderDashboard([run({ id: 'demo', source: DEMO, mode: 'demo' })])
    expect(screen.getByText('No improvements from your own repositories yet')).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Start a run' }).getAttribute('href')).toBe('/new')
    expect(valueOf('CO2e saved')).toBe(NONE)
    expect(screen.queryByRole('region', { name: 'Yearly estimate' })).toBeNull()
  })

  it('leaves out Start a run while a run is active', () => {
    stubServer()
    renderDashboard([run({ id: 'busy', state: 'triaging', counts_by_outcome: {} })])
    expect(screen.getByText('No improvements from your own repositories yet')).toBeTruthy()
    expect(screen.queryByRole('link', { name: 'Start a run' })).toBeNull()
    expect(screen.getByRole('heading', { name: 'Running now' })).toBeTruthy()
  })

  it('offers a first run or a replay when there are no runs', () => {
    stubServer()
    renderDashboard([])
    expect(screen.getByText('No runs yet')).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Start a run' }).getAttribute('href')).toBe('/new')
    expect(screen.getByRole('link', { name: 'Watch a replay' }).getAttribute('href')).toBe('/replays')
    expect(screen.queryByText('Recent runs')).toBeNull()
  })
})
