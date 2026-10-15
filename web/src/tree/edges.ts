// Connector paths, drawn from the boxes as they are this frame (tweened), not from the targets,
// so a connector stays attached while its nodes move. Pure; the engine owns the SVG elements.
import type { NodeId } from './derive'
import { PILL_H, type Anchor, type Box, type EdgeSpec } from './layout'

export type BoxOf = (id: NodeId) => Box | undefined

const f1 = (n: number): string => n.toFixed(1)

/** A vertical line from a down to b, or nothing when b is not below a. */
export const vline = (x: number, a: number, b: number): string => (b > a + 0.5 ? `M${f1(x)} ${f1(a)}V${f1(b)}` : '')

/** An S-curve from (x0, y0) down to (x1, y1), leaving and arriving vertically. */
export function curve(x0: number, y0: number, x1: number, y1: number): string {
  if (Math.abs(x1 - x0) < 0.5) return `M${f1(x0)} ${f1(y0)}V${f1(y1)}`
  const k = (y1 - y0) * 0.62
  return `M${f1(x0)} ${f1(y0)}C${f1(x0)} ${f1(y0 + k)} ${f1(x1)} ${f1(y1 - k)} ${f1(x1)} ${f1(y1)}`
}

const bottom = (b: Box): number => b.y + b.h
const centre = (b: Box): number => b.x + b.w / 2

/** The y of an anchor, or null while none of its nodes has a box. */
export function anchorY(a: Anchor, boxOf: BoxOf): number | null {
  let y: number | null = null
  for (const id of a.ids) {
    const b = boxOf(id)
    if (b === undefined) continue
    const v = a.edge === 'bot' ? bottom(b) : b.y
    y = y === null ? v : Math.max(y, v)
  }
  return y === null ? null : y + (a.dy ?? 0)
}

/** A candidate's line into the result: down to the bottom of its row, then a curve to the axis. */
export function conv(node: Box, result: Box, row: readonly Box[] | null, ax: number): string {
  const x = centre(node)
  const b0 = bottom(node)
  const rb = row === null ? b0 : Math.max(b0, ...row.map(bottom))
  return vline(x, b0, rb) + curve(x, rb, ax, result.y)
}

/** The caution arrow curling back up from a reverted result, or nothing when it would leave the pane. */
export function backArrow(r: Box): string {
  const x0 = r.x - 3
  const ym = r.y + Math.min(r.h, PILL_H) / 2
  const x1 = x0 - 30
  const y1 = ym - 30
  if (x1 < 6) return ''
  return (
    `M${f1(x0)} ${f1(ym)}C${f1(x0 - 22)} ${f1(ym)} ${f1(x1)} ${f1(ym - 6)} ${f1(x1)} ${f1(y1)}` +
    `M${f1(x1 - 3.5)} ${f1(y1 + 4.5)}L${f1(x1)} ${f1(y1)}L${f1(x1 + 3.5)} ${f1(y1 + 4.5)}`
  )
}

/** The path of an edge this frame; '' while a node it needs has no box. */
export function edgePath(e: EdgeSpec, boxOf: BoxOf, ax: number): string {
  switch (e.kind) {
    case 'vline': {
      const a = anchorY(e.from, boxOf)
      const b = anchorY(e.to, boxOf)
      return a === null || b === null ? '' : vline(ax, a, b)
    }
    case 'fork': {
      const o = boxOf(e.from)
      const c = boxOf(e.to)
      return o === undefined || c === undefined ? '' : curve(ax, bottom(o), centre(c), c.y)
    }
    case 'conv': {
      const n = boxOf(e.node)
      const r = boxOf(e.result)
      if (n === undefined || r === undefined) return ''
      const inRow = e.row.includes(e.node)
      const row = inRow ? e.row.flatMap((id) => boxOf(id) ?? []) : null
      return conv(n, r, row, ax)
    }
    case 'back': {
      const r = boxOf(e.result)
      return r === undefined ? '' : backArrow(r)
    }
    case 'dot':
      return ''
  }
}
