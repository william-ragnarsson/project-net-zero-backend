import type { TrendPoint } from '../gen/events'
import { formatDate, formatGrams } from '../lib/format'
import { RUN_STATE } from './labels'

/** A project's recent runs as small bars, oldest first: height is CO₂ saved per 1M calls. */
export function MiniTrend({ points }: { points: readonly TrendPoint[] }) {
  const max = Math.max(0, ...points.map((p) => p.g_saved_per_1m_calls))
  return (
    <div className="flex h-10 items-end gap-[3px]" aria-hidden="true">
      {points.map((p) => {
        const saved = p.state === 'completed' && max > 0 ? (p.g_saved_per_1m_calls / max) * 100 : 0
        const color = p.state === 'completed' ? (saved > 0 ? 'bg-neon/70' : 'bg-[#3a3a3a]') : undefined
        return (
          <span
            key={p.run_id}
            title={`${formatDate(p.created_ts)}: ${
              p.state === 'completed'
                ? `${formatGrams(p.g_saved_per_1m_calls)} saved per 1M calls`
                : RUN_STATE[p.state].text
            }`}
            className={`w-full max-w-[10px] flex-1 ${color ?? ''}`}
            style={{
              height: p.state === 'completed' ? `max(2px, ${saved}%)` : '3px',
              ...(color === undefined && { backgroundColor: RUN_STATE[p.state].color }),
            }}
          />
        )
      })}
    </div>
  )
}
