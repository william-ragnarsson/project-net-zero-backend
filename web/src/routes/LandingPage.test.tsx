// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LandingPage } from './LandingPage'

const INDEX = [
  {
    id: 'synthetic',
    title: 'Synthetic demo run',
    description: '',
    src: '/replays/synthetic.events.jsonl',
    functions: 6,
    duration_ms: 201_657,
  },
]

function renderLanding(index: unknown) {
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string) =>
      url.endsWith('index.json')
        ? Promise.resolve(Response.json(index))
        : Promise.resolve(new Response(null, { status: 404 })),
    ),
  )
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/']}>
        <LandingPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return client
}

// the hero's link; the same link comes back in the closing band
const href = (name: string) => screen.getAllByRole('link', { name })[0]!.getAttribute('href')

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('LandingPage', () => {
  it('says what it does and leads into the app', () => {
    renderLanding(INDEX)
    expect(screen.getByRole('heading', { level: 1, name: 'Make your Python use less energy.' })).toBeTruthy()
    expect(href('Start a run')).toBe('/new')
    expect(href('Open the app')).toBe('/dashboard')
    const nav = within(screen.getByRole('navigation', { name: 'Main' }))
    expect(nav.getByRole('link', { name: 'How it works' }).getAttribute('href')).toBe('#how')
    expect(nav.getByRole('link', { name: 'Replays' }).getAttribute('href')).toBe('/replays')
    for (const name of ['From a repository to a patch you can review', 'Every change has to earn its place']) {
      expect(screen.getByRole('heading', { level: 2, name })).toBeTruthy()
    }
    // the hero picture is labelled as scripted, not measured
    expect(screen.getByText(/numbers are examples, not measurements/)).toBeTruthy()
  })

  it('opens a menu on small screens', () => {
    renderLanding(INDEX)
    expect(screen.queryByRole('navigation', { name: 'Menu' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Open menu' }))
    const menu = within(screen.getByRole('navigation', { name: 'Menu' }))
    expect(menu.getByRole('link', { name: 'Open the app' }).getAttribute('href')).toBe('/dashboard')
    fireEvent.click(menu.getByRole('link', { name: 'The numbers' }))
    expect(screen.queryByRole('navigation', { name: 'Menu' })).toBeNull()
  })

  it('opens the first bundled replay once the index has loaded', async () => {
    renderLanding(INDEX)
    // the list until then
    expect(href('Watch a replay')).toBe('/replays')
    await vi.waitFor(() =>
      expect(screen.getAllByRole('link', { name: 'Watch a replay' }).map((a) => a.getAttribute('href'))).toEqual([
        '/replays/synthetic',
        '/replays/synthetic',
      ]),
    )
  })

  it('falls back to the list when there are no replays', async () => {
    const client = renderLanding([])
    await vi.waitFor(() => expect(client.getQueryState(['replays'])?.status).toBe('success'))
    expect(href('Watch a replay')).toBe('/replays')
  })
})
