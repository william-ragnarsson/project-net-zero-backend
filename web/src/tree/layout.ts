// Where every node of the tree goes. Pure: the tree, which nodes are open, the measured sizes
// and the pane width in; target boxes, edge specs and layer extents out. Edges are specs, not
// paths, so they can be redrawn every frame from the tweened boxes (edges.ts).
import type { CandidateId } from '../gen/events'
import type { NodeId, Tree } from './derive'

export const PILL_H = 28
export const CARD_R = 8
export const GAP_PRE = 40
export const GAP_FN = 34
export const FORK_PILL = 30
export const FORK_CARD = 40
export const CONV_PILL = 30
export const CONV_CARD = 38
export const GAP_C = 20
export const SPREAD_MIN = 132
export const PILL_GAP = 26
/** space above the setup pill */
export const TOP = 18
/** gap between the candidate cards stacked on the axis in narrow mode */
export const STACK_GAP = 14

export interface Box {
  x: number
  y: number
  w: number
  h: number
}

export interface Geometry {
  /** the axis, on a half pixel so 1px lines are crisp */
  ax: number
  avail: number
  /** candidate card width in wide mode */
  cw: number
  /** three candidate cards fit side by side */
  wide: boolean
}

export const clamp = (v: number, a: number, b: number): number => Math.max(a, Math.min(b, v))

export function geometry(width: number): Geometry {
  const cw = clamp(Math.floor((width - 48 - 2 * GAP_C) / 3), 280, 336)
  return {
    ax: Math.round(width / 2) + 0.5,
    avail: Math.max(240, width - 32),
    cw,
    wide: 3 * cw + 2 * GAP_C + 32 <= width,
  }
}

export type NodeKind = 'pre' | 'function' | 'candidate' | 'result' | 'tail'

export function kindOf(id: NodeId): NodeKind {
  if (id === 'pre') return 'pre'
  if (id === 'tail') return 'tail'
  if (id.endsWith('/result')) return 'result'
  if (/\/[ABC]$/.test(id)) return 'candidate'
  return 'function'
}

/** The width of a node's open card. */
export function cardWidth(kind: NodeKind, g: Geometry): number {
  switch (kind) {
    case 'pre':
      return Math.min(440, g.avail)
    case 'function':
      return Math.min(460, g.avail)
    case 'candidate':
      return g.wide ? g.cw : Math.min(380, g.avail)
    case 'result':
      return Math.min(420, g.avail)
    case 'tail':
      return 0
  }
}

/**
 * Measured sizes: a closed node's natural pill width (`w`), an open node's card height (`h`),
 * the tail label's width and height.
 */
export interface Size {
  w: number
  h: number
}

/** A point on the tweened boxes: the top or bottom of the lowest of `ids`, plus `dy`. */
export interface Anchor {
  ids: NodeId[]
  edge: 'top' | 'bot'
  dy?: number
}

interface EdgeBase {
  id: string
  layer: 'base' | 'over' | 'dots'
  cls: string
  /** 'grow' for a structural edge, 'draw' (with a delay) for the winner's path */
  anim: 'grow' | 'draw' | null
  delay?: number
}

export type EdgeSpec =
  | (EdgeBase & { kind: 'vline'; from: Anchor; to: Anchor })
  | (EdgeBase & { kind: 'fork'; from: NodeId; to: NodeId })
  | (EdgeBase & { kind: 'conv'; node: NodeId; result: NodeId; row: NodeId[] })
  | (EdgeBase & { kind: 'back'; result: NodeId })
  | (EdgeBase & { kind: 'dot'; at: NodeId; r: number })

export interface Target {
  box: Box
  open: boolean
  /** where a new node fades in from */
  from: { x: number; y: number }
}

export interface Layout {
  g: Geometry
  targets: Map<NodeId, Target>
  /** reading order, which is also DOM order */
  order: NodeId[]
  edges: EdgeSpec[]
  /** vertical extent of each function's layer, by function node id */
  layers: Map<NodeId, { top: number; bot: number }>
  /** pill widths forced so A and C mirror each other */
  faceW: Map<NodeId, number>
  height: number
}

export interface TailSpec {
  dot: 'q' | 'live' | 'end'
  dotted: boolean
}

export interface LayoutInput {
  tree: Tree
  open: ReadonlySet<NodeId>
  sizes: ReadonlyMap<NodeId, Size>
  width: number
  /** how the tail label connects; null when there is no tail */
  tail: TailSpec | null
}

export function layoutTree(input: LayoutInput): Layout {
  const { tree, open, sizes, width } = input
  const g = geometry(width)
  const AX = g.ax
  const targets = new Map<NodeId, Target>()
  const order: NodeId[] = []
  const edges: EdgeSpec[] = []
  const layers = new Map<NodeId, { top: number; bot: number }>()
  const faceW = new Map<NodeId, number>()

  const isOpen = (id: NodeId) => open.has(id)
  const pillW = (id: NodeId) => Math.max(0, sizes.get(id)?.w ?? 0)
  const sz = (id: NodeId): Size =>
    isOpen(id) ? { w: cardWidth(kindOf(id), g), h: sizes.get(id)?.h ?? PILL_H } : { w: pillW(id), h: PILL_H }
  const place = (id: NodeId, x: number, y: number, w: number, h: number, from?: (b: Box) => { x: number; y: number }) => {
    const box = { x, y, w, h }
    order.push(id)
    targets.set(id, { box, open: isOpen(id), from: from ? from(box) : { x, y: y - 6 } })
  }
  const bot = (id: NodeId): Anchor => ({ ids: [id], edge: 'bot' })
  const top = (id: NodeId): Anchor => ({ ids: [id], edge: 'top' })

  let y = TOP
  {
    const z = sz('pre')
    place('pre', AX - z.w / 2, y, z.w, z.h)
    y += z.h
  }
  let prevBot = bot('pre') as Anchor | null
  let runBot = null as Anchor | null

  for (const [k, fn] of tree.functions.entries()) {
    const o = fn.id
    y += k === 0 ? GAP_PRE : GAP_FN
    const layerTop = y
    const oz = sz(o)
    place(o, AX - oz.w / 2, y, oz.w, oz.h)
    if (prevBot) edges.push({ id: `tr:${o}`, layer: 'base', cls: 'e', anim: 'grow', kind: 'vline', from: prevBot, to: top(o) })
    y += oz.h
    const cands = fn.candidates
    if (cands.length === 0) {
      prevBot = bot(o)
      runBot = fn.status === 'running' ? prevBot : null
      layers.set(o, { top: layerTop, bot: y })
      continue
    }
    const byLetter = new Map<CandidateId, NodeId>(cands.map((c) => [c.letter, c.id]))
    const ids = cands.map((c) => c.id)
    const anyOpen = ids.some(isOpen)
    const forkTop = y
    y += anyOpen ? FORK_CARD : FORK_PILL
    const rowTop = y
    // A and C share a pill width so they mirror each other about the axis; B sizes itself
    const sides = cands.filter((c) => c.letter !== 'B' && !isOpen(c.id))
    const sideW = Math.max(0, ...sides.map((c) => pillW(c.id)))
    for (const c of sides) faceW.set(c.id, sideW)
    const pw = (L: CandidateId, id: NodeId) => (L === 'B' ? pillW(id) : sideW)
    const wz = (L: CandidateId, id: NodeId) => (isOpen(id) ? cardWidth('candidate', g) : pw(L, id))
    const hz = (id: NodeId) => (isOpen(id) ? (sizes.get(id)?.h ?? PILL_H) : PILL_H)
    const fromFork = (t: Box) => ({ x: t.x + (AX - (t.x + t.w / 2)) * 0.3, y: Math.max(forkTop, t.y - 14) })
    const pillD = () => {
      const b = byLetter.get('B')
      const wB = b !== undefined && !isOpen(b) ? pillW(b) : 0
      const d = Math.max(SPREAD_MIN, wB / 2 + PILL_GAP + sideW / 2)
      return Math.min(d, Math.max(wB / 2 + 8 + sideW / 2, g.avail / 2 - sideW / 2))
    }
    const centre = (L: CandidateId, d: number) => (L === 'A' ? AX - d : L === 'C' ? AX + d : AX)
    let row: NodeId[]
    if (anyOpen && g.wide) {
      // card lanes: A and C one card width plus a gutter either side of B
      const d = g.cw + GAP_C
      let rh = 0
      for (const c of cands) {
        const w = wz(c.letter, c.id)
        place(c.id, centre(c.letter, d) - w / 2, rowTop, w, hz(c.id), fromFork)
        rh = Math.max(rh, hz(c.id))
      }
      y = rowTop + rh
      row = ids
    } else if (anyOpen) {
      // narrow: pills stay in a row, open cards stack on the axis below it
      const pills = cands.filter((c) => !isOpen(c.id))
      const cards = cands.filter((c) => isOpen(c.id))
      let rh = 0
      if (pills.length > 0) {
        const d = pillD()
        for (const c of pills) {
          const w = pw(c.letter, c.id)
          place(c.id, centre(c.letter, d) - w / 2, rowTop, w, PILL_H, fromFork)
        }
        rh = PILL_H
      }
      y = rowTop + rh
      cards.forEach((c, j) => {
        if (rh > 0 || j > 0) y += STACK_GAP
        const w = wz(c.letter, c.id)
        place(c.id, AX - w / 2, y, w, hz(c.id), fromFork)
        y += hz(c.id)
      })
      row = pills.map((c) => c.id)
    } else {
      const d = pillD()
      for (const c of cands) {
        const w = pw(c.letter, c.id)
        place(c.id, centre(c.letter, d) - w / 2, rowTop, w, PILL_H, fromFork)
      }
      y = rowTop + PILL_H
      row = ids
    }
    for (const c of cands) edges.push({ id: `f:${c.id}`, layer: 'base', cls: 'e', anim: 'grow', kind: 'fork', from: o, to: c.id })
    const res = fn.result
    if (res !== null) {
      y += anyOpen ? CONV_CARD : CONV_PILL
      const r = res.id
      const rz = sz(r)
      place(r, AX - rz.w / 2, y, rz.w, rz.h, (t) => ({ x: t.x, y: t.y - 10 }))
      for (const c of cands) {
        const rej = c.phase === 'rejected'
        edges.push({ id: `v:${c.id}`, layer: 'base', cls: rej ? 'e rj' : 'e', anim: rej ? null : 'grow', kind: 'conv', node: c.id, result: r, row })
      }
      const w = res.winner === null ? undefined : byLetter.get(res.winner)
      if (w !== undefined) {
        const rv = res.result === 'reverted'
        const cls = rv ? 'w rvp' : 'w'
        edges.push({ id: `wf:${o}`, layer: 'over', cls, anim: 'draw', delay: 0, kind: 'fork', from: o, to: w })
        edges.push({ id: `wv:${o}`, layer: 'over', cls, anim: 'draw', delay: 120, kind: 'conv', node: w, result: r, row })
        if (rv) edges.push({ id: `bb:${o}`, layer: 'over', cls: 'rv', anim: null, kind: 'back', result: r })
      }
      y += rz.h
      prevBot = bot(r)
      runBot = null
    } else {
      prevBot = null
      runBot = { ids: [o, ...ids], edge: 'bot' }
    }
    layers.set(o, { top: layerTop, bot: y })
  }

  const tail = tree.tail
  if (tail !== null && input.tail !== null) {
    const spec = input.tail
    const from = prevBot ?? runBot
    y += GAP_FN - 4
    const s = sizes.get('tail') ?? { w: 0, h: 16 }
    place('tail', AX + 13, y, s.w, s.h)
    const ty: Anchor = { ids: ['tail'], edge: 'top', dy: 8 }
    if (from) {
      edges.push({ id: 'ttr', layer: 'base', cls: spec.dotted ? 'e rj' : 'e', anim: spec.dotted ? null : 'grow', kind: 'vline', from, to: ty })
    }
    edges.push({ id: 'tdot', layer: 'dots', cls: spec.dot, anim: null, kind: 'dot', at: 'tail', r: spec.dot === 'end' ? 3 : 2.5 })
    y += s.h
  }

  return { g, targets, order, edges, layers, faceW, height: Math.ceil(y + 40) }
}
