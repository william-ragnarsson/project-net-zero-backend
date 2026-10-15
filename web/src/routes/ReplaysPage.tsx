// /replays: the recorded runs bundled with this build.
import { Link } from 'react-router'
import { PageHeader } from '../components/PageHeader'
import { formatClock } from '../lib/format'
import { useTitle } from '../lib/useTitle'
import { useReplayIndex } from '../sources/queries'
import { replayHref } from '../sources/replay'
import { Button } from '../ui/Button'
import { Delayed } from '../ui/Delayed'
import { EmptyState } from '../ui/EmptyState'
import { InlineNotice } from '../ui/InlineNotice'
import { Page } from '../ui/Page'

export function ReplaysPage() {
  useTitle('Replays')
  const index = useReplayIndex()
  return (
    <Page>
      <PageHeader title="Replays" meta="Recorded runs that play back event by event" />
      {index.isPending ? (
        <Delayed>
          <p className="text-base text-ink-3">Loading</p>
        </Delayed>
      ) : index.isError ? (
        <InlineNotice
          tone="fail"
          action={
            <Button size="sm" onClick={() => void index.refetch()}>
              Retry
            </Button>
          }
        >
          Can’t load replays
        </InlineNotice>
      ) : index.data.length === 0 ? (
        <EmptyState title="No replays in this build" />
      ) : (
        <ul className="divide-y divide-line">
          {index.data.map((r) => (
            <li key={r.id}>
              <Link
                to={replayHref(r.id)}
                className="-mx-3 flex items-center justify-between gap-4 rounded-lg px-3 py-3 hover:bg-hover"
              >
                <div className="min-w-0">
                  <div className="text-base text-ink">{r.title}</div>
                  {r.description !== '' && <div className="line-clamp-1 text-sm text-ink-2">{r.description}</div>}
                </div>
                <div className="shrink-0 text-sm tabular-nums text-ink-3">
                  {r.functions} functions · {formatClock(r.duration_ms)}
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Page>
  )
}
