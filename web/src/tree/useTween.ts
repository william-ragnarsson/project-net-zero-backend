// The tree's motion. Each node box eases from where it is towards its layout target, and the
// edges are redrawn from the moving boxes, once per animation frame. It writes styles straight
// to the DOM and never sets React state, and its loop stops when nothing moves or the tab hides.
import { useEffect, useState } from 'react'
import type { NodeId } from './derive'
import { edgePath } from './edges'
import { CARD_R, clamp, PILL_H, type Box, type EdgeSpec, type Layout } from './layout'

export const TWEEN_MS = 200
/** the fade-in of the tail label after it jumped to a far target */
const HOP_IN = 150

/** `cubic-bezier(0.22, 1, 0.36, 1)`, the app's one easing, as a function of progress 0 to 1. */
export function easeOut(u: number): number {
  if (u <= 0) return 0
  if (u >= 1) return 1
  const [x1, y1, x2, y2] = [0.22, 1, 0.36, 1]
  const cx = 3 * x1
  const bx = 3 * (x2 - x1) - cx
  const ax = 1 - cx - bx
  const cy = 3 * y1
  const by = 3 * (y2 - y1) - cy
  const ay = 1 - cy - by
  // solve x(t) = u by Newton's method; x is monotone on [0, 1] for these control points
  let t = u
  for (let i = 0; i < 8; i++) {
    const x = ((ax * t + bx) * t + cx) * t - u
    const dx = (3 * ax * t + 2 * bx) * t + cx
    if (Math.abs(x) < 1e-5 || Math.abs(dx) < 1e-6) break
    t = clamp(t - x / dx, 0, 1)
  }
  return ((ay * t + by) * t + cy) * t
}

/** A node box this frame: position, size, opacity, and `p`, how far it has opened into a card. */
interface Cur extends Box {
  o: number
  p: number
}

interface Tween {
  from: Cur
  to: Cur
  t0: number
  dur: number
  /** hidden while the nodes around it settle, then a short fade-in where it lands */
  hop: boolean
}

interface Rec {
  cur: Cur
  to: Cur
  tw: Tween | null
  open: boolean
}

export interface TreeParts {
  root: HTMLElement
  nodes: HTMLElement
  svg: SVGSVGElement
  layers: Record<EdgeSpec['layer'], SVGGElement>
}

export type Ticker = (dt: number, now: number) => boolean

export interface TreeEngine {
  mount(parts: TreeParts): void
  /** Moves every node towards `layout`; `instant` puts them there at once, with no animation. */
  apply(layout: Layout, edges: readonly EdgeSpec[], instant: boolean): void
  /** Runs once per frame after the tweens (the follow scroll); returns whether it still moves. */
  setTicker(ticker: Ticker | null): void
  kick(): void
  /** Starts listening for the tab coming back; `dispose` undoes it. */
  connect(): void
  dispose(): void
}

const SVG_NS = 'http://www.w3.org/2000/svg'

const same = (a: Cur, b: Cur): boolean =>
  Math.abs(a.x - b.x) < 0.05 &&
  Math.abs(a.y - b.y) < 0.05 &&
  Math.abs(a.w - b.w) < 0.05 &&
  Math.abs(a.h - b.h) < 0.05 &&
  a.o === b.o &&
  a.p === b.p

export function createTreeEngine(): TreeEngine {
  let parts: TreeParts | null = null
  const recs = new Map<NodeId, Rec>()
  let els = new Map<NodeId, HTMLElement>()
  const edgeEls = new Map<string, SVGElement>()
  const drawing = new Set<SVGElement>()
  const timers = new Set<number>()
  let edges: readonly EdgeSpec[] = []
  let ax = 0
  let raf = 0
  let last = 0
  let ticker: Ticker | null = null

  const boxOf = (id: NodeId): Box | undefined => recs.get(id)?.cur

  function applyNode(id: NodeId, r: Rec) {
    const el = els.get(id)
    if (el === undefined) return
    const c = r.cur
    el.style.transform = `translate(${c.x.toFixed(1)}px,${c.y.toFixed(1)}px)`
    el.style.opacity = c.o < 0.999 ? c.o.toFixed(3) : ''
    // the tail label sizes itself
    if (id === 'tail') return
    el.style.width = `${c.w.toFixed(1)}px`
    el.style.height = `${c.h.toFixed(1)}px`
    el.style.borderRadius = `${((PILL_H / 2) * (1 - c.p) + CARD_R * c.p).toFixed(1)}px`
    // the face fades out early in the morph and the card fades in late, so they barely overlap
    const fo = clamp(1 - c.p / 0.45, 0, 1)
    const co = clamp((c.p - 0.3) / 0.7, 0, 1)
    const face = el.querySelector<HTMLElement>('[data-face]')
    if (face !== null) {
      face.style.opacity = fo < 1 ? fo.toFixed(3) : ''
      face.style.visibility = r.open && fo <= 0.001 ? 'hidden' : ''
      face.style.pointerEvents = r.open ? 'none' : ''
    }
    const card = el.querySelector<HTMLElement>('[data-card-wrap]')
    if (card !== null) {
      card.style.opacity = co < 1 ? co.toFixed(3) : ''
      card.style.visibility = !r.open && co <= 0.001 ? 'hidden' : ''
      card.style.pointerEvents = r.open ? '' : 'none'
    }
  }

  function stepTweens(now: number): boolean {
    let moving = false
    for (const [id, r] of recs) {
      const tw = r.tw
      if (tw === null) continue
      const u = clamp((now - tw.t0) / tw.dur, 0, 1)
      if (tw.hop) {
        const t = u * tw.dur
        r.cur = { ...tw.to, o: t < TWEEN_MS ? 0 : tw.to.o * easeOut(clamp((t - TWEEN_MS) / HOP_IN, 0, 1)) }
      } else {
        const e = easeOut(u)
        const f = tw.from
        const g = tw.to
        r.cur = {
          x: f.x + (g.x - f.x) * e,
          y: f.y + (g.y - f.y) * e,
          w: f.w + (g.w - f.w) * e,
          h: f.h + (g.h - f.h) * e,
          o: f.o + (g.o - f.o) * e,
          p: f.p + (g.p - f.p) * e,
        }
      }
      if (u >= 1) {
        r.cur = { ...tw.to }
        r.tw = null
      } else moving = true
      applyNode(id, r)
    }
    return moving
  }

  /** A new edge draws itself in: a dash as long as the path slides from hidden to shown. */
  function startDraw(el: SVGElement, delay: number) {
    el.setAttribute('pathLength', '1')
    el.style.strokeDasharray = '1 1'
    el.style.strokeDashoffset = '1'
    el.style.transitionDelay = `${delay}ms`
    drawing.add(el)
    // resolve the start style now, so the change below is a transition and not the first style
    void getComputedStyle(el).strokeDashoffset
    el.style.strokeDashoffset = '0'
    const t = window.setTimeout(() => {
      timers.delete(t)
      drawing.delete(el)
      el.removeAttribute('pathLength')
      el.style.strokeDasharray = ''
      el.style.strokeDashoffset = ''
      el.style.transitionDelay = ''
      el.classList.remove('tree-draw')
    }, delay + TWEEN_MS + 60)
    timers.add(t)
  }

  function syncEdges(animate: boolean) {
    if (parts === null) return
    const keep = new Set<string>()
    for (const e of edges) {
      keep.add(e.id)
      let el = edgeEls.get(e.id)
      const fresh = el === undefined
      if (el === undefined) {
        el = document.createElementNS(SVG_NS, e.kind === 'dot' ? 'circle' : 'path')
        parts.layers[e.layer].appendChild(el)
        edgeEls.set(e.id, el)
      }
      el.setAttribute('class', `${e.kind === 'dot' ? 'tree-dot' : 'tree-edge'} ${e.cls}${drawing.has(el) ? ' tree-draw' : ''}`)
      if (fresh && animate && e.kind !== 'dot' && e.anim !== null) {
        el.classList.add('tree-draw')
        startDraw(el, e.delay ?? 0)
      }
    }
    for (const [id, el] of edgeEls) {
      if (keep.has(id)) continue
      el.remove()
      edgeEls.delete(id)
      drawing.delete(el)
    }
  }

  function drawEdges() {
    for (const e of edges) {
      const el = edgeEls.get(e.id)
      if (el === undefined) continue
      if (e.kind === 'dot') {
        const b = recs.get(e.at)?.cur
        if (b === undefined) continue
        el.setAttribute('cx', String(ax))
        el.setAttribute('cy', (b.y + 8).toFixed(1))
        el.setAttribute('r', String(e.r))
        el.style.opacity = b.o < 0.999 ? b.o.toFixed(3) : ''
        continue
      }
      const d = edgePath(e, boxOf, ax)
      if (el.getAttribute('d') !== d) el.setAttribute('d', d)
    }
  }

  function frame(now: number) {
    raf = 0
    // a hidden tab gets no frames; `visibilitychange` restarts the loop
    if (document.hidden) return
    const dt = clamp(now - last, 0, 64)
    last = now
    let busy = stepTweens(now)
    drawEdges()
    if (ticker !== null && ticker(dt, now)) busy = true
    if (busy) raf = requestAnimationFrame(frame)
  }

  function kick() {
    if (raf !== 0 || document.hidden) return
    last = performance.now()
    raf = requestAnimationFrame(frame)
  }

  const onVisible = () => {
    if (!document.hidden) kick()
  }

  return {
    mount(p) {
      parts = p
    },
    apply(layout, nextEdges, instant) {
      if (parts === null) return
      const now = performance.now()
      els = new Map()
      for (const el of parts.nodes.querySelectorAll<HTMLElement>('[data-node]')) {
        const id = el.dataset.node
        if (id !== undefined) els.set(id, el)
      }
      for (const id of layout.order) {
        const t = layout.targets.get(id)
        if (t === undefined) continue
        const to: Cur = { ...t.box, o: 1, p: t.open ? 1 : 0 }
        const r = recs.get(id)
        if (r === undefined) {
          const from: Cur = { ...to, x: t.from.x, y: t.from.y, o: 0 }
          recs.set(
            id,
            instant
              ? { cur: { ...to }, to, tw: null, open: t.open }
              : { cur: from, to, tw: { from, to, t0: now, dur: TWEEN_MS, hop: false }, open: t.open },
          )
          continue
        }
        r.open = t.open
        if (instant) {
          r.cur = { ...to }
          r.to = to
          r.tw = null
        } else if (!same(r.to, to)) {
          // the tail label jumps when a layer opens or folds above it: it hides, then fades in
          const hop = id === 'tail' && Math.abs(r.cur.y - to.y) > PILL_H
          r.tw = { from: { ...r.cur }, to, t0: now, dur: hop ? TWEEN_MS + HOP_IN : TWEEN_MS, hop }
          r.to = to
        }
      }
      for (const id of [...recs.keys()]) if (!layout.targets.has(id)) recs.delete(id)

      ax = layout.g.ax
      edges = nextEdges
      parts.root.style.height = `${layout.height}px`
      parts.svg.setAttribute('width', String(Math.ceil(layout.g.ax * 2)))
      parts.svg.setAttribute('height', String(layout.height))
      for (const [id, r] of recs) applyNode(id, r)
      syncEdges(!instant)
      drawEdges()
      if ([...recs.values()].some((r) => r.tw !== null)) kick()
    },
    setTicker(t) {
      ticker = t
    },
    kick,
    connect() {
      document.addEventListener('visibilitychange', onVisible)
    },
    dispose() {
      cancelAnimationFrame(raf)
      raf = 0
      for (const t of timers) window.clearTimeout(t)
      timers.clear()
      document.removeEventListener('visibilitychange', onVisible)
    },
  }
}

/** One engine per mounted tree. */
export function useTween(): TreeEngine {
  const [engine] = useState(createTreeEngine)
  useEffect(() => {
    engine.connect()
    return () => engine.dispose()
  }, [engine])
  return engine
}
