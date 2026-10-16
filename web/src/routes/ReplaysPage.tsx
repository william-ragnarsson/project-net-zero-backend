// /replays: the recorded runs bundled with this build.
import { Link } from 'react-router'
import { PageHeader } from '../components/PageHeader'
import { formatClock } from '../lib/format'
import { useTitle } from '../lib/useTitle'
import { useReplayIndex } from '../sources/queries'
import { replayHref } from '../sources/replay'
import { Badge } from '../ui/Badge'
import { Button, buttonClass } from '../ui/Button'
import { Card, CardHeader } from '../ui/Card'
import { Delayed } from '../ui/Delayed'
import { EmptyState } from '../ui/EmptyState'
import { Icon } from '../ui/Icon'
import { InlineNotice } from '../ui/InlineNotice'
import { Page } from '../ui/Page'

export function ReplaysPage() {
  useTitle('Replays')
  const index = useReplayIndex()
  return (
    <Page>
      <PageHeader title="Replays" meta="Recorded runs that play back event by event" />
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="min-w-0">
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
            <Card>
              <EmptyState title="No replays in this build" />
            </Card>
          ) : (
            <ul className="space-y-4">
              {index.data.map((r) => (
                <li key={r.id}>
                  <Card className="relative flex flex-col gap-4 p-5 transition-colors hover:border-edge-strong sm:flex-row sm:items-center">
                    <span
                      aria-hidden="true"
                      className="grid size-11 shrink-0 place-items-center rounded-lg bg-accent-tint text-accent"
                    >
                      <Icon name="play" size={20} />
                    </span>
                    <div className="min-w-0 flex-1">
                      <h2 className="text-md font-medium text-ink">
                        <Link to={replayHref(r.id)} className="after:absolute after:inset-0 after:rounded-xl after:content-['']">
                          {r.title}
                        </Link>
                      </h2>
                      {r.description !== '' && <p className="mt-0.5 text-sm text-ink-2">{r.description}</p>}
                      <div className="mt-3 flex flex-wrap gap-2">
                        <Badge>{r.functions} functions</Badge>
                        <Badge>{formatClock(r.duration_ms)}</Badge>
                      </div>
                    </div>
                    <span aria-hidden="true" className={`${buttonClass('secondary', 'md')} self-start sm:self-auto`}>
                      Watch
                      <Icon name="arrow-right" />
                    </span>
                  </Card>
                </li>
              ))}
            </ul>
          )}
        </div>
        <aside>
          <Card>
            <CardHeader title="About replays" />
            <div className="space-y-3 px-5 pb-5 text-sm text-ink-2">
              <p>
                A replay reads a recorded event log and draws it the same way as a live run. Nothing runs on the
                server.
              </p>
              <p>Pause it, step through it one event at a time, or jump to any point.</p>
              <p className="text-ink-3">The bundled replay is scripted, so its numbers are examples, not measurements.</p>
            </div>
          </Card>
        </aside>
      </div>
    </Page>
  )
}
