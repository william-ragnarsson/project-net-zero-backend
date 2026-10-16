// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useParams } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Capabilities, RunSummary } from '../gen/events'
import { NewRunPage } from './NewRunPage'

function run(over: Partial<RunSummary> = {}): RunSummary {
  return {
    id: '20261013-120000-example-textkit',
    created_ts: 1_791_857_368_385,
    updated_ts: 1_791_857_368_385,
    state: 'created',
    source: { kind: 'github', url: 'https://github.com/example/textkit', ref: null },
    mode: 'live',
    functions_total: 0,
    functions_done: 0,
    counts_by_outcome: {},
    mean_reduction_pct: null,
    g_saved_per_1m_calls: 0,
    last_seq: 1,
    ...over,
  }
}

const CAPS: Capabilities = {
  version: '0.2.0',
  has_api_key: true,
  modes: ['live', 'demo'],
  active_run: null,
  platform: 'darwin-arm64',
  python: '3.12.15',
  tools: { git: true, uv: true },
  power: null,
  models: {},
  demo_available: true,
}

let posts: unknown[] = []

function stubServer(reply: () => Response) {
  posts = []
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      if (url === '/api/runs' && init?.method === 'POST') {
        posts.push(JSON.parse(String(init.body)))
        return Promise.resolve(reply())
      }
      if (url === '/api/runs') return Promise.resolve(Response.json([]))
      if (url === '/api/health') return Promise.resolve(Response.json({ ok: true, version: '0.2.0' }))
      return Promise.resolve(new Response(null, { status: 404 }))
    }),
  )
}

function RunStub() {
  const { runId = '' } = useParams()
  return <p>Run page {runId}</p>
}

function renderPage({ caps = CAPS, runs = [], at = '/new' }: { caps?: Capabilities; runs?: RunSummary[]; at?: string } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  client.setQueryData(['capabilities'], caps)
  client.setQueryData(['runs'], runs)
  client.setQueryData(['health'], { ok: true, version: '0.2.0' })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[at]}>
        <Routes>
          <Route path="/new" element={<NewRunPage />} />
          <Route path="/runs/:runId" element={<RunStub />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const repoInput = () => screen.getByLabelText('GitHub repository') as HTMLInputElement
const startButton = () => screen.getByRole('button', { name: 'Start run' }) as HTMLButtonElement

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('NewRunPage', () => {
  it('shows a field error for an invalid URL and sends nothing', () => {
    stubServer(() => Response.json(run(), { status: 201 }))
    renderPage()
    fireEvent.change(repoInput(), { target: { value: 'example/textkit on gitlab' } })
    fireEvent.click(startButton())

    expect(screen.getByText('Enter a GitHub repository URL, like https://github.com/owner/repo')).toBeTruthy()
    expect(repoInput().getAttribute('aria-invalid')).toBe('true')
    expect(posts).toEqual([])
  })

  it('posts the normalized URL with no auto_select and opens the new run', async () => {
    stubServer(() => Response.json(run(), { status: 201 }))
    renderPage()
    fireEvent.change(repoInput(), { target: { value: '  github.com/example/textkit.git  ' } })
    fireEvent.click(startButton())

    await screen.findByText('Run page 20261013-120000-example-textkit')
    expect(posts).toEqual([{ github_url: 'https://github.com/example/textkit', ref: null }])
    expect(posts[0]).not.toHaveProperty('auto_select')
  })

  it('prefills from Run again and sends the ref', async () => {
    stubServer(() => Response.json(run(), { status: 201 }))
    renderPage({ at: `/new?repo=${encodeURIComponent('https://github.com/acme/geo-utils')}&ref=dev` })
    expect(repoInput().value).toBe('https://github.com/acme/geo-utils')
    expect((screen.getByLabelText('Branch or tag') as HTMLInputElement).value).toBe('dev')
    fireEvent.click(startButton())

    await waitFor(() => expect(posts).toEqual([{ github_url: 'https://github.com/acme/geo-utils', ref: 'dev' }]))
  })

  it('links to the run in the way on a 409', async () => {
    const other = run({ id: 'busy-run', state: 'optimizing' })
    stubServer(() =>
      Response.json({ detail: 'A run is already active', code: 'run_active', active_run: other }, { status: 409 }),
    )
    renderPage()
    fireEvent.change(repoInput(), { target: { value: 'https://github.com/example/textkit' } })
    fireEvent.click(startButton())

    await screen.findByText('Another run is in progress')
    expect(screen.getByRole('link', { name: 'Open it' }).getAttribute('href')).toBe('/runs/busy-run')
    expect(startButton().disabled).toBe(true)
  })

  it('names an active run from the list before anything is sent', () => {
    stubServer(() => Response.json(run(), { status: 201 }))
    renderPage({ runs: [run({ id: 'listed', state: 'awaiting_selection' })] })

    expect(screen.getByText('Another run is in progress')).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Open it' }).getAttribute('href')).toBe('/runs/listed')
    expect(startButton().disabled).toBe(true)
    expect((screen.getByRole('button', { name: 'Run the demo' }) as HTMLButtonElement).disabled).toBe(
      true,
    )
  })

  it('offers only the demo when the server has no live mode', async () => {
    stubServer(() => Response.json(run({ id: 'demo-run', source: { kind: 'demo', url: null, ref: null } }), { status: 201 }))
    renderPage({ caps: { ...CAPS, has_api_key: false, modes: ['demo'] } })

    expect(screen.getByText('Live runs need an Anthropic API key on the server')).toBeTruthy()
    expect(screen.getByRole('link', { name: 'How to set it' }).getAttribute('href')).toBe('/settings#api-key')
    expect(repoInput().disabled).toBe(true)
    expect(startButton().disabled).toBe(true)
    const demo = screen.getByRole('button', { name: 'Run the demo' }) as HTMLButtonElement
    expect(demo.className).toContain('bg-accent')
    expect(demo.disabled).toBe(false)

    fireEvent.click(demo)
    await screen.findByText('Run page demo-run')
    expect(posts).toEqual([{ demo: true }])
  })

  it('disables everything when the server can start nothing', () => {
    stubServer(() => Response.json(run(), { status: 201 }))
    renderPage({ caps: { ...CAPS, has_api_key: false, modes: ['demo'], demo_available: false } })

    expect(screen.getByText('This server can’t start runs yet')).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Open settings' }).getAttribute('href')).toBe('/settings#api-key')
    expect(screen.queryByText('Live runs need an Anthropic API key on the server')).toBeNull()
    expect(screen.queryByRole('button', { name: /demo/ })).toBeNull()
    expect(startButton().disabled).toBe(true)
  })

  it('shows a 412 as a failure notice, not as the API key copy', async () => {
    stubServer(() => Response.json({ detail: 'The server is still starting', code: 'not_ready', active_run: null }, { status: 412 }))
    renderPage()
    fireEvent.change(repoInput(), { target: { value: 'https://github.com/example/textkit' } })
    fireEvent.click(startButton())

    expect((await screen.findByRole('alert')).textContent).toBe('The server is still starting')
    expect(screen.queryByText('Live runs need an Anthropic API key on the server')).toBeNull()
  })

  it('shows a server field error under the repository', async () => {
    stubServer(() => Response.json({ detail: 'Not a public GitHub repository', code: 'invalid_url', active_run: null }, { status: 400 }))
    renderPage()
    fireEvent.change(repoInput(), { target: { value: 'https://github.com/example/textkit' } })
    fireEvent.click(startButton())

    expect((await screen.findByText('Not a public GitHub repository')).id).toBe('repo-error')
  })
})
