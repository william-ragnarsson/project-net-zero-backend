// A project's runs, oldest first. Bar height is the CO₂ the run's accepted rewrites save per
// 1M calls; a run that did not complete is a short stub in its state's color.
import { useState } from 'react'
import { Link } from 'react-router'
import type { HistoryRun } from '../gen/events'
import { formatDate, formatGrams, formatPct } from '../lib/format'
import { storedReplayHref } from '../sources/history'
import { RUN_STATE, shortSha } from './labels'

function describe(run: HistoryRun): string {
  const parts = [formatDate(run.created_ts), run.ref ?? shortSha(run.base_sha)].filter((p) => p !== null)
  if (run.state !== 'completed') {
    parts.push(run.error ?? RUN_STATE[run.state].text)
    return parts.join(' · ')
  }
  const accepted = run.counts_by_outcome.accepted ?? 0
  parts.push(`${accepted} of ${run.functions_total} functions improved`)
  if (accepted > 0) {
    parts.push(`${formatGrams(run.g_saved_per_1m_calls)} saved per 1M calls`)
    parts.push(`mean ${formatPct(run.mean_reduction_pct)} per call`)
  }
  return parts.join(' · ')
}

export function RunsChart({ runs }: { runs: readonly HistoryRun[] }) {
  const chrono = [...runs].reverse()
  const [hover, setHover] = useState<string | null>(null)
  const shown = chrono.find((r) => r.run_id === hover) ?? chrono.at(-1)
  const max = Math.max(0, ...chrono.map((r) => (r.state === 'completed' ? r.g_saved_per_1m_calls : 0)))
  const dense = chrono.length > 16

  return (
    <figure>
      <div className="relative">
        <div className="pointer-events-none absolute inset-x-0 top-0 border-t border-dashed border-[#262626]" />
        <span className="pointer-events-none absolute right-0 top-1 font-mono text-[10px] text-[#5a5a5a]">
          {max > 0 ? formatGrams(max) : ''}
        </span>
        <div className="flex h-48 items-end gap-1 border-b border-[#2a2a2a] md:gap-2" onMouseLeave={() => setHover(null)}>
          {chrono.map((run) => {
            const done = run.state === 'completed'
            const pct = done && max > 0 ? (run.g_saved_per_1m_calls / max) * 100 : 0
            const active = run.run_id === shown?.run_id
            return (
              <Link
                key={run.run_id}
                to={storedReplayHref(run.run_id)}
                aria-label={`Replay: ${describe(run)}`}
                onMouseEnter={() => setHover(run.run_id)}
                onFocus={() => setHover(run.run_id)}
                className="flex h-full max-w-14 flex-1 flex-col justify-end outline-none"
              >
                <span
                  className="block w-full transition-opacity"
                  style={{
                    height: done ? `max(2px, ${pct}%)` : '4px',
                    backgroundColor: done ? (pct > 0 ? '#00ff88' : '#3a3a3a') : RUN_STATE[run.state].color,
                    opacity: active ? 1 : 0.55,
                  }}
                />
              </Link>
            )
          })}
        </div>
      </div>
      <div className="mt-2 flex gap-1 md:gap-2" aria-hidden="true">
        {chrono.map((run, i) => (
          <span
            key={run.run_id}
            className="max-w-14 flex-1 truncate text-center font-mono text-[10px] text-[#5a5a5a]"
          >
            {!dense || i === 0 || i === chrono.length - 1 ? formatDate(run.created_ts) : ''}
          </span>
        ))}
      </div>
      {shown !== undefined && (
        <figcaption className="mt-4 font-mono text-xs text-gray-300">
          <span style={{ color: RUN_STATE[shown.state].color }}>●</span> {describe(shown)}
        </figcaption>
      )}
    </figure>
  )
}
