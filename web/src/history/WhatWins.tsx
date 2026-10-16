// Across a project's runs: how far each rewrite strategy's candidates got, and why the
// rejected ones were thrown out.
import type { CandidateStat, RejectStat } from '../gen/events'
import { formatInt } from '../lib/format'
import { REJECT_REASON } from './labels'

// each stage is a subset of the one before it, so the bars nest
const STAGES = [
  { key: 'proposed', text: 'Written', fill: 'bg-hover' },
  { key: 'eligible', text: 'Passed the checks', fill: 'bg-control' },
  { key: 'significant', text: 'Measurably better', fill: 'bg-accent-soft' },
  { key: 'accepted', text: 'Kept', fill: 'bg-accent' },
] as const

export function StrategyFunnel({ candidates }: { candidates: readonly CandidateStat[] }) {
  if (candidates.length === 0) return <p className="text-sm text-ink-3">No rewrites written yet.</p>
  const max = Math.max(1, ...candidates.map((c) => c.proposed))
  return (
    <div>
      <ul className="space-y-5">
        {candidates.map((c) => (
          <li key={c.candidate_id}>
            <div className="flex items-baseline justify-between gap-4 text-base">
              <span className="min-w-0 truncate">
                <span className="font-medium text-ink">Rewrite {c.candidate_id}</span>
                <span className="text-ink-3"> · {c.hint}</span>
              </span>
              <span className="shrink-0 text-sm tabular-nums text-ink-3">
                <span className="font-medium text-accent">{formatInt(c.accepted)}</span> kept of {formatInt(c.proposed)}
              </span>
            </div>
            <div className="relative mt-2 h-2.5 overflow-hidden rounded-full">
              {STAGES.map((stage) => (
                <span
                  key={stage.key}
                  title={`${formatInt(c[stage.key])} ${stage.text.toLowerCase()}`}
                  className={`absolute inset-y-0 left-0 rounded-full ${stage.fill}`}
                  style={{ width: `${(c[stage.key] / max) * 100}%` }}
                />
              ))}
            </div>
          </li>
        ))}
      </ul>
      <div className="mt-5 flex flex-wrap gap-x-5 gap-y-2 text-xs text-ink-3">
        {STAGES.map((stage) => (
          <span key={stage.key} className="flex items-center gap-2">
            <span aria-hidden="true" className={`block h-2 w-3 rounded-sm border border-line ${stage.fill}`} />
            {stage.text}
          </span>
        ))}
      </div>
    </div>
  )
}

export function RejectReasons({ rejections }: { rejections: readonly RejectStat[] }) {
  if (rejections.length === 0) return <p className="text-sm text-ink-3">No rewrite was rejected.</p>
  const max = Math.max(1, ...rejections.map((r) => r.count))
  return (
    <ul className="space-y-3">
      {rejections.map((r) => (
        <li key={r.reason} className="grid grid-cols-[10rem_minmax(0,1fr)_2.5rem] items-center gap-3 text-base">
          <span className="truncate text-ink-2">{REJECT_REASON[r.reason]}</span>
          <span className="block h-2 overflow-hidden rounded-full bg-hover">
            <span className="block h-full rounded-full bg-fail-soft" style={{ width: `${(r.count / max) * 100}%` }} />
          </span>
          <span className="text-right text-sm tabular-nums text-ink-3">{formatInt(r.count)}</span>
        </li>
      ))}
    </ul>
  )
}
