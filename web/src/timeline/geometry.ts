// SVG paths for the layout's edges and loops, in world units.
import type { LayoutEdge, Point } from './layout'

/** Corner radius of an elbow; small, like the brand's other radii. */
const RADIUS = 12

const r1 = (n: number) => Math.round(n * 10) / 10

function elbow(from: Point, to: Point, bendX: number): string {
  const dy = to.y - from.y
  const r = Math.min(RADIUS, Math.abs(dy) / 2, Math.abs(bendX - from.x), Math.abs(to.x - bendX))
  const sy = Math.sign(dy)
  return [
    `M${r1(from.x)} ${r1(from.y)}`,
    `H${r1(bendX - r)}`,
    `Q${r1(bendX)} ${r1(from.y)} ${r1(bendX)} ${r1(from.y + sy * r)}`,
    `V${r1(to.y - sy * r)}`,
    `Q${r1(bendX)} ${r1(to.y)} ${r1(bendX + r)} ${r1(to.y)}`,
    `H${r1(to.x)}`,
  ].join(' ')
}

/** Leaves and arrives level, so it reads as one line bending back into the trunk. */
function merge(from: Point, to: Point): string {
  const mid = (from.x + to.x) / 2
  return `M${r1(from.x)} ${r1(from.y)} C${r1(mid)} ${r1(from.y)} ${r1(mid)} ${r1(to.y)} ${r1(to.x)} ${r1(to.y)}`
}

export function edgePath(edge: Pick<LayoutEdge, 'from' | 'to' | 'shape' | 'bendX'>): string {
  const { from, to } = edge
  if (edge.shape === 'elbow' && from.y !== to.y) return elbow(from, to, edge.bendX)
  if (edge.shape === 'merge') return merge(from, to)
  return `M${r1(from.x)} ${r1(from.y)} L${r1(to.x)} ${r1(to.y)}`
}

/** A loop over a node of radius `r`: up from its left shoulder and back down on its right. */
export function loopPath(center: Point, r: number): string {
  const { x, y } = center
  const dx = r * 0.7
  const top = y - r - 18
  return `M${r1(x - dx)} ${r1(y - r * 0.7)} C${r1(x - r - 8)} ${r1(top)} ${r1(x + r + 8)} ${r1(top)} ${r1(x + dx)} ${r1(y - r * 0.7)}`
}
