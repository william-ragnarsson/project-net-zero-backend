// The run as a vertical tree of pills. Only the live function's layer opens into code cards, and
// it folds back to pills once that function is done; a pin opens any node's detail card.
// React renders the nodes; useTween moves them, and the window follows the live layer.
import { useEffect, useLayoutEffect, useReducer, useRef, useState, type ReactNode } from 'react'
import type { RunModel } from '../state/types'
import { buttonClass } from '../ui/Button'
import { CandidateCard } from './CandidateCard'
import type { CardProps } from './Card'
import { deriveTree, functionOfId, nodeIds, type NodeId, type Tree } from './derive'
import { FunctionCard } from './FunctionCard'
import { cardWidth, geometry, kindOf, layoutTree, PILL_H, TOP, type EdgeSpec, type Geometry, type Layout, type Size } from './layout'
import { candFaceOf, escapeUsed, fnFace, Pill, preFace, resultFaceOf, type Face } from './Pill'
import { PreCard } from './PreCard'
import { ResultCard } from './ResultCard'
import { TailLabel } from './TailLabel'
import { tailText } from './text'
import { FOLLOW_BOTTOM, followInset, followTarget, useFollow } from './useFollow'
import { useFrontierHold } from './useFrontierHold'
import { useReducedMotion } from './useReducedMotion'
import { TWEEN_MS, useTween } from './useTween'
import './tree.css'

export interface TreeProps {
  model: RunModel
  /** bumps when the session is rebuilt from scratch (hydrate, seek): the tree snaps, no motion */
  epoch: number
  speed: number
  /** a replay is playing, or a live run is streaming */
  playing: boolean
  /** the pinned node, from `?node=`; an id that is not in the tree yet is kept and not drawn */
  selectedId: NodeId | null
  onSelect: (id: NodeId | null) => void
  /** the identity of the run shown; a new one can restart at epoch 0 */
  session?: unknown
  /** the height of a sticky bar above the tree, kept clear when following */
  topInset?: number
  width: number
}

/** How long a step or a change keeps following active while paused. */
const FOLLOW_AFTER_MS = 1200
/** How long the end of a run keeps following, to bring the tail into view. */
const FOLLOW_END_MS = 1600
/** px of the node box around the face: its padding plus its border */
const FACE_PAD = 26

/** True while `on`, and for `ms` after it turns off, so a closing card can fade out. */
function useLinger(on: boolean, ms: number): boolean {
  const [shown, setShown] = useState(on)
  if (on && !shown) setShown(true)
  useEffect(() => {
    if (on) return
    const t = window.setTimeout(() => setShown(false), ms)
    return () => window.clearTimeout(t)
  }, [on, ms])
  return on || shown
}

function NodeBox({
  id,
  face,
  open,
  pinned,
  cardW,
  linger,
  onSelect,
  children,
}: {
  id: NodeId
  face: Face
  open: boolean
  pinned: boolean
  cardW: number
  linger: number
  onSelect: (id: NodeId | null) => void
  children: ReactNode
}) {
  const mounted = useLinger(open, linger)
  const box = open ? `bg-surface ${pinned ? 'border-ink-2' : 'border-control'}` : face.box
  return (
    <div data-node={id} className={`absolute left-0 top-0 overflow-hidden border transition-colors duration-150 ${box}`}>
      <Pill id={id} face={face} open={open} onSelect={onSelect} />
      {mounted && (
        <div data-card-wrap inert={!open} className="absolute left-0 top-0" style={{ width: cardW - 2 }}>
          {children}
        </div>
      )}
    </div>
  )
}

/** The scroll target while following: the open layer, the whole run once it ended, else the newest layer. */
function followBox(layout: Layout, tree: Tree, expandedId: NodeId | null): { top: number; bot: number } | null {
  if (expandedId !== null) {
    const l = layout.layers.get(expandedId)
    if (l !== undefined) return l
  }
  const tail = layout.targets.get('tail')
  if (tree.ended && tail !== undefined) return { top: TOP, bot: tail.box.y + tail.box.h }
  const last = tree.functions.at(-1)
  const l = last === undefined ? undefined : layout.layers.get(last.id)
  if (l !== undefined) return l
  const pre = layout.targets.get('pre')
  return pre === undefined ? null : { top: pre.box.y, bot: pre.box.y + pre.box.h }
}

function focusRecord(root: HTMLElement, el: Element | null): Frame['focus'] {
  if (el === null || el === document.body || !root.contains(el)) return null
  return { el, id: el.closest<HTMLElement>('[data-node]')?.dataset.node ?? null }
}

function signature(layout: Layout, edges: readonly EdgeSpec[]): string {
  const parts: string[] = [String(layout.height), String(layout.g.ax)]
  for (const id of layout.order) {
    const t = layout.targets.get(id)
    if (t === undefined) continue
    const b = t.box
    parts.push(`${id}:${b.x.toFixed(1)},${b.y.toFixed(1)},${b.w.toFixed(1)},${b.h.toFixed(1)},${t.open ? 1 : 0}`)
  }
  for (const e of edges) parts.push(`${e.id}:${e.cls}`)
  return parts.join('|')
}

interface Frame {
  layout: Layout | null
  tree: Tree | null
  sig: string
  width: number
  epoch: number
  session: unknown
  pinned: NodeId | null
  ended: boolean
  playing: boolean
  reduced: boolean
  expandedId: NodeId | null
  topInset: number
  /** the tree's top in page coordinates */
  treeTop: number
  /** following stays active until then, even while paused */
  until: number
  /** what had focus at the last commit, and in which node */
  focus: { el: Element; id: NodeId | null } | null
}

export function RunTree({ model, epoch, speed, playing, selectedId, onSelect, session, topInset = 16, width }: TreeProps) {
  const tree = deriveTree(model)
  const reduced = useReducedMotion()
  const hold = useFrontierHold(tree, model, epoch, speed, reduced, session)
  const expandedId = tree.ended ? null : hold.expandedId
  const ids = nodeIds(tree)
  const pinned = selectedId !== null && ids.has(selectedId) ? selectedId : null
  const expanded = expandedId === null ? undefined : tree.functions.find((f) => f.id === expandedId)
  const open = new Set<NodeId>()
  if (expanded !== undefined) {
    open.add(expanded.id)
    for (const c of expanded.candidates) open.add(c.id)
  }
  if (pinned !== null) open.add(pinned)
  const live = !tree.ended
  const g: Geometry = geometry(width)

  const engine = useTween()
  const follow = useFollow()
  const [, remeasure] = useReducer((n: number) => n + 1, 0)
  const rootRef = useRef<HTMLDivElement>(null)
  const nodesRef = useRef<HTMLDivElement>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const baseRef = useRef<SVGGElement>(null)
  const overRef = useRef<SVGGElement>(null)
  const dotsRef = useRef<SVGGElement>(null)
  const frame = useRef<Frame>({
    layout: null,
    tree: null,
    sig: '',
    width: 0,
    epoch: 0,
    session: undefined,
    pinned: null,
    ended: false,
    playing: false,
    reduced: false,
    expandedId: null,
    topInset,
    treeTop: 0,
    until: 0,
    focus: null,
  })
  const select = useRef({ selectedId, onSelect })

  // two passes: React rendered the faces and the open cards, now read their sizes, lay out, move
  useLayoutEffect(() => {
    const root = rootRef.current
    const nodes = nodesRef.current
    const svg = svgRef.current
    const base = baseRef.current
    const over = overRef.current
    const dots = dotsRef.current
    if (root === null || nodes === null || svg === null || base === null || over === null || dots === null) return
    engine.mount({ root, nodes, svg, layers: { base, over, dots } })
    select.current = { selectedId, onSelect }
    const s = frame.current
    const now = performance.now()

    // every read first
    const sizes = new Map<NodeId, Size>()
    const els = new Map<NodeId, HTMLElement>()
    for (const el of nodes.querySelectorAll<HTMLElement>('[data-node]')) {
      const id = el.dataset.node
      if (id === undefined) continue
      els.set(id, el)
      if (id === 'tail') {
        const label = el.firstElementChild
        const h = label instanceof HTMLElement ? label.offsetHeight : 0
        sizes.set(id, { w: label instanceof HTMLElement ? label.offsetWidth : 0, h: h > 0 ? h : 16 })
      } else if (open.has(id)) {
        const wrap = el.querySelector<HTMLElement>('[data-card-wrap]')
        sizes.set(id, { w: 0, h: Math.max(PILL_H, (wrap?.offsetHeight ?? 0) + 2) })
      } else {
        const inner = el.querySelector<HTMLElement>('[data-face-inner]')
        sizes.set(id, { w: Math.ceil(inner?.getBoundingClientRect().width ?? 0) + FACE_PAD, h: PILL_H })
      }
    }
    const treeTop = root.getBoundingClientRect().top + window.scrollY
    const active = document.activeElement

    // then the layout, and the writes
    const tt = tree.tail === null ? null : tailText(tree.tail, model)
    const layout = layoutTree({ tree, open, sizes, width, tail: tt === null ? null : { dot: tt.dot, dotted: tt.dotted } })
    // the live path is drawn a step darker; a display class only, the layout does not know it
    const frontier = new Set<string>()
    if (expanded !== undefined) {
      frontier.add(`tr:${expanded.id}`)
      for (const c of expanded.candidates) frontier.add(`f:${c.id}`).add(`v:${c.id}`)
    }
    if (live) frontier.add('ttr')
    const edges = layout.edges.map((e) => (e.layer === 'base' && frontier.has(e.id) ? { ...e, cls: `${e.cls} fr` } : e))

    const first = s.layout === null
    const rebuilt = !first && (width !== s.width || epoch !== s.epoch || session !== s.session)
    const sig = signature(layout, edges)
    if (first || rebuilt || sig !== s.sig) {
      engine.apply(layout, edges, first || rebuilt || reduced)
      // a change while paused (a step, a seek) still brings the live layer into view
      if (!first && width === s.width) s.until = now + FOLLOW_AFTER_MS
    }
    if (tree.ended && !s.ended && !first) s.until = now + FOLLOW_END_MS

    if (pinned !== s.pinned && pinned !== null) {
      const fe = expandedId === null ? null : functionOfId(expandedId)
      // opening something away from the live layer stops following, so it stays in view
      if (!tree.ended && functionOfId(pinned) !== fe) follow.stop()
      const activeFollow = follow.ref.current && ((playing && !tree.ended) || now < s.until)
      const t = layout.targets.get(pinned)
      if (!activeFollow && t !== undefined) {
        const inset = followInset(topInset) + 8
        const top = treeTop + t.box.y
        const bottom = top + t.box.h
        const y = window.scrollY
        let want: number | null = null
        if (bottom > y + window.innerHeight - FOLLOW_BOTTOM) want = Math.min(bottom - window.innerHeight + FOLLOW_BOTTOM, top - inset)
        else if (top < y + inset) want = top - inset
        if (want !== null && Math.abs(want - y) > 2) {
          window.scrollTo({ top: Math.max(0, want), behavior: first || reduced ? 'auto' : 'smooth' })
        }
      }
    }

    // focus repair: a face that opened, a card that closed, or a node that is gone
    const prev =
      active !== null && root.contains(active) && active.closest('[inert]') !== null
        ? { el: active, id: active.closest<HTMLElement>('[data-node]')?.dataset.node ?? null }
        : (active === null || active === document.body) &&
            s.focus !== null &&
            (!s.focus.el.isConnected || s.focus.el.closest('[inert]') !== null)
          ? s.focus
          : null
    if (prev !== null) {
      const target = (id: NodeId | null): HTMLElement | null => {
        if (id === null) return null
        const el = els.get(id)
        if (el === undefined) return null
        return el.querySelector<HTMLElement>(open.has(id) ? '[data-card-header]' : '[data-face]')
      }
      const fid = prev.id === null ? null : functionOfId(prev.id)
      const before = s.layout?.order ?? []
      const at = prev.id === null ? -1 : before.indexOf(prev.id)
      const earlier = before.slice(0, Math.max(0, at)).reverse()
      const into =
        target(prev.id) ??
        target(fid === null ? null : `f/${fid}`) ??
        earlier.map(target).find((el) => el !== null) ??
        target('pre')
      into?.focus({ preventScroll: true })
    }
    s.focus = focusRecord(root, document.activeElement)

    if (playing && !s.playing) engine.kick()
    Object.assign(s, {
      layout,
      tree,
      sig,
      width,
      epoch,
      session,
      pinned,
      ended: tree.ended,
      playing,
      reduced,
      expandedId,
      topInset,
      treeTop,
    })
    if (s.until > now) engine.kick()
  })

  // what has focus in the tree, so focus can be put back when that element goes inert or away
  useEffect(() => {
    const root = rootRef.current
    if (root === null) return
    const onIn = (e: FocusEvent) => {
      frame.current.focus = focusRecord(root, e.target instanceof Element ? e.target : null)
    }
    const onOut = (e: FocusEvent) => {
      const t = e.target
      const to = e.relatedTarget
      // focus moved out of the tree, or the viewer blurred it: there is nothing to repair. A
      // blur from an element that went inert or was removed keeps the record.
      const left = to instanceof Node ? !root.contains(to) : t instanceof Element && t.isConnected && t.closest('[inert]') === null
      if (left) frame.current.focus = null
    }
    root.addEventListener('focusin', onIn)
    root.addEventListener('focusout', onOut)
    return () => {
      root.removeEventListener('focusin', onIn)
      root.removeEventListener('focusout', onOut)
    }
  }, [])

  // a web font that loads late changes every pill's width
  useEffect(() => {
    let alive = true
    void document.fonts?.ready.then(() => {
      if (alive) remeasure()
    })
    return () => {
      alive = false
    }
  }, [])

  // the follow scroll runs in the engine's frame loop, after the tweens
  useEffect(() => {
    engine.setTicker((dt, now) => {
      const s = frame.current
      if (!follow.ref.current || s.layout === null || s.tree === null) return false
      if (!((s.playing && !s.ended) || now < s.until)) return false
      const box = followBox(s.layout, s.tree, s.expandedId)
      if (box === null) return false
      const want = followTarget(
        box,
        s.treeTop,
        followInset(s.topInset),
        window.innerHeight,
        document.documentElement.scrollHeight - window.innerHeight,
      )
      const cur = window.scrollY
      const dv = want - cur
      if (Math.abs(dv) < 1) return now < s.until
      const a = s.reduced ? 1 : 1 - Math.exp(-dt / 190)
      window.scrollTo(0, Math.abs(dv * a) < 0.6 ? want : cur + dv * a)
      return true
    })
    return () => engine.setTicker(null)
  }, [engine, follow.ref])

  // Esc lets the pin go, after a tooltip or a dialog had its turn
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented || escapeUsed.has(e)) return
      const { selectedId: sel, onSelect: set } = select.current
      if (sel !== null) set(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const resume = () => {
    follow.resume()
    frame.current.until = performance.now() + FOLLOW_AFTER_MS
    engine.kick()
  }

  const linger = reduced ? 0 : TWEEN_MS
  const props = (id: NodeId): CardProps => ({ id, pinned: pinned === id, framed: false, live, onSelect })
  const box = (id: NodeId, face: Face | null, card: ReactNode) =>
    face === null ? null : (
      <NodeBox
        key={id}
        id={id}
        face={face}
        open={open.has(id)}
        pinned={pinned === id}
        cardW={cardWidth(kindOf(id), g)}
        linger={linger}
        onSelect={onSelect}
      >
        {card}
      </NodeBox>
    )

  const nodes: ReactNode[] = [box('pre', preFace(tree.pre, model, pinned === 'pre'), <PreCard node={tree.pre} model={model} {...props('pre')} />)]
  for (const f of tree.functions) {
    nodes.push(
      box(f.id, fnFace(f, model, pinned === f.id, f.id === tree.frontierId), <FunctionCard node={f} model={model} {...props(f.id)} />),
    )
    for (const c of f.candidates) {
      nodes.push(box(c.id, candFaceOf(c, model, pinned === c.id), <CandidateCard node={c} model={model} {...props(c.id)} />))
    }
    const r = f.result
    if (r !== null) nodes.push(box(r.id, resultFaceOf(r, model, live, pinned === r.id), <ResultCard node={r} model={model} {...props(r.id)} />))
  }
  if (tree.tail !== null) {
    nodes.push(
      <div key="tail" data-node="tail" className="absolute left-0 top-0">
        <TailLabel tail={tree.tail} model={model} />
      </div>,
    )
  }

  return (
    <>
      <div ref={rootRef} className="relative overflow-x-clip">
        <svg ref={svgRef} aria-hidden="true" className="pointer-events-none absolute left-0 top-0 overflow-visible">
          <g ref={baseRef} />
          <g ref={overRef} />
          <g ref={dotsRef} />
        </svg>
        <div ref={nodesRef}>{nodes}</div>
      </div>
      {!follow.following && !tree.ended && <FollowChip onClick={resume} />}
    </>
  )
}

/** Shown once a hand scroll stopped following; a click brings the live layer back. */
export function FollowChip({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`fixed bottom-6 left-1/2 z-20 -translate-x-1/2 lg:left-[calc(50%+120px)] ${buttonClass('secondary', 'sm')} shadow-float`}
    >
      Follow the run
    </button>
  )
}
