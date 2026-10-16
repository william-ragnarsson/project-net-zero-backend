// @vitest-environment jsdom
import * as Tooltip from '@radix-ui/react-tooltip'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { initialRunModel, reduce } from '../state/reducer'
import type { RunModel } from '../state/types'
import { buildSyntheticRun } from '../synth/scenario'
import { deriveTree } from './derive'
import { repoName } from './Pill'
import { RunTree } from './RunTree'
import { RunView } from './RunView'

const { events } = buildSyntheticRun()
const ended = reduce(initialRunModel(), events)
/** partway through the run: the second function is open */
const partial = (): RunModel => {
  const at = events.findIndex((e, i) => e.type === 'function.started' && events.slice(0, i).some((p) => p.type === 'function.decision'))
  return reduce(initialRunModel(), events.slice(0, at + 12))
}

/** the containers' width, as the ResizeObserver stub reports it */
let observedWidth = 1280

beforeEach(() => {
  observedWidth = 1280
  vi.stubGlobal(
    'ResizeObserver',
    class {
      cb: ResizeObserverCallback
      constructor(cb: ResizeObserverCallback) {
        this.cb = cb
      }
      observe() {
        this.cb([{ contentRect: { width: observedWidth } } as ResizeObserverEntry], this as unknown as ResizeObserver)
      }
      unobserve() {}
      disconnect() {}
    },
  )
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: false,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }))
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => window.setTimeout(() => cb(performance.now()), 16))
  vi.stubGlobal('cancelAnimationFrame', (id: number) => window.clearTimeout(id))
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  // a page taller than the window, so a wheel can scroll it
  Object.defineProperty(document.documentElement, 'scrollHeight', { configurable: true, get: () => 5000 })
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: 800 })
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function tree(model: RunModel, over: { selectedId?: string | null; onSelect?: (id: string | null) => void } = {}) {
  const onSelect = over.onSelect ?? vi.fn()
  const view = render(
    <Tooltip.Provider>
      <RunTree
        model={model}
        epoch={0}
        speed={1}
        playing={false}
        selectedId={over.selectedId ?? null}
        onSelect={onSelect}
        width={1280}
      />
    </Tooltip.Provider>,
  )
  const faces = () => [...view.container.querySelectorAll<HTMLElement>('[data-face]')]
  const face = (id: string) => view.container.querySelector<HTMLElement>(`[data-node="${CSS.escape(id)}"] [data-face]`)
  return { ...view, faces, face }
}

describe('RunTree', () => {
  it('shows the repository on the root pill and each function by its name alone', () => {
    const { face } = tree(ended)
    expect(face('pre')?.textContent).toContain(repoName(ended))
    const fns = deriveTree(ended).functions
    expect(fns.length).toBeGreaterThan(0)
    for (const f of fns) {
      const text = face(f.id)?.textContent ?? ''
      expect(text.startsWith(f.qualname)).toBe(true)
      expect(text).not.toMatch(/^\d/)
    }
  })

  it('draws a mark only for a pass or a failure', () => {
    const { faces } = tree(ended)
    const CHECK = 'M3.5 8.5l3 3 6-7'
    const CLOSE = 'M4 4l8 8M12 4l-8 8'
    const marks = faces().flatMap((f) => [...f.querySelectorAll('svg')])
    expect(marks.length).toBeGreaterThan(0)
    for (const svg of marks) expect([CHECK, CLOSE]).toContain(svg.querySelector('path')?.getAttribute('d'))
    // no status dot or ring anywhere in a face
    for (const f of faces()) expect(f.querySelector('circle')).toBeNull()
  })

  it('pins a node when its face is clicked', () => {
    const onSelect = vi.fn()
    const { face } = tree(ended, { onSelect })
    const first = deriveTree(ended).functions[0]
    if (first === undefined) throw new Error('no function')
    fireEvent.click(face(first.id) as HTMLElement)
    expect(onSelect).toHaveBeenCalledWith(first.id)
  })

  it('lets the pin go on Escape', () => {
    const onSelect = vi.fn()
    tree(ended, { selectedId: 'pre', onSelect })
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onSelect).toHaveBeenCalledWith(null)
  })

  it('offers to follow again after a hand scroll while the run goes on', () => {
    const model = partial()
    expect(deriveTree(model).ended).toBe(false)
    tree(model)
    expect(screen.queryByText('Follow the run')).toBeNull()
    act(() => {
      fireEvent.wheel(window, { deltaY: 120 })
    })
    expect(screen.getByText('Follow the run')).toBeTruthy()
  })
})

describe('RunView', () => {
  it('lists the run as rows in a narrow container', () => {
    observedWidth = 375
    const { container } = render(
      <RunView model={ended} epoch={0} speed={1} playing={false} selectedId={null} onSelect={() => {}} />,
    )
    const rows = [...container.querySelectorAll<HTMLElement>('[data-row]')]
    expect(rows.length).toBeGreaterThan(deriveTree(ended).functions.length)
    expect(rows[0]?.textContent).toContain(repoName(ended))
    expect(container.querySelector('[data-face]')).toBeNull()
    expect(container.querySelector('svg[aria-hidden] g')).toBeNull()
  })

  it('draws the tree in a wide container', () => {
    observedWidth = 1280
    const { container } = render(
      <RunView model={ended} epoch={0} speed={1} playing={false} selectedId={null} onSelect={() => {}} />,
    )
    expect(container.querySelector('[data-row]')).toBeNull()
    expect(container.querySelectorAll('[data-face]').length).toBeGreaterThan(0)
  })
})
