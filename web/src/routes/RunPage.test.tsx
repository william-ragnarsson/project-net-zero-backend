// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { RunEvent, TriageItem } from '../gen/events'
import type { LiveStatus } from '../sources/live'
import { createRunSession, type RunSessionStore } from '../state/store'
import { buildSyntheticRun } from '../synth/scenario'
import { makeEvent } from '../test/events'
import { RunPage } from './RunPage'

// The page reads its run through useLiveRun; the stub hands it a session the test drives.
const live = vi.hoisted(() => ({
  session: null as RunSessionStore | null,
  status: { kind: 'streaming' } as LiveStatus,
}))

vi.mock('../sources/useLiveRun', async () => {
  const { useStore } = await import('zustand')
  return {
    useLiveRun: () => {
      const session = live.session as RunSessionStore
      const model = useStore(session, (s) => s.state)
      const epoch = useStore(session, (s) => s.epoch)
      return { session, model, epoch, status: live.status, conn: null, retry: () => {} }
    },
  }
})

const { events: synth } = buildSyntheticRun()
const RUN = synth[0]!.run_id
const REPO = 'https://github.com/example/textkit'
const seqOf = (pred: (e: RunEvent) => boolean) => synth.find(pred)!.seq
const AWAITING = seqOf((e) => e.type === 'run.state_changed' && e.data.to_state === 'awaiting_selection')
const DISCOVERED = seqOf((e) => e.type === 'run.discovery.completed')

const triageItems = synth.flatMap((e) => (e.type === 'run.triage.completed' ? e.data.items : []))
const eligible = triageItems.filter((t) => t.skip_reason === null).slice(0, 3)
const ineligible = triageItems.find((t) => t.skip_reason !== null)!
/** three eligible rows, the first two recommended, then one that is not eligible */
const ITEMS: TriageItem[] = [
  ...eligible.map((t, i) => ({ ...t, preselected: i < 2 })),
  { ...ineligible, preselected: false },
]
const [A, B, C] = ITEMS.map((t) => t.function_id) as [string, string, string]

/** The synthetic run as a github run with max_functions 2 and the short triage list above. */
function asGithub(e: RunEvent): RunEvent {
  if (e.type === 'run.created') {
    return {
      ...e,
      data: {
        ...e.data,
        mode: 'live',
        source: { kind: 'github', url: REPO, ref: null },
        settings: { ...e.data.settings, max_functions: 2 },
      },
    }
  }
  if (e.type === 'run.triage.completed') return { ...e, data: { ...e.data, items: ITEMS, preselected: [A, B] } }
  return e
}

const through = (seq: number) => synth.filter((e) => e.seq <= seq).map(asGithub)

let calls: { url: string; method: string; body: unknown }[] = []
let selectReply: () => Response = () => Response.json({})

function stubServer() {
  calls = []
  selectReply = () => Response.json({})
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ url, method, body: typeof init?.body === 'string' ? JSON.parse(init.body) : null })
      if (url === '/api/health') return Promise.resolve(Response.json({ ok: true, version: '0.2.0' }))
      if (url === '/api/runs') return Promise.resolve(Response.json([]))
      if (url === `/api/runs/${RUN}/select`) return Promise.resolve(selectReply())
      if (url === `/api/runs/${RUN}/cancel`) return Promise.resolve(Response.json({}))
      return Promise.resolve(Response.json({ detail: 'no', code: 'not_found', active_run: null }, { status: 404 }))
    }),
  )
}

function open(events: RunEvent[], status: LiveStatus = { kind: 'streaming' }) {
  const session = createRunSession()
  if (events.length > 0) session.getState().hydrate(events)
  live.session = session
  live.status = status
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  client.setQueryData(['health'], { ok: true, version: '0.2.0' })
  client.setQueryData(['runs'], [])
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/runs/${RUN}`]}>
        <Routes>
          <Route path="/runs/:runId" element={<RunPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return session
}

const box = (name: RegExp) => screen.getByRole('checkbox', { name }) as HTMLInputElement
const startButton = () => screen.getByRole('button', { name: /Start optimizing|Starting/ }) as HTMLButtonElement
const posts = (path: string) => calls.filter((c) => c.method === 'POST' && c.url === path)

beforeEach(() => {
  stubServer()
  // the tree measures 0 wide in jsdom and draws nothing, which keeps these tests on the page
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('RunPage, choosing', () => {
  it('ticks the recommended rows and keeps ineligible ones apart', () => {
    open(through(AWAITING))
    expect(screen.getByRole('heading', { name: 'Choose functions' })).toBeTruthy()
    expect(box(new RegExp(ITEMS[0]!.qualname)).checked).toBe(true)
    expect(box(new RegExp(ITEMS[1]!.qualname)).checked).toBe(true)
    expect(box(new RegExp(ITEMS[2]!.qualname)).checked).toBe(false)
    expect(screen.getByText('Not eligible (1)')).toBeTruthy()
    expect(screen.getAllByRole('checkbox')).toHaveLength(3)
    expect(screen.getByText('2 selected')).toBeTruthy()
  })

  it('disables the start and says why at 0 and above the run’s maximum', () => {
    open(through(AWAITING))
    fireEvent.click(box(new RegExp(ITEMS[2]!.qualname)))
    expect(screen.getByText('Pick up to 2 functions')).toBeTruthy()
    expect(startButton().disabled).toBe(true)

    for (const t of ITEMS.slice(0, 3)) fireEvent.click(box(new RegExp(t.qualname)))
    expect(screen.getByText('Pick at least one function')).toBeTruthy()
    expect(startButton().disabled).toBe(true)
  })

  it('posts the picks in triage order and moves on only with the selection event', async () => {
    const session = open(through(AWAITING))
    // untick both, then tick the third and the first: the body still follows the ranking
    fireEvent.click(box(new RegExp(ITEMS[0]!.qualname)))
    fireEvent.click(box(new RegExp(ITEMS[1]!.qualname)))
    fireEvent.click(box(new RegExp(ITEMS[2]!.qualname)))
    fireEvent.click(box(new RegExp(ITEMS[0]!.qualname)))
    fireEvent.click(startButton())

    await waitFor(() => expect(startButton().textContent).toBe('Starting'))
    expect(posts(`/api/runs/${RUN}/select`).map((c) => c.body)).toEqual([{ function_ids: [A, C] }])
    // the answer came back, but the choice stays until the stream says so
    expect(screen.getByRole('heading', { name: 'Choose functions' })).toBeTruthy()
    expect(startButton().disabled).toBe(true)

    act(() => {
      session.getState().dispatchBatch([
        makeEvent(AWAITING + 1, 'run.selection.confirmed', { function_ids: [A, C], auto: false }, { run_id: RUN }),
        makeEvent(
          AWAITING + 2,
          'run.state_changed',
          { from_state: 'awaiting_selection', to_state: 'optimizing', reason: null },
          { run_id: RUN },
        ),
      ])
    })
    expect(screen.queryByRole('heading', { name: 'Choose functions' })).toBeNull()
    expect(screen.getByText('0 of 2 functions done')).toBeTruthy()
    expect(screen.getByRole('tab', { name: 'Tree' })).toBeTruthy()
    expect(screen.queryByText('The recommended functions were selected automatically')).toBeNull()
  })

  it('says so when the run already moved on', async () => {
    selectReply = () =>
      Response.json({ detail: 'run is optimizing', code: 'bad_state', active_run: null }, { status: 409 })
    open(through(AWAITING))
    fireEvent.click(startButton())
    expect(await screen.findByText('This run already moved on')).toBeTruthy()
  })

  it('tells the user when the server picked for them', () => {
    const session = open(through(AWAITING))
    act(() => {
      session.getState().dispatchBatch([
        makeEvent(AWAITING + 1, 'run.selection.confirmed', { function_ids: [A, B], auto: true }, { run_id: RUN }),
      ])
    })
    expect(screen.getByText('The recommended functions were selected automatically')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }))
    expect(screen.queryByText('The recommended functions were selected automatically')).toBeNull()
  })
})

describe('RunPage, other views', () => {
  it('cancels only on the second click', async () => {
    open(through(10))
    expect(screen.getByText('Clone the repository')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel run' }))
    expect(posts(`/api/runs/${RUN}/cancel`)).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: 'Confirm cancel' }))
    await waitFor(() => expect(posts(`/api/runs/${RUN}/cancel`)).toHaveLength(1))
    expect((screen.getByRole('button', { name: 'Cancelling' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('says when there is no such run', () => {
    open([], { kind: 'not_found' })
    expect(screen.getByRole('heading', { name: 'Run not found' })).toBeTruthy()
    expect(screen.getByText('There is no run with this id')).toBeTruthy()
    expect(screen.getByRole('link', { name: 'All runs' }).getAttribute('href')).toBe('/runs')
  })

  it('renders a failure reason in plain words', () => {
    // \u2265 is the server's math glyph, spelled as an escape so the copy grep stays clean
    const message = 'no function can be optimized: score \u2265 0.5 saves no CO2'
    open([
      ...through(DISCOVERED),
      makeEvent(
        DISCOVERED + 1,
        'run.state_changed',
        { from_state: 'discovering', to_state: 'failed', reason: null },
        { run_id: RUN },
      ),
      makeEvent(
        DISCOVERED + 2,
        'run.failed',
        { stage: 'discovering', error: { kind: 'validation', message, detail: null } },
        { run_id: RUN },
      ),
    ])
    const notice = screen.getByRole('alert')
    expect(notice.textContent).toBe(
      'Failed while finding functions: no function can be optimized: score is at least 0.5 saves no CO2',
    )
    expect(notice.textContent).not.toContain('\u2265')
    expect(within(notice).getByText('2').tagName).toBe('SUB')
    // nothing started, so the setup list stays with the failing step marked
    expect(screen.getByText('Scan the code')).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Run again' }).getAttribute('href')).toBe(
      `/new?repo=${encodeURIComponent(REPO)}`,
    )
  })

  it('offers the patch when the run is done', () => {
    const m = open(synth.map(asGithub)).getState().state
    const patch = m.steps.artifacts?.result?.patch
    expect(patch).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Download patch' }).getAttribute('href')).toBe(patch!.url)
    expect(screen.getByText(/functions improved, using/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Cancel run' })).toBeNull()
  })
})
