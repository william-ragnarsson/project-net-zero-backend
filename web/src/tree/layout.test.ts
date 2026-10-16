import { describe, expect, it } from 'vitest'
import type { RunEvent } from '../gen/events'
import { initialRunModel, reduce } from '../state/reducer'
import { buildSyntheticRun } from '../synth/scenario'
import { deriveTree, type NodeId, type Tree } from './derive'
import { backArrow, curve, edgePath, vline } from './edges'
import { cardWidth, GAP_C, geometry, kindOf, layoutTree, PILL_H, type Box, type Layout, type Size } from './layout'

const { events } = buildSyntheticRun()
const treeAt = (seq: number): Tree => deriveTree(reduce(initialRunModel(), events.filter((e: RunEvent) => e.seq <= seq)))
const END = deriveTree(reduce(initialRunModel(), events))

/** plausible measured sizes: pills by kind, cards a fixed height */
function sizesFor(t: Tree): Map<NodeId, Size> {
  const s = new Map<NodeId, Size>()
  s.set('pre', { w: 190, h: 260 })
  for (const f of t.functions) {
    s.set(f.id, { w: 230, h: 320 })
    for (const c of f.candidates) s.set(c.id, { w: c.letter === 'B' ? 96 : c.letter === 'A' ? 88 : 80, h: 380 })
    if (f.result !== null) s.set(f.result.id, { w: 120, h: 240 })
  }
  s.set('tail', { w: 110, h: 16 })
  return s
}

function lay(t: Tree, width: number, open: Iterable<NodeId> = []): Layout {
  return layoutTree({ tree: t, open: new Set(open), sizes: sizesFor(t), width, tail: { dot: 'q', dotted: true } })
}

const box = (l: Layout, id: NodeId): Box => {
  const b = l.targets.get(id)?.box
  if (b === undefined) throw new Error(`no box for ${id}`)
  return b
}
const cx = (b: Box) => b.x + b.w / 2
const withCands = (t: Tree) => t.functions.filter((f) => f.candidates.length === 3)

describe('geometry', () => {
  it('fits three candidate cards side by side only on a wide pane', () => {
    expect(geometry(1240)).toMatchObject({ ax: 620.5, cw: 336, wide: true })
    expect(geometry(900)).toMatchObject({ cw: 280, wide: false })
    expect(geometry(375)).toMatchObject({ ax: 188.5, avail: 343, wide: false })
  })

  it('names node kinds from their ids', () => {
    expect(['pre', 'tail', 'f/a.b:c', 'f/a.b:c/B', 'f/a.b:c/result'].map(kindOf)).toEqual([
      'pre',
      'tail',
      'function',
      'candidate',
      'result',
    ])
  })
})

describe('layoutTree', () => {
  for (const width of [1240, 900, 375]) {
    it(`mirrors A and C about the axis as pills at ${width}px`, () => {
      const l = lay(END, width)
      for (const f of withCands(END)) {
        const [a, b, c] = f.candidates.map((x) => box(l, x.id)) as [Box, Box, Box]
        expect(a.w).toBe(c.w)
        expect(l.g.ax - cx(a)).toBeCloseTo(cx(c) - l.g.ax, 6)
        expect(cx(b)).toBeCloseTo(l.g.ax, 6)
        // the row does not overlap
        expect(a.x + a.w).toBeLessThan(b.x)
        expect(b.x + b.w).toBeLessThan(c.x)
        expect(a.y).toBe(b.y)
        expect(a.h).toBe(PILL_H)
      }
    })

    it(`stacks layers top to bottom with the tail last at ${width}px`, () => {
      const l = lay(END, width)
      let prev = 0
      for (const f of END.functions) {
        const layer = l.layers.get(f.id)
        if (layer === undefined) throw new Error('no layer')
        expect(layer.top).toBeGreaterThan(prev)
        expect(layer.bot).toBeGreaterThan(layer.top)
        prev = layer.bot
      }
      expect(box(l, 'tail').y).toBeGreaterThan(prev)
      expect(l.height).toBeGreaterThan(box(l, 'tail').y)
      expect(l.order[0]).toBe('pre')
      expect(l.order.at(-1)).toBe('tail')
    })
  }

  it('lays open candidate cards in three lanes on a wide pane', () => {
    const f = withCands(END)[0]
    if (f === undefined) throw new Error('no function')
    const l = lay(END, 1240, [f.id, ...f.candidates.map((c) => c.id)])
    const g = l.g
    const [a, b, c] = f.candidates.map((x) => box(l, x.id)) as [Box, Box, Box]
    expect([cx(a), cx(b), cx(c)]).toEqual([g.ax - g.cw - GAP_C, g.ax, g.ax + g.cw + GAP_C])
    expect(a.w).toBe(cardWidth('candidate', g))
    expect(a.y).toBe(c.y)
    expect(box(l, f.id).w).toBe(460)
  })

  it('stacks open cards on the axis on a narrow pane, below the closed pills', () => {
    const f = withCands(END)[0]
    if (f === undefined) throw new Error('no function')
    const [ca, cb, cc] = f.candidates
    if (ca === undefined || cb === undefined || cc === undefined) throw new Error('no candidates')
    const l = lay(END, 900, [ca.id, cc.id])
    const b = box(l, cb.id)
    const a = box(l, ca.id)
    const c = box(l, cc.id)
    expect(b.h).toBe(PILL_H)
    expect(cx(a)).toBeCloseTo(l.g.ax, 6)
    expect(cx(c)).toBeCloseTo(l.g.ax, 6)
    expect(a.y).toBeGreaterThan(b.y + b.h)
    expect(c.y).toBeGreaterThan(a.y + a.h)
  })

  it('keeps every node inside the gutters on a phone', () => {
    const W = 375
    const all = END.functions.flatMap((f) => [f.id, ...f.candidates.map((c) => c.id)])
    for (const open of [[], all]) {
      const l = lay(END, W, open)
      for (const [id, t] of l.targets) {
        if (id === 'tail') continue
        expect(t.box.x).toBeGreaterThanOrEqual(16)
        // the axis sits on a half pixel, so a full-width card can reach half a pixel past it
        expect(t.box.x + t.box.w).toBeLessThanOrEqual(W - 15)
      }
    }
  })

  it('lays out a partial log: a running function ends the tree in its candidates', () => {
    const benchSeq = events.find((e) => e.type === 'candidate.bench.started')?.seq ?? 0
    const t = treeAt(benchSeq)
    const l = lay(t, 1240)
    const f = t.functions.at(-1)
    if (f === undefined) throw new Error('no function')
    expect(f.result).toBeNull()
    const tr = l.edges.find((e) => e.id === 'ttr')
    expect(tr).toMatchObject({ kind: 'vline', from: { ids: [f.id, ...f.candidates.map((c) => c.id)], edge: 'bot' } })
    // no candidate of the running function converges yet
    for (const c of f.candidates) expect(l.edges.some((e) => e.kind === 'conv' && e.node === c.id)).toBe(false)
    expect(l.layers.get(f.id)?.bot).toBe(Math.max(...f.candidates.map((c) => box(l, c.id).y + box(l, c.id).h)))
  })

  it('draws the winner path and the arrow back from a revert', () => {
    const l = lay(END, 1240)
    const ids = l.edges.map((e) => e.id)
    const [dedupe, avg] = END.functions
    if (dedupe === undefined || avg === undefined) throw new Error('no functions')
    expect(ids).toContain(`wf:${dedupe.id}`)
    expect(ids.some((id) => id.startsWith('fd:'))).toBe(false)
    expect(ids).not.toContain(`bb:${dedupe.id}`)
    expect(ids).toContain(`bb:${avg.id}`)
    expect(l.edges.find((e) => e.id === `wv:${avg.id}`)?.cls).toBe('w rvp')
  })
})

describe('edge paths', () => {
  it('draws straight lines when there is no horizontal offset', () => {
    expect(curve(10, 0, 10.2, 40)).toBe('M10.0 0.0V40.0')
    expect(curve(0, 0, 100, 100)).toBe('M0.0 0.0C0.0 62.0 100.0 38.0 100.0 100.0')
    expect(vline(5, 10, 10.3)).toBe('')
  })

  it('runs a short candidate down to the bottom of its row before it curves in', () => {
    const boxes = new Map<NodeId, Box>([
      ['a', { x: 0, y: 0, w: 20, h: 28 }],
      ['b', { x: 40, y: 0, w: 20, h: 100 }],
      ['r', { x: 40, y: 140, w: 20, h: 28 }],
    ])
    const d = edgePath({ id: 'v', layer: 'base', cls: 'e', anim: null, kind: 'conv', node: 'a', result: 'r', row: ['a', 'b'] }, (id) => boxes.get(id), 50)
    expect(d.startsWith('M10.0 28.0V100.0M10.0 100.0C')).toBe(true)
    expect(edgePath({ id: 'x', layer: 'base', cls: 'e', anim: null, kind: 'fork', from: 'a', to: 'gone' }, (id) => boxes.get(id), 50)).toBe('')
  })

  it('leaves out the back arrow when it would leave the pane', () => {
    expect(backArrow({ x: 20, y: 0, w: 80, h: 28 })).toBe('')
    expect(backArrow({ x: 200, y: 100, w: 80, h: 28 })).toMatch(/^M197\.0 114\.0C/)
  })
})
