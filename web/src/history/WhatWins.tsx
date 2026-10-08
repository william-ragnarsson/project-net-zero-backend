// Across a project's runs: how far each rewrite strategy's candidates got, and why the
// rejected ones were thrown out.
import type { CandidateStat, RejectStat } from '../gen/events'
import { formatInt } from '../lib/format'
import { NEON } from '../timeline/theme'
import { REJECT_REASON } from './labels'

// each stage is a subset of the one before it, so the bars nest
const STAGES = [
  { key: 'proposed', text: 'written', color: '#1f1f1f' },
  { key: 'eligible', text: 'passed the checks', color: '#353535' },
  { key: 'significant', text: 'measurably better', color: '#00cc6a66' },
  { key: 'accepted', text: 'kept', color: NEON },
] as const

export function StrategyFunnel({ candidates }: { candidates: readonly CandidateStat[] }) {
  const max = Math.max(1, ...candidates.map((c) => c.proposed))
  return (
    <div>
      <ul className="space-y-5">
        {candidates.map((c) => (
          <li key={c.candidate_id}>
            <div className="flex items-baseline justify-between gap-4 text-sm">
              <span className="min-w-0 truncate">
                <span className="text-white">Rewrite {c.candidate_id}</span>
                <span className="text-muted"> · {c.hint}</span>
              </span>
              <span className="shrink-0 font-mono text-xs tabular-nums text-gray-300">
                <span className="text-neon">{formatInt(c.accepted)}</span> kept of {formatInt(c.proposed)}
              </span>
            </div>
            <div className="relative mt-2 h-3">
              {STAGES.map((stage) => (
                <span
                  key={stage.key}
                  title={`${formatInt(c[stage.key])} ${stage.text}`}
                  className="absolute inset-y-0 left-0"
                  style={{ width: `${(c[stage.key] / max) * 100}%`, backgroundColor: stage.color }}
                />
              ))}
            </div>
          </li>
        ))}
      </ul>
      <div className="mt-5 flex flex-wrap gap-x-5 gap-y-2 font-mono text-[11px] text-muted">
        {STAGES.map((stage) => (
          <span key={stage.key} className="flex items-center gap-2">
            <span className="block h-2 w-3" style={{ backgroundColor: stage.color, outline: '1px solid #2a2a2a' }} />
            {stage.text}
          </span>
        ))}
      </div>
    </div>
  )
}

export function RejectReasons({ rejections }: { rejections: readonly RejectStat[] }) {
  if (rejections.length === 0) return <p className="text-sm text-muted">No rewrite was rejected.</p>
  const max = Math.max(1, ...rejections.map((r) => r.count))
  return (
    <ul className="space-y-3">
      {rejections.map((r) => (
        <li key={r.reason} className="grid grid-cols-[10rem_minmax(0,1fr)_2.5rem] items-center gap-3 text-sm">
          <span className="truncate text-gray-300">{REJECT_REASON[r.reason]}</span>
          <span className="block h-2 bg-[#161616]">
            <span className="block h-full bg-fail/60" style={{ width: `${(r.count / max) * 100}%` }} />
          </span>
          <span className="text-right font-mono text-xs tabular-nums text-muted">{formatInt(r.count)}</span>
        </li>
      ))}
    </ul>
  )
}
