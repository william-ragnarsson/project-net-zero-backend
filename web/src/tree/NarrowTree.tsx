// The tree below a 640px container: one indented list instead of the drawn tree. The live function
// lists its candidates and their live cards under its row, and a tap on a row opens its card in place.
import { useEffect, useLayoutEffect, useRef, type ReactNode } from 'react'
import { CandidateCard } from './CandidateCard'
import type { CardProps } from './Card'
import { deriveTree, functionOfId, nodeIds, type CandNode, type FnNode, type NodeId } from './derive'
import { FunctionCard } from './FunctionCard'
import { Glyph } from './Glyph'
import { candFaceOf, escapeUsed, repoName, resultFaceOf } from './Pill'
import { PreCard } from './PreCard'
import { ResultCard } from './ResultCard'
import { FollowChip, type TreeProps } from './RunTree'
import { TailLabel } from './TailLabel'
import { fnPillTail, preText } from './text'
import { followInset, useFollow } from './useFollow'
import { useFrontierHold } from './useFrontierHold'
import { useReducedMotion } from './useReducedMotion'
import './tree.css'

interface RowProps {
  id: NodeId
  open: boolean
  onToggle: (id: NodeId) => void
  /** the space sticky bars take at the top, so a row scrolled up to stays in sight */
  inset: number
  children: ReactNode
}

function Row({ id, open, onToggle, inset, children }: RowProps) {
  return (
    <button
      type="button"
      data-row={id}
      aria-expanded={open}
      onClick={() => onToggle(id)}
      style={{ scrollMarginTop: inset }}
      className="flex min-h-10 w-full items-center justify-between gap-3 text-left text-sm"
    >
      {children}
    </button>
  )
}

export function NarrowTree({ model, epoch, speed, selectedId, onSelect, session, topInset = 16 }: TreeProps) {
  const tree = deriveTree(model)
  const reduced = useReducedMotion()
  const hold = useFrontierHold(tree, model, epoch, speed, reduced, session)
  const expandedId = tree.ended ? null : hold.expandedId
  const pinned = selectedId !== null && nodeIds(tree).has(selectedId) ? selectedId : null
  const live = !tree.ended
  const follow = useFollow()
  const inset = followInset(topInset) + 8
  const listRef = useRef<HTMLOListElement>(null)
  const focus = useRef<{ el: Element; id: NodeId | null } | null>(null)

  const toggle = (id: NodeId) => onSelect(pinned === id ? null : id)
  const cardProps = (id: NodeId): CardProps => ({ id, pinned: pinned === id, framed: true, live, onSelect })
  const rowOf = (id: NodeId | null): HTMLElement | null =>
    id === null
      ? null
      : ([...(listRef.current?.querySelectorAll<HTMLElement>('[data-row]') ?? [])].find((el) => el.dataset.row === id) ?? null)

  const reveal = (id: NodeId | null) => {
    const el = rowOf(id)
    if (el !== null && typeof el.scrollIntoView === 'function') {
      el.scrollIntoView({ block: 'nearest', behavior: reduced ? 'auto' : 'smooth' })
    }
  }

  // follow: the live function's row comes into view when the live function changes
  const revealRef = useRef(reveal)
  revealRef.current = reveal
  useEffect(() => {
    if (expandedId !== null && follow.ref.current) revealRef.current(expandedId)
  }, [expandedId, follow.ref])

  // a row or card that held focus and went away (a seek back) hands focus to the nearest row
  useLayoutEffect(() => {
    const prev = focus.current
    const active = document.activeElement
    if (prev !== null && !prev.el.isConnected && (active === null || active === document.body)) {
      const fid = prev.id === null ? null : functionOfId(prev.id)
      const to = rowOf(prev.id) ?? rowOf(fid === null ? null : `f/${fid}`) ?? rowOf('pre')
      to?.focus({ preventScroll: true })
    }
    const now = document.activeElement
    focus.current =
      now !== null && listRef.current?.contains(now) === true
        ? { el: now, id: now.closest<HTMLElement>('[data-node]')?.dataset.node ?? null }
        : null
  })

  // Esc lets the pin go
  const sel = useRef({ pinned, onSelect })
  sel.current = { pinned, onSelect }
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented || escapeUsed.has(e)) return
      if (sel.current.pinned !== null) sel.current.onSelect(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const card = (id: NodeId, body: ReactNode) => (
    <div data-node={id} className="pb-3">
      {body}
    </div>
  )

  const candRow = (c: CandNode) => {
    const face = candFaceOf(c, model, pinned === c.id)
    if (face === null) return null
    return (
      <li key={c.id}>
        <Row id={c.id} open={pinned === c.id} onToggle={toggle} inset={inset}>
          <span className="flex items-center gap-1.5">{face.content}</span>
        </Row>
      </li>
    )
  }

  const fnItem = (f: FnNode) => {
    const fm = model.functions[f.functionId]
    if (fm === undefined) return null
    const result = f.result === null ? null : resultFaceOf(f.result, model, live, false)
    const tail = result === null ? fnPillTail(f, fm) : null
    const frontier = f.id === expandedId
    const inner = pinned !== null && pinned !== f.id && functionOfId(pinned) === f.functionId
    const pinnedCand = inner && !frontier ? f.candidates.find((c) => c.id === pinned) : undefined
    const pinnedResult = inner && f.result?.id === pinned ? f.result : null
    return (
      <li key={f.id} data-node={f.id}>
        <Row id={f.id} open={pinned === f.id} onToggle={toggle} inset={inset}>
          <span className="min-w-0 truncate">{f.qualname}</span>
          {result !== null && <span className="flex shrink-0 items-center gap-1.5">{result.content}</span>}
          {tail?.kind === 'sub' && <span className="shrink-0 text-ink-3">{tail.text}</span>}
        </Row>
        {pinned === f.id && card(f.id, <FunctionCard node={f} model={model} {...cardProps(f.id)} />)}
        {frontier && f.candidates.length > 0 && (
          <>
            <ol className="ml-1 border-l border-edge pl-3">{f.candidates.map(candRow)}</ol>
            <div className="flex flex-col gap-3 pb-3 pt-2">
              {f.candidates.map((c) => (
                <div key={c.id} data-node={c.id}>
                  <CandidateCard node={c} model={model} {...cardProps(c.id)} />
                </div>
              ))}
            </div>
          </>
        )}
        {pinnedCand !== undefined && card(pinnedCand.id, <CandidateCard node={pinnedCand} model={model} {...cardProps(pinnedCand.id)} />)}
        {pinnedResult !== null && card(pinnedResult.id, <ResultCard node={pinnedResult} model={model} {...cardProps(pinnedResult.id)} />)}
      </li>
    )
  }

  const pre = preText(tree.pre, model)
  return (
    <>
      <ol ref={listRef} className="border-l border-edge pl-4">
        <li data-node="pre">
          <Row id="pre" open={pinned === 'pre'} onToggle={toggle} inset={inset}>
            <span className="min-w-0 truncate">{repoName(model)}</span>
            <span className={`flex shrink-0 items-center gap-1.5 ${pre.tone === 'fail' ? 'text-fail' : 'text-ink-2'}`}>
              <Glyph glyph={pre.glyph} tone={pre.tone} />
              {pre.text}
            </span>
          </Row>
          {pinned === 'pre' && card('pre', <PreCard node={tree.pre} model={model} {...cardProps('pre')} />)}
        </li>
        {tree.functions.map(fnItem)}
        {tree.tail !== null && (
          <li data-node="tail" className="flex min-h-10 items-center">
            <TailLabel tail={tree.tail} model={model} />
          </li>
        )}
      </ol>
      {!follow.following && !tree.ended && (
        <FollowChip
          onClick={() => {
            follow.resume()
            reveal(expandedId ?? tree.functions.at(-1)?.id ?? null)
          }}
        />
      )}
    </>
  )
}
