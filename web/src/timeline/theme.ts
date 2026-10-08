// The timeline's palette. Neon marks data and what is happening now; everything at rest is
// a step of grey, so the one path that changed the code stands out.
import type { FunctionOutcome } from '../gen/events'
import type { EdgeTone, NodeStatus } from './layout'

export const NEON = '#00ff88'
export const CAUTION = '#ffbd2e'
export const FAIL = '#ff5f57'

export interface EdgeStyle {
  stroke: string
  width: number
  opacity: number
}

export const EDGE: Readonly<Record<EdgeTone, EdgeStyle>> = {
  idle: { stroke: '#3a3a3a', width: 1.25, opacity: 1 },
  lit: { stroke: NEON, width: 1.75, opacity: 1 },
  fail: { stroke: FAIL, width: 1.25, opacity: 0.75 },
  muted: { stroke: '#2a2a2a', width: 1.25, opacity: 1 },
}

/** Paint order: the lit path goes on top where edges share a stretch. */
export const EDGE_ORDER: readonly EdgeTone[] = ['muted', 'idle', 'fail', 'lit']

export const DASH = '4 5'

export interface NodeStyle {
  fill: string
  border: string
  glyph: string
  label: string
  dashed: boolean
}

/** `lit` is the neon variant of `ok`, for the path a kept change took. */
export const NODE: Readonly<Record<NodeStatus | 'lit', NodeStyle>> = {
  pending: { fill: '#0a0a0a', border: '#333333', glyph: '#4a4a4a', label: '#5a5a5a', dashed: true },
  active: { fill: '#0f1712', border: NEON, glyph: NEON, label: '#e5e5e5', dashed: false },
  ok: { fill: '#101010', border: '#3a3a3a', glyph: '#d4d4d4', label: '#8a8a8a', dashed: false },
  lit: { fill: '#0f1712', border: NEON, glyph: NEON, label: '#d4d4d4', dashed: false },
  fail: { fill: '#170d0c', border: '#ff5f57b3', glyph: FAIL, label: '#d98b87', dashed: false },
  caution: { fill: '#17130a', border: '#ffbd2eb3', glyph: CAUTION, label: '#c9a85a', dashed: false },
  muted: { fill: '#0c0c0c', border: '#242424', glyph: '#4f4f4f', label: '#4f4f4f', dashed: false },
}

export const LABEL_TONE: Readonly<Record<EdgeTone, string>> = {
  idle: '#6b6b6b',
  lit: NEON,
  fail: FAIL,
  muted: '#4f4f4f',
}

/** Half the size of a node's disc, in world units. */
export const NODE_R = 11

/** How a function's outcome reads, here and in the history pages. */
export const OUTCOME: Readonly<Record<FunctionOutcome, { text: string; color: string }>> = {
  accepted: { text: 'accepted', color: NEON },
  reverted: { text: 'reverted', color: FAIL },
  all_rejected: { text: 'all rejected', color: FAIL },
  no_significant_win: { text: 'no significant win', color: '#8a8a8a' },
  skipped_untestable: { text: 'skipped', color: '#6b6b6b' },
  skipped_capture: { text: 'skipped', color: '#6b6b6b' },
  failed: { text: 'failed', color: FAIL },
  cancelled: { text: 'cancelled', color: '#6b6b6b' },
}
