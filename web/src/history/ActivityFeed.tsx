// The notable events of every stored run, newest first, grouped by run. Each line opens the
// stored run's replay paused right after that event. It sits in a card, under the card's header.
import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { Co2 } from '../components/Co2'
import type { ActivityItem } from '../gen/events'
import { formatAgo, formatGrams, formatPct, formatTime } from '../lib/format'
import { storedReplayHref, useActivity } from '../sources/history'
import type { BadgeTone } from '../ui/Badge'
import { Button } from '../ui/Button'
import { CardFooter } from '../ui/Card'
import { OUTCOME, splitFunctionId } from './labels'

const DOT: Readonly<Record<BadgeTone, string>> = {
  success: 'bg-accent',
  live: 'bg-accent',
  caution: 'bg-caution',
  fail: 'bg-fail',
  neutral: 'bg-edge-strong',
}

interface Line {
  tone: BadgeTone
  text: ReactNode
  detail?: string
}

function describe(item: ActivityItem): Line {
  switch (item.type) {
    case 'run.created':
      return { tone: 'neutral', text: 'Run started' }
    case 'run.selection.confirmed':
      return { tone: 'neutral', text: `Picked ${item.functions ?? 0} functions to optimize` }
    case 'function.completed': {
      const name = <span className="font-mono text-code">{splitFunctionId(item.function_id ?? '').name}</span>
      if (item.outcome === 'accepted') {
        return {
          tone: 'success',
          text: (
            <>
              {name} <span className="font-medium text-accent">{formatPct(item.delta_pct)}</span> <Co2 /> per call,
              rewrite {item.winner}
            </>
          ),
        }
      }
      const outcome = item.outcome === null ? null : OUTCOME[item.outcome]
      return {
        tone: outcome?.tone ?? 'neutral',
        text: (
          <>
            {name} <span className="text-ink-3">{outcome?.text.toLowerCase() ?? 'finished'}</span>
          </>
        ),
        ...(item.message !== null && item.message !== '' && { detail: item.message }),
      }
    }
    case 'run.completed':
      return {
        tone: (item.accepted ?? 0) > 0 ? 'success' : 'neutral',
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
      return { tone: 'fail', text: 'Run failed', ...(item.message !== null && { detail: item.message }) }
    case 'run.cancelled':
      return { tone: 'neutral', text: 'Run cancelled' }
    case 'run.interrupted':
      return { tone: 'caution', text: 'Run interrupted', ...(item.message !== null && { detail: item.message }) }
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

const Note = ({ fail = false, children }: { fail?: boolean; children: ReactNode }) => (
  <p className={`border-t border-line px-5 py-4 text-sm ${fail ? 'text-fail' : 'text-ink-3'}`}>{children}</p>
)

export function ActivityFeed({ enabled, project, now }: { enabled: boolean; project?: string; now: number }) {
  const activity = useActivity(enabled, project)
  if (activity.isPending) return <Note>Loading activity</Note>
  if (activity.isError) return <Note fail>Can’t load activity: {activity.error.message}</Note>

  const items = activity.data.pages.flatMap((page) => page.items)
  if (items.length === 0) return <Note>No runs stored yet.</Note>

  return (
    <>
      <ol className="divide-y divide-line border-t border-line">
        {groupByRun(items).map((group) => {
          const head = group[0]!
          return (
            <li key={`${head.run_id}:${head.position}`} className="px-5 py-4">
              <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                <p className="min-w-0 truncate text-sm">
                  {project === undefined && (
                    <>
                      <Link
                        to={`/history/${encodeURIComponent(head.project_id)}`}
                        className="font-medium text-ink underline-offset-2 hover:underline"
                      >
                        {head.project_name}
                      </Link>
                      <span className="text-ink-3"> · </span>
                    </>
                  )}
                  <span className="font-mono text-xs text-ink-3">{head.run_id}</span>
                </p>
                <p className="shrink-0 text-xs text-ink-3">{formatAgo(head.ts, now)}</p>
              </div>
              <ul className="mt-2">
                {group.map((item) => {
                  const line = describe(item)
                  return (
                    <li key={item.position}>
                      <Link
                        to={storedReplayHref(item.project_id, item.run_id, item.seq)}
                        title="Open the replay right after this event"
                        className="-mx-2 flex gap-3 rounded-lg px-2 py-1.5 transition-colors duration-150 hover:bg-hover"
                      >
                        <span aria-hidden="true" className={`mt-[7px] size-1.5 shrink-0 rounded-full ${DOT[line.tone]}`} />
                        <span className="min-w-0 flex-1">
                          <span className="block text-base text-ink">{line.text}</span>
                          {line.detail !== undefined && (
                            <span className="mt-0.5 block truncate text-sm text-ink-3">{line.detail}</span>
                          )}
                        </span>
                        <span className="shrink-0 pt-px text-xs tabular-nums text-ink-3">{formatTime(item.ts)}</span>
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
        <CardFooter>
          <Button
            size="sm"
            onClick={() => void activity.fetchNextPage()}
            disabled={activity.isFetchingNextPage}
          >
            {activity.isFetchingNextPage ? 'Loading' : 'Load older'}
          </Button>
        </CardFooter>
      )}
    </>
  )
}
