// A project's runs, oldest first. Bar height is the CO₂ the run's accepted rewrites save per
// 1M calls; a run that did not complete is a short stub in its state's colour.
import { useState } from 'react'
import { Link } from 'react-router'
import type { HistoryRun } from '../gen/events'
import { formatDate, formatGrams, formatPct } from '../lib/format'
import { storedReplayHref } from '../sources/history'
import { runState, shortSha, TONE_FILL } from './labels'

function describe(run: HistoryRun): string {
  const parts = [formatDate(run.created_ts), run.ref ?? shortSha(run.base_sha)].filter((p) => p !== null)
  if (run.state !== 'completed') {
    parts.push(run.error ?? runState(run.state).text)
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

const fill = (run: HistoryRun, pct: number): string =>
  run.state === 'completed' ? (pct > 0 ? 'bg-accent' : 'bg-control') : TONE_FILL[runState(run.state).tone]

export function RunsChart({ projectId, runs }: { projectId: string; runs: readonly HistoryRun[] }) {
  const chrono = [...runs].reverse()
  const [hover, setHover] = useState<string | null>(null)
  const shown = chrono.find((r) => r.run_id === hover) ?? chrono.at(-1)
  const max = Math.max(0, ...chrono.map((r) => (r.state === 'completed' ? r.g_saved_per_1m_calls : 0)))
  const dense = chrono.length > 16
  const pctOf = (run: HistoryRun) => (run.state === 'completed' && max > 0 ? (run.g_saved_per_1m_calls / max) * 100 : 0)

  return (
    <figure>
      <div className="relative">
        <div className="pointer-events-none absolute inset-x-0 top-0 border-t border-dashed border-line" />
        <span className="pointer-events-none absolute right-0 top-1 text-xs tabular-nums text-ink-3">
          {max > 0 ? formatGrams(max) : ''}
        </span>
        <div className="flex h-44 items-end gap-1 border-b border-control md:gap-2" onMouseLeave={() => setHover(null)}>
          {chrono.map((run) => {
            const pct = pctOf(run)
            const active = run.run_id === shown?.run_id
            return (
              <Link
                key={run.run_id}
                to={storedReplayHref(projectId, run.run_id)}
                aria-label={`Replay: ${describe(run)}`}
                onMouseEnter={() => setHover(run.run_id)}
                onFocus={() => setHover(run.run_id)}
                className="group flex h-full max-w-14 flex-1 flex-col justify-end rounded-t-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink-2"
              >
                <span
                  className={`block w-full rounded-t-sm transition-opacity duration-150 ${fill(run, pct)} ${
                    active ? 'opacity-100' : 'opacity-60 group-hover:opacity-100'
                  }`}
                  style={{ height: run.state === 'completed' ? `max(2px, ${pct}%)` : '4px' }}
                />
              </Link>
            )
          })}
        </div>
      </div>
      <div className="mt-2 flex gap-1 md:gap-2" aria-hidden="true">
        {chrono.map((run, i) => (
          <span key={run.run_id} className="max-w-14 flex-1 truncate text-center text-xs text-ink-3">
            {!dense || i === 0 || i === chrono.length - 1 ? formatDate(run.created_ts) : ''}
          </span>
        ))}
      </div>
      {shown !== undefined && (
        <figcaption className="mt-4 flex items-center gap-2 text-sm text-ink-2">
          <span aria-hidden="true" className={`size-2 shrink-0 rounded-full ${fill(shown, pctOf(shown))}`} />
          <span className="min-w-0 truncate">{describe(shown)}</span>
        </figcaption>
      )}
    </figure>
  )
}
