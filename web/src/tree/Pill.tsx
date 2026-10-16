// A node's collapsed face: a name and at most one short status. Numbers that need a unit live in
// the tooltip and the cards.
import * as Tooltip from '@radix-ui/react-tooltip'
import { useState, type ReactNode } from 'react'
import { plainReason, plainText } from '../components/Co2'
import { repoOf } from '../lib/runs'
import type { RunModel } from '../state/types'
import type { CandNode, FnNode, NodeId, PreNode, ResultNode, Tone } from './derive'
import { Glyph } from './Glyph'
import { candAria, candFace, candTip, fnAria, fnPillTail, fnTip, preText, resultAria, resultFace, resultTip } from './text'

/** The node box's border and fill while it is a pill. */
const BOX = 'border-control bg-surface hover:border-control-strong'
const BOX_WIN = 'border-accent-tint bg-accent-tint text-accent'

export interface Face {
  content: ReactNode
  /** border and fill of the node box while closed */
  box: string
  aria: string
  tip: string | null
}

const PENDING = new Set(['written', 'checked', 'queued'])

/** The word colour of a status by tone; busy steps are a softer ink, never animated. */
const wordClass = (tone: Tone, busy: boolean): string =>
  busy ? 'text-ink-2' : tone === 'fail' ? 'text-fail' : tone === 'caution' ? 'text-caution' : tone === 'muted' ? 'text-ink-3' : ''

export const repoName = (m: RunModel): string => (m.meta === null ? 'Run' : repoOf(m.meta).name)

export function preFace(node: PreNode, m: RunModel, pinned: boolean): Face {
  const s = preText(node, m)
  const name = repoName(m)
  return {
    content: (
      <>
        <span>{name}</span>
        <Glyph glyph={s.glyph} tone={s.tone} />
        {/* a step wider than the other faces' gaps: name and status are two things */}
        <span className={`ml-0.5 ${s.tone === 'fail' ? 'text-fail' : 'text-ink-2'}`}>{s.text}</span>
      </>
    ),
    box: BOX,
    aria: `${name}, ${s.text}${pinned ? '' : '. Show details'}`,
    tip: null,
  }
}

export function fnFace(node: FnNode, m: RunModel, pinned: boolean, frontier: boolean): Face | null {
  const f = m.functions[node.functionId]
  if (f === undefined) return null
  const tail = fnPillTail(node, f)
  return {
    content: (
      <>
        <span>{node.qualname}</span>
        {tail?.kind === 'sub' && <span className="text-ink-3">{tail.text}</span>}
      </>
    ),
    box: frontier ? 'border-edge-strong bg-surface hover:border-control-strong' : BOX,
    aria: fnAria(node, f, pinned),
    tip: fnTip(node, f),
  }
}

export function candFaceOf(node: CandNode, m: RunModel, pinned: boolean): Face | null {
  const f = m.functions[node.functionId]
  const c = f?.candidates[node.letter]
  if (f === undefined || c === undefined) return null
  const face = candFace(node)
  const pending = PENDING.has(node.phase)
  const quiet = pending || node.dimmed || node.tone === 'muted' || node.tone === 'fail'
  const deltaClass = node.tone === 'caution' ? 'text-caution' : quiet ? 'text-ink-3' : ''
  return {
    content: (
      <>
        <span className={quiet ? 'text-ink-3' : ''}>{node.letter}</span>
        {face.sub !== null && <span className="text-ink-2">{face.sub}</span>}
        {face.delta !== null && <span className={`tabular-nums ${deltaClass}`}>{face.delta}</span>}
        <Glyph glyph={face.glyph} tone={node.tone} />
      </>
    ),
    box: node.tone === 'win' ? BOX_WIN : pending ? `${BOX} border-dashed` : BOX,
    aria: candAria(node, c, pinned),
    tip: candTip(node, c),
  }
}

export function resultFaceOf(node: ResultNode, m: RunModel, live: boolean, pinned: boolean): Face | null {
  const f = m.functions[node.functionId]
  if (f === undefined) return null
  const r = resultFace(node, f, live)
  const merged = node.result === 'merged'
  return {
    content: (
      <>
        <Glyph glyph={r.glyph} tone={merged ? 'win' : r.tone} />
        <span className={merged ? '' : wordClass(r.tone, r.busy)}>{r.text}</span>
        {r.delta !== null && <span className="tabular-nums">{r.delta}</span>}
      </>
    ),
    box: merged ? BOX_WIN : BOX,
    aria: resultAria(node, pinned),
    tip: resultTip(node, f, m),
  }
}

/** Escape presses a tooltip already used, so the tree's own Escape leaves the pin alone. */
export const escapeUsed = new WeakSet<Event>()

/** The tooltip every face and tail shares. */
export function Tip({ text, children }: { text: string | null; children: ReactNode }) {
  const [open, setOpen] = useState(false)
  if (text === null || text === '') return children
  return (
    <Tooltip.Root open={open} onOpenChange={setOpen}>
      <Tooltip.Trigger asChild>{children}</Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content
          side="right"
          sideOffset={8}
          collisionPadding={8}
          onEscapeKeyDown={(e) => escapeUsed.add(e)}
          className="z-30 max-w-[280px] rounded-lg border border-line bg-surface px-2.5 py-1.5 text-xs text-ink-2 shadow-float"
        >
          {plainReason(text)}
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  )
}

/**
 * The face button. It fills the top strip of the node box, which draws the border, so the box
 * can grow into a card while the face fades out.
 */
export function Pill({
  id,
  face,
  open,
  onSelect,
}: {
  id: NodeId
  face: Face
  open: boolean
  onSelect: (id: NodeId) => void
}) {
  return (
    <Tip text={open ? null : face.tip}>
      <button
        type="button"
        data-face
        aria-label={plainText(face.aria)}
        aria-expanded={open}
        inert={open}
        onClick={() => onSelect(id)}
        className="absolute inset-x-0 top-0 flex h-[26px] items-center justify-center whitespace-nowrap rounded-full px-3 text-sm focus-visible:outline-offset-[-2px]"
      >
        <span data-face-inner className="inline-flex shrink-0 items-center gap-1.5">
          {face.content}
        </span>
      </button>
    </Tip>
  )
}
