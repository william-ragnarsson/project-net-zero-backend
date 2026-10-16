// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { RunSummary } from '../gen/events'
import { RunsPage } from './RunsPage'

function run(over: Partial<RunSummary>): RunSummary {
  return {
    id: 'r',
    created_ts: 1_791_857_368_385,
    updated_ts: 1_791_857_368_385,
    state: 'completed',
    source: { kind: 'github', url: 'https://github.com/example/textkit', ref: null },
    mode: 'live',
    functions_total: 8,
    functions_done: 8,
    counts_by_outcome: { accepted: 6 },
    mean_reduction_pct: -70.8,
    g_saved_per_1m_calls: 121.7,
    last_seq: 545,
    ...over,
  }
}

const RUNS = [
  run({ id: 'textkit' }),
  run({ id: 'demo', source: { kind: 'demo', url: null, ref: null }, mode: 'demo' }),
  run({
    id: 'broken',
    state: 'failed',
    source: { kind: 'github', url: 'https://github.com/acme/geo-utils', ref: null },
    functions_total: 0,
    functions_done: 0,
    counts_by_outcome: {},
    mean_reduction_pct: null,
    g_saved_per_1m_calls: 0,
  }),
]

/** The URL as the page left it, so tests can see what it wrote. */
function Where() {
  const { pathname, search } = useLocation()
  return <output data-testid="where">{pathname + search}</output>
}

function renderRuns(at = '/runs', state?: unknown, runs = RUNS) {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(Response.json(runs))))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  client.setQueryData(['runs'], runs)
  client.setQueryData(['health'], { ok: true, version: '0.2.0' })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[{ pathname: '/runs', search: at.slice('/runs'.length), state }]}>
        <Routes>
          <Route
            path="/runs"
            element={
              <>
                <RunsPage />
                <Where />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const where = () => screen.getByTestId('where').textContent
const tableNames = () =>
  within(screen.getByRole('table'))
    .getAllByRole('link')
    .map((a) => a.textContent)

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('RunsPage', () => {
  it('lists every run with NONE where a run has no functions', () => {
    renderRuns()
    expect(tableNames()).toEqual(['textkit', 'Demo repository', 'geo-utils'])
    const failed = screen.getByRole('table').querySelectorAll('tbody tr')[2]!
    expect(failed.textContent).toContain('Failed')
    expect(failed.textContent).not.toContain('0 of 0')
  })

  it('filters by repository and run type through the URL', () => {
    renderRuns()
    fireEvent.change(screen.getByLabelText('Filter by repository'), { target: { value: 'GEO' } })
    expect(where()).toBe('/runs?q=GEO')
    expect(tableNames()).toEqual(['geo-utils'])

    fireEvent.change(screen.getByLabelText('Filter by repository'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('radio', { name: 'Demo' }))
    expect(where()).toBe('/runs?mode=demo')
    expect(tableNames()).toEqual(['Demo repository'])

    fireEvent.click(screen.getByRole('radio', { name: 'All' }))
    expect(where()).toBe('/runs')
  })

  it('says what matched nothing and clears the filter', () => {
    renderRuns('/runs?q=nope&mode=live')
    expect(screen.getByText('No runs match “nope”')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Clear filter' }))
    expect(where()).toBe('/runs')
    expect(tableNames()).toHaveLength(3)
  })

  it('names the empty segment when there is no text', () => {
    renderRuns('/runs?mode=live', undefined, [RUNS[1]!])
    expect(screen.getByText('No live runs')).toBeTruthy()
    expect(screen.queryByText(/match/)).toBeNull()
  })

  it('focuses the filter when `/` brought the user here', () => {
    renderRuns('/runs', { focusFilter: true })
    expect(document.activeElement?.id).toBe('runs-filter')
  })
})
