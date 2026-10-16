import '../tree/tree.css'
import { useMemo } from 'react'
import { useSearchParams } from 'react-router'
import { plainReason } from '../components/Co2'
import type { FunctionOutcome } from '../gen/events'
import { formatPct } from '../lib/format'
import { selectFunctions } from '../state/selectors'
import type { FunctionModel, RunModel } from '../state/types'
import { DiffLines } from '../tree/DiffLines'
import { fullDiffRows } from '../tree/diff'
import { EmptyState } from '../ui/EmptyState'

const OUTCOME_LABEL: Record<FunctionOutcome, string> = {
  accepted: 'Accepted',
  no_significant_win: 'No significant win',
  all_rejected: 'All candidates rejected',
  reverted: 'Reverted',
  skipped_untestable: 'Skipped, no usable tests',
  skipped_capture: 'Skipped, calls not captured',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

type Finished = FunctionModel & { outcome: NonNullable<FunctionModel['outcome']> }

const finished = (f: FunctionModel): f is Finished => f.outcome !== null

/** Accepted functions first, each group in the order they finished. */
function rowsOf(m: RunModel): Finished[] {
  const done = selectFunctions(m)
    .filter(finished)
    .sort((a, b) => a.outcome.bornSeq - b.outcome.bornSeq)
  return [...done.filter((f) => f.outcome.outcome === 'accepted'), ...done.filter((f) => f.outcome.outcome !== 'accepted')]
}

/** One row per finished function: its result, why, and the diff when it was kept. */
export function ChangesTab({ model: m }: { model: RunModel }) {
  const [params] = useSearchParams()
  const rows = useMemo(() => rowsOf(m), [m])
  if (rows.length === 0) return <EmptyState title="No finished functions yet" />

  const fn = params.get('fn')
  const pinned = fn !== null && rows.some((f) => f.functionId === fn)
  const firstAccepted = rows.find((f) => f.outcome.outcome === 'accepted')?.functionId
  const diffs = m.steps.artifacts?.result?.function_diffs ?? []

  return (
    <div className="divide-y divide-line">
      {rows.map((f) => {
        const o = f.outcome
        const accepted = o.outcome === 'accepted'
        const module =
          m.triage.find((t) => t.function_id === f.functionId)?.module ?? f.started?.info.module ?? null
        const diffUrl = diffs.find((d) => d.function_id === f.functionId)?.url
        return (
          // keyed with `pinned` so a new ?fn= reopens the right row even after the user toggled one
          <details key={`${f.functionId}:${pinned ? fn : ''}`} open={pinned ? f.functionId === fn : f.functionId === firstAccepted}>
            <summary className="-mx-3 flex cursor-pointer list-none items-center gap-3 rounded-lg px-3 py-3 transition-colors duration-150 hover:bg-hover [&::-webkit-details-marker]:hidden">
              <span className="min-w-0 truncate text-ink">
                {f.qualname}
                {module !== null && <span className="ml-2 text-sm text-ink-3">{module}</span>}
              </span>
              <span className={`ml-auto shrink-0 tabular-nums ${accepted ? 'text-accent' : 'text-sm text-ink-3'}`}>
                {accepted ? formatPct(o.delta_pct) : OUTCOME_LABEL[o.outcome]}
              </span>
            </summary>
            <div className="space-y-3 pb-4">
              <p className="text-sm text-ink-2">{plainReason(o.reason)}</p>
              {accepted && (
                <div data-codebox className="overflow-auto rounded-lg border border-control bg-surface font-mono text-code text-ink">
                  <DiffLines rows={fullDiffRows(o.diff ?? f.merge?.result?.diff)} />
                </div>
              )}
              {diffUrl !== undefined && (
                <a href={diffUrl} download className="inline-block text-sm text-ink-2 hover:text-ink">
                  Download diff
                </a>
              )}
            </div>
          </details>
        )
      })}
    </div>
  )
}
