// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { RunSummary } from '../gen/events'
import { AppShell } from './AppShell'
import { PageHeader } from './PageHeader'

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
  run({ id: 'done', source: { kind: 'github', url: 'https://github.com/example/textkit', ref: null } }),
  run({
    id: 'live',
    created_ts: 1_791_857_000_000,
    state: 'optimizing',
    source: { kind: 'github', url: 'https://github.com/acme/geo-utils', ref: null },
    functions_done: 2,
  }),
]

function stubServer(healthy: boolean) {
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string) => {
      if (url === '/api/health') {
        return healthy
          ? Promise.resolve(Response.json({ ok: true, version: '0.2.0' }))
          : Promise.reject(new TypeError('Failed to fetch'))
      }
      if (url === '/api/runs') return Promise.resolve(Response.json(RUNS))
      return Promise.resolve(new Response(null, { status: 404 }))
    }),
  )
}

function renderShell() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  client.setQueryData(['runs'], RUNS)
  client.setQueryData(['health'], { ok: true, version: '0.2.0' })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route element={<AppShell />}>
            <Route index element={<PageHeader title="Dashboard" />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

// The viewport starts narrow; `widen()` crosses the 1024px breakpoint.
let wide = false
const mediaListeners = new Set<() => void>()
function widen() {
  wide = true
  act(() => mediaListeners.forEach((fn) => fn()))
}

beforeEach(() => {
  wide = false
  mediaListeners.clear()
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      get matches() {
        return wide
      },
      media: query,
      onchange: null,
      addEventListener: (_: string, fn: () => void) => mediaListeners.add(fn),
      removeEventListener: (_: string, fn: () => void) => mediaListeners.delete(fn),
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }),
  })
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('AppShell', () => {
  it('renders the nav and each run’s quiet value', () => {
    stubServer(true)
    renderShell()
    const nav = screen.getByRole('navigation', { name: 'Main' })
    for (const name of ['Dashboard', 'Runs', 'Replays']) expect(within(nav).getByRole('link', { name })).toBeTruthy()
    expect(screen.getByRole('link', { name: 'New run' }).getAttribute('href')).toBe('/new')
    expect(screen.getByRole('link', { name: 'Settings' })).toBeTruthy()

    const live = screen.getByRole('link', { name: /geo-utils/ })
    expect(within(live).getByText('running')).toBeTruthy()
    const done = screen.getByRole('link', { name: /textkit/ })
    expect(within(done).getByText('−71%')).toBeTruthy()
    // the active run is pinned first
    const rows = screen.getAllByRole('listitem').map((li) => li.textContent)
    expect(rows[0]).toContain('geo-utils')

    expect(screen.getByText('Local server')).toBeTruthy()
    expect(screen.queryByText('Can’t reach the net-zero server')).toBeNull()
  })

  it('shows the offline line under the page header when the server is gone', async () => {
    stubServer(false)
    renderShell()
    const line = await screen.findByText('Can’t reach the net-zero server')
    const header = line.closest('header')
    expect(header).not.toBeNull()
    expect(within(header as HTMLElement).getByRole('heading', { name: 'Dashboard' })).toBeTruthy()
    expect(within(line).getByRole('button', { name: 'Retry' })).toBeTruthy()
    expect(screen.getByText('Server offline')).toBeTruthy()
  })

  it('closes the drawer when the window grows past the breakpoint', () => {
    stubServer(true)
    renderShell()
    fireEvent.click(screen.getByRole('button', { name: 'Open menu' }))
    expect(within(screen.getByRole('dialog')).getByRole('link', { name: 'New run' })).toBeTruthy()
    widen()
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})
