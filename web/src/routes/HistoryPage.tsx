// /history: what Postgres holds of every run, across projects. With no database configured it
// says how to set one up instead.
import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { PageHeader } from '../components/PageHeader'
import type { HistoryStatus, ProjectSummary } from '../gen/events'
import { ActivityFeed } from '../history/ActivityFeed'
import { ProjectCard } from '../history/ProjectCard'
import { Setup } from '../history/Setup'
import { StatStrip } from '../history/StatStrip'
import { StorePanel } from '../history/StorePanel'
import { formatBytes, formatInt } from '../lib/format'
import { useTitle } from '../lib/useTitle'
import { useHistoryStatus, useProjects } from '../sources/history'
import { Button, buttonClass } from '../ui/Button'
import { Card, CardHeader } from '../ui/Card'
import { Delayed } from '../ui/Delayed'
import { EmptyState } from '../ui/EmptyState'
import { InlineNotice } from '../ui/InlineNotice'
import { Page } from '../ui/Page'

/** The database answers and has its tables, so the read endpoints work. */
export const isReadable = (status: HistoryStatus | undefined): boolean =>
  status !== undefined && status.enabled && status.migrations.length > 0

const Code = ({ children }: { children: ReactNode }) => <code className="font-mono text-code text-ink">{children}</code>

const Loading = () => (
  <Delayed>
    <p className="text-base text-ink-3">Loading</p>
  </Delayed>
)

export function HistoryPage() {
  useTitle('History')
  const status = useHistoryStatus()
  const readable = isReadable(status.data)
  const projects = useProjects(readable)

  if (status.isPending) {
    return (
      <Page>
        <PageHeader title="History" />
        <Loading />
      </Page>
    )
  }
  if (status.isError) {
    return (
      <Page>
        <PageHeader title="History" />
        <InlineNotice
          tone="fail"
          action={
            <Button size="sm" onClick={() => void status.refetch()}>
              Retry
            </Button>
          }
        >
          Can’t reach the net-zero server: {status.error.message}
        </InlineNotice>
        <p className="mt-3 text-base text-ink-2">
          History is read from the server. Start it with <Code>make dev</Code>.
        </p>
      </Page>
    )
  }

  if (!status.data.enabled) {
    return (
      <Page>
        <PageHeader title="History" meta="Keep every run in Postgres and see what changed from one run to the next" />
        <div className="space-y-6">
          <p className="max-w-2xl text-base text-ink-2">
            Each run already writes its events to <Code>runs/</Code>. With a database set up, the server also copies
            every event into Postgres, and this page shows what changed across runs: per project, per function and per
            rewrite strategy.
          </p>
          <Setup />
        </div>
      </Page>
    )
  }

  if (!readable) {
    return (
      <Page>
        <PageHeader title="History" />
        <InlineNotice tone="fail">Can’t read the database yet: {status.data.detail}</InlineNotice>
        <p className="mt-3 text-base text-ink-2">
          <Code>make db</Code> starts the bundled one. The server keeps retrying, and this page picks it up on its own.
        </p>
      </Page>
    )
  }

  const now = Date.now()
  return (
    <Page width="wide">
      <PageHeader
        title="History"
        meta="Every run, kept in Postgres. Open a project to see how its functions changed from run to run."
      />
      <div className="space-y-6">
        {projects.data !== undefined && <Totals projects={projects.data} status={status.data} />}

        <section aria-labelledby="history-projects" className="space-y-3">
          <h2 id="history-projects" className="text-md font-medium text-ink">
            Projects
          </h2>
          {projects.isPending && <Loading />}
          {projects.isError && (
            <InlineNotice
              tone="fail"
              action={
                <Button size="sm" onClick={() => void projects.refetch()}>
                  Retry
                </Button>
              }
            >
              Can’t load projects: {projects.error.message}
            </InlineNotice>
          )}
          {projects.data?.length === 0 && (
            <Card>
              <EmptyState
                title="No runs stored yet"
                body={
                  <>
                    Start a run, or store the bundled replays with{' '}
                    <Code>uv run netzero db import web/public/replays/*.events.jsonl</Code>
                  </>
                }
                actions={
                  <Link to="/new" className={buttonClass('primary', 'md')}>
                    Start a run
                  </Link>
                }
              />
            </Card>
          )}
          {projects.data !== undefined && projects.data.length > 0 && (
            <ul className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {projects.data.map((project) => (
                <li key={project.id}>
                  <ProjectCard project={project} now={now} />
                </li>
              ))}
            </ul>
          )}
        </section>

        <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_24rem]">
          <Card>
            <CardHeader title="Activity" description="Open any line to replay its run from that point" />
            <ActivityFeed enabled={readable} now={now} />
          </Card>
          <Card>
            <CardHeader title="Inside Postgres" description="The log, and the tables folded from it" />
            <StorePanel status={status.data} now={now} />
          </Card>
        </div>
      </div>
    </Page>
  )
}

function Totals({ projects, status }: { projects: readonly ProjectSummary[]; status: HistoryStatus }) {
  const sum = (pick: (p: ProjectSummary) => number) => projects.reduce((n, p) => n + pick(p), 0)
  const events = status.tables.find((t) => t.name === 'events')
  return (
    <StatStrip
      stats={[
        { label: 'Projects', icon: 'file', value: formatInt(projects.length) },
        {
          label: 'Runs',
          icon: 'runs',
          value: formatInt(sum((p) => p.runs)),
          note: `${formatInt(sum((p) => p.runs_completed))} completed`,
        },
        {
          label: 'Functions improved',
          icon: 'code',
          value: formatInt(sum((p) => p.functions_improved)),
          note: `of ${formatInt(sum((p) => p.functions_tracked))} tried`,
        },
        {
          label: 'Events stored',
          icon: 'database',
          value: formatInt(events?.rows ?? status.head_position),
          ...(events !== undefined && { note: formatBytes(events.bytes) }),
        },
      ]}
    />
  )
}
