import { Co2, plainReason } from '../components/Co2'
import type { RunState, RunTotals } from '../gen/events'
import { formatGrams, formatPct } from '../lib/format'
import type { Terminal } from '../state/types'
import { Disclosure } from '../ui/Disclosure'
import { InlineNotice } from '../ui/InlineNotice'

/** The result in one or two sentences, from run.completed's summary only. */
export function RunSummary({ summary }: { summary: RunTotals }) {
  const accepted = summary.counts_by_outcome.accepted ?? 0
  const mean = summary.mean_reduction_pct
  if (accepted === 0 || mean === null) {
    return (
      <p className="mb-8 max-w-[640px] text-md text-ink-2">
        None of the {summary.functions_total} functions improved enough to keep.
      </p>
    )
  }
  // the mean is negative for a win; "70.8% less" avoids a double negative
  return (
    <p className="mb-8 max-w-[640px] text-md text-ink-2">
      <span className="tabular-nums text-ink">
        {accepted} of {summary.functions_total}
      </span>{' '}
      functions improved, using <span className="text-accent">{formatPct(Math.abs(mean), 1)} less</span> <Co2 />e per
      call on average. Together they save{' '}
      <span className="tabular-nums text-ink">{formatGrams(summary.g_saved_per_1m_calls)}</span> <Co2 />e per 1M calls
      of each function.
    </p>
  )
}

const STAGE: Partial<Record<RunState, string>> = {
  created: 'starting',
  cloning: 'cloning the repository',
  installing: 'setting up the environment',
  discovering: 'finding functions',
  triaging: 'ranking functions',
  awaiting_selection: 'waiting for the selection',
  optimizing: 'optimizing',
  finalizing: 'writing the patch',
}

/** One sentence on why a run stopped short, for every terminal but run.completed. */
export function EndedNotice({ terminal }: { terminal: Terminal }) {
  switch (terminal.type) {
    case 'run.failed':
      return (
        <div className="mb-6">
          <InlineNotice tone="fail">
            Failed while {STAGE[terminal.stage] ?? 'running'}: {plainReason(terminal.error.message)}
          </InlineNotice>
          {terminal.error.detail !== null && terminal.error.detail !== '' && (
            <Disclosure className="mt-3" summary="Details">
              <pre className="whitespace-pre-wrap font-mono text-code text-ink-2">{terminal.error.detail}</pre>
            </Disclosure>
          )}
        </div>
      )
    case 'run.cancelled':
      return (
        <div className="mb-6">
          <InlineNotice>Cancelled before it finished</InlineNotice>
        </div>
      )
    case 'run.interrupted':
      return (
        <div className="mb-6">
          <InlineNotice>The server stopped before this run finished</InlineNotice>
        </div>
      )
    default:
      return null
  }
}
