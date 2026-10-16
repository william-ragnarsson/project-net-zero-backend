// The parts every tree card shares. In the wide tree the node box draws the frame, so it can
// morph from a pill into the card; the narrow list frames each card itself.
import type { ReactNode } from 'react'
import { plainText } from '../components/Co2'
import type { NodeId } from './derive'

export interface CardProps {
  id: NodeId
  /** opened by the user: the full detail instead of the live view */
  pinned: boolean
  /** the card draws its own border (narrow list) */
  framed: boolean
  live: boolean
  onSelect: (id: NodeId | null) => void
}

export function CardShell({ framed, pinned, children }: { framed: boolean; pinned: boolean; children: ReactNode }) {
  return (
    <div className={framed ? `w-full rounded-lg border bg-surface ${pinned ? 'border-ink-2' : 'border-control'}` : ''}>
      {children}
    </div>
  )
}

/** The header is the card's one control: it pins the card, or lets a pinned one go. */
export function CardHeader({
  id,
  pinned,
  aria,
  win = false,
  onSelect,
  children,
}: {
  id: NodeId
  pinned: boolean
  aria: string
  win?: boolean
  onSelect: (id: NodeId | null) => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      data-card-header
      aria-label={plainText(`${aria}${pinned ? '. Hide details' : '. Show details'}`)}
      aria-expanded={pinned}
      onClick={() => onSelect(pinned ? null : id)}
      className={`flex h-[35px] w-full min-w-0 items-center gap-2 border-b border-line px-3 text-left text-sm focus-visible:outline-offset-[-2px] ${
        win ? 'bg-accent-tint text-accent' : 'text-ink'
      }`}
    >
      {children}
    </button>
  )
}

/** Code scrolls inside its card, never the page. */
export function CodeBody({ children }: { children: ReactNode }) {
  return (
    <div data-codebox className="max-h-[150px] overflow-auto font-mono text-code text-ink">
      {children}
    </div>
  )
}

export function CardFooter({ children }: { children: ReactNode }) {
  return <div className="flex h-[30px] min-w-0 items-center gap-2 border-t border-line px-3 text-xs text-ink-2">{children}</div>
}

export type Fact = readonly [ReactNode, ReactNode]

/** A plain definition list for a pinned card's facts; false rows are left out. */
export function Facts({ rows }: { rows: readonly (Fact | null | false | undefined)[] }) {
  const shown = rows.filter((r): r is Fact => r !== null && r !== false && r !== undefined)
  if (shown.length === 0) return null
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 px-3 py-2.5 text-xs">
      {shown.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-ink-2">{k}</dt>
          <dd className="min-w-0 break-words text-ink tabular-nums">{v}</dd>
        </div>
      ))}
    </dl>
  )
}
