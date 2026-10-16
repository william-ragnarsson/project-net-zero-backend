import type { TrendPoint } from '../gen/events'
import { formatDate, formatGrams } from '../lib/format'
import { runState, TONE_FILL } from './labels'

/** A project's recent runs as small bars, oldest first: height is CO₂ saved per 1M calls. */
export function MiniTrend({ points }: { points: readonly TrendPoint[] }) {
  const max = Math.max(0, ...points.map((p) => p.g_saved_per_1m_calls))
  return (
    <div className="flex h-10 items-end gap-[3px]" aria-hidden="true">
      {points.map((p) => {
        const done = p.state === 'completed'
        const saved = done && max > 0 ? (p.g_saved_per_1m_calls / max) * 100 : 0
        const fill = done ? (saved > 0 ? 'bg-accent' : 'bg-control') : TONE_FILL[runState(p.state).tone]
        return (
          <span
            key={p.run_id}
            title={`${formatDate(p.created_ts)}: ${
              done ? `${formatGrams(p.g_saved_per_1m_calls)} saved per 1M calls` : runState(p.state).text
            }`}
            className={`w-full max-w-[10px] flex-1 rounded-sm ${fill}`}
            style={{ height: done ? `max(2px, ${saved}%)` : '3px' }}
          />
        )
      })}
    </div>
  )
}
