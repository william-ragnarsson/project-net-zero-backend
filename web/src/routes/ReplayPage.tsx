// /replays/:replayId?speed=&at=&node= plays one recorded run from the bundled index.
import { Link, useParams } from 'react-router'
import { PageHeader } from '../components/PageHeader'
import { useTitle } from '../lib/useTitle'
import { ReplayPlayer } from '../replay/ReplayPlayer'
import { useReplayFile, useReplayIndex } from '../sources/queries'
import { Button } from '../ui/Button'
import { Delayed } from '../ui/Delayed'
import { EmptyState } from '../ui/EmptyState'
import { InlineNotice } from '../ui/InlineNotice'
import { Page } from '../ui/Page'

const CRUMBS = [{ label: 'Replays', to: '/replays' }]

const Loading = () => (
  <Delayed>
    <p className="text-base text-ink-3">Loading</p>
  </Delayed>
)

function Failed({ text, retry }: { text: string; retry: () => void }) {
  return (
    <InlineNotice
      tone="fail"
      action={
        <Button size="sm" onClick={retry}>
          Retry
        </Button>
      }
    >
      {text}
    </InlineNotice>
  )
}

export function ReplayPage() {
  const { replayId = '' } = useParams()
  const index = useReplayIndex()
  const entry = index.data?.find((r) => r.id === replayId)
  useTitle(entry?.title ?? 'Replays')

  if (index.isPending) {
    return (
      <Page width="wide">
        <Loading />
      </Page>
    )
  }
  if (index.isError) {
    return (
      <Page width="wide">
        <PageHeader title="Replay" crumbs={CRUMBS} />
        <Failed text="Can’t load this replay" retry={() => void index.refetch()} />
      </Page>
    )
  }
  if (entry === undefined) {
    return (
      <Page>
        <PageHeader title="Replay not found" />
        <Link to="/replays" className="text-base text-ink-2 underline underline-offset-2 hover:text-ink">
          All replays
        </Link>
      </Page>
    )
  }
  return (
    <Page width="wide">
      <PageHeader title={entry.title} crumbs={CRUMBS} meta={entry.description} />
      <ReplayLoader key={entry.src} src={entry.src} />
    </Page>
  )
}

/** Fetches one events.jsonl and plays it. The stored runs in /history use it too. */
export function ReplayLoader({ src }: { src: string }) {
  const file = useReplayFile(src)
  if (file.isPending) return <Loading />
  if (file.isError) return <Failed text="Can’t load this replay" retry={() => void file.refetch()} />
  if (file.data.events.length === 0) return <EmptyState title="This replay has no events" />
  return <ReplayPlayer events={file.data.events} />
}
