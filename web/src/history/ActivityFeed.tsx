// The notable events of every stored run, newest first, grouped by run. Each line opens the
// stored run's replay paused right after that event.
import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { Co2 } from '../components/Co2'
import type { ActivityItem } from '../gen/events'
import { formatAgo, formatGrams, formatPct, formatTime } from '../lib/format'
import { storedReplayHref, useActivity } from '../sources/history'
import { CAUTION, FAIL, NEON, OUTCOME } from '../timeline/theme'
import { splitFunctionId } from './labels'

const DIM = '#6b6b6b'

interface Line {
  color: string
  text: ReactNode
  detail?: string
}

function describe(item: ActivityItem): Line {
  switch (item.type) {
    case 'run.created':
      return { color: DIM, text: 'Run started' }
    case 'run.selection.confirmed':
      return { color: DIM, text: `Picked ${item.functions ?? 0} functions to optimize` }
    case 'function.completed': {
      const name = <span className="font-mono text-[0.92em]">{splitFunctionId(item.function_id ?? '').name}</span>
      if (item.outcome === 'accepted') {
        return {
          color: NEON,
          text: (
            <>
              {name} <span className="text-neon">{formatPct(item.delta_pct)}</span> <Co2 /> per call, rewrite{' '}
              {item.winner}
            </>
          ),
        }
      }
      const outcome = item.outcome === null ? null : OUTCOME[item.outcome]
      return {
        color: outcome?.color ?? DIM,
        text: (
          <>
            {name} <span className="text-muted">{outcome?.text ?? 'finished'}</span>
          </>
        ),
        ...(item.message !== null && item.message !== '' && { detail: item.message }),
      }
    }
    case 'run.completed':
      return {
        color: (item.accepted ?? 0) > 0 ? NEON : DIM,
        text: (
          <>
            Run completed: {item.accepted ?? 0} of {item.functions ?? 0} functions improved
            {item.g_saved_per_1m_calls !== null && item.g_saved_per_1m_calls > 0 && (
              <>, {formatGrams(item.g_saved_per_1m_calls)} saved per 1M calls</>
            )}
          </>
        ),
      }
    case 'run.failed':
      return { color: FAIL, text: 'Run failed', ...(item.message !== null && { detail: item.message }) }
    case 'run.cancelled':
      return { color: DIM, text: 'Run cancelled' }
    case 'run.interrupted':
      return { color: CAUTION, text: 'Run interrupted', ...(item.message !== null && { detail: item.message }) }
  }
}

/** Consecutive items of one run; a run can span pages, so this runs over all loaded pages. */
function groupByRun(items: readonly ActivityItem[]): ActivityItem[][] {
  const groups: ActivityItem[][] = []
  for (const item of items) {
    const last = groups.at(-1)
    if (last?.[0]?.run_id === item.run_id) last.push(item)
    else groups.push([item])
  }
  return groups
}

export function ActivityFeed({ enabled, project, now }: { enabled: boolean; project?: string; now: number }) {
  const activity = useActivity(enabled, project)
  if (activity.isPending) return <p className="label">Loading activity</p>
  if (activity.isError) return <p className="text-sm text-fail">Could not load activity: {activity.error.message}</p>

  const items = activity.data.pages.flatMap((page) => page.items)
  if (items.length === 0) return <p className="text-sm text-muted">No runs stored yet.</p>

  return (
    <div>
      <ol className="space-y-8">
        {groupByRun(items).map((group) => {
          const head = group[0]!
          return (
            <li key={`${head.run_id}:${head.position}`}>
              <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-dark-border pb-2">
                <p className="min-w-0 truncate text-sm">
                  {project === undefined && (
                    <>
                      <Link
                        to={`/history/${encodeURIComponent(head.project_id)}`}
                        className="text-white transition-colors hover:text-neon"
                      >
                        {head.project_name}
                      </Link>
                      <span className="text-muted"> · </span>
                    </>
                  )}
                  <span className="font-mono text-xs text-muted">{head.run_id}</span>
                </p>
                <p className="shrink-0 font-mono text-xs text-muted">{formatAgo(head.ts, now)}</p>
              </div>
              <ul>
                {group.map((item) => {
                  const line = describe(item)
                  return (
                    <li key={item.position}>
                      <Link
                        to={storedReplayHref(item.run_id, item.seq)}
                        title="Open the replay right after this event"
                        className="group -mx-3 flex gap-3 rounded-sm px-3 py-2 transition-colors hover:bg-dark-card"
                      >
                        <span
                          aria-hidden="true"
                          className="mt-[0.45rem] size-1.5 shrink-0 rounded-full"
                          style={{ backgroundColor: line.color }}
                        />
                        <span className="min-w-0 flex-1">
                          <span className="block text-sm text-gray-300 group-hover:text-white">{line.text}</span>
                          {line.detail !== undefined && (
                            <span className="mt-0.5 block truncate text-xs text-muted">{line.detail}</span>
                          )}
                        </span>
                        <span className="shrink-0 pt-0.5 font-mono text-[11px] text-[#5a5a5a]">
                          {formatTime(item.ts)}
                        </span>
                      </Link>
                    </li>
                  )
                })}
              </ul>
            </li>
          )
        })}
      </ol>
      {activity.hasNextPage && (
        <button
          type="button"
          onClick={() => void activity.fetchNextPage()}
          disabled={activity.isFetchingNextPage}
          className="mt-8 border border-dark-border px-4 py-2 font-mono text-xs text-gray-300 transition-colors hover:border-neon/40 hover:text-neon disabled:opacity-50"
        >
          {activity.isFetchingNextPage ? 'Loading' : 'Load older'}
        </button>
      )}
    </div>
  )
}
