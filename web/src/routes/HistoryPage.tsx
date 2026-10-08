// /history: what Postgres holds of every run, across projects. With no database configured it
// says how to set one up instead.
import { PageHeader } from '../components/PageHeader'
import type { HistoryStatus, ProjectSummary } from '../gen/events'
import { ActivityFeed } from '../history/ActivityFeed'
import { ProjectCard } from '../history/ProjectCard'
import { Setup } from '../history/Setup'
import { StatStrip } from '../history/StatStrip'
import { StorePanel } from '../history/StorePanel'
import { formatBytes, formatInt } from '../lib/format'
import { useHistoryStatus, useProjects } from '../sources/history'

/** The database answers and has its tables, so the read endpoints work. */
export const isReadable = (status: HistoryStatus | undefined): boolean =>
  status !== undefined && status.enabled && status.migrations.length > 0

export function HistoryPage() {
  const status = useHistoryStatus()
  const readable = isReadable(status.data)
  const projects = useProjects(readable)

  if (status.isPending) return <PageHeader label="History" title="Loading the history" />
  if (status.isError) {
    return (
      <PageHeader label="History" title="The server isn't answering.">
        <code className="font-mono text-sm text-fail">{status.error.message}</code>
        <p className="mt-3">
          History is read from the net-zero server: start it with{' '}
          <code className="font-mono text-sm text-gray-300">make dev</code>.
        </p>
      </PageHeader>
    )
  }

  if (!status.data.enabled) {
    return (
      <>
        <PageHeader label="History" title="Keep every run in Postgres.">
          Each run already writes its events to <code className="font-mono text-sm text-gray-300">runs/</code>. With a
          database configured, the server also copies every event into Postgres, and this tab shows what changed
          across runs: per project, per function and per rewrite strategy.
        </PageHeader>
        <section className="px-6 py-12 md:px-12">
          <Setup />
        </section>
      </>
    )
  }

  if (!readable) {
    return (
      <PageHeader label="History" title="Can't read the database yet.">
        <code className="font-mono text-sm text-fail">{status.data.detail}</code>
        <p className="mt-3">
          <code className="font-mono text-sm text-gray-300">make db</code> starts the bundled one. The server keeps
          retrying, and this page picks it up on its own.
        </p>
      </PageHeader>
    )
  }

  const now = Date.now()
  return (
    <>
      <PageHeader label="History" title="Every run, kept in Postgres.">
        The server copies each run&apos;s events into Postgres as they happen. Pick a project to see how its
        functions changed from run to run, or click any event below to replay its run from that point.
      </PageHeader>
      {projects.data !== undefined && <Totals projects={projects.data} status={status.data} />}

      <section className="px-6 py-12 md:px-12">
        <h2 className="label mb-6">Projects</h2>
        {projects.isPending && <p className="label">Loading projects</p>}
        {projects.isError && <p className="text-sm text-fail">Could not load projects: {projects.error.message}</p>}
        {projects.data?.length === 0 && (
          <p className="text-sm text-muted">
            No runs stored yet. Start one, or store the bundled replays with{' '}
            <code className="font-mono text-gray-300">uv run netzero db import web/public/replays/*.events.jsonl</code>.
          </p>
        )}
        {projects.data !== undefined && projects.data.length > 0 && (
          <ul className="grid gap-px border border-dark-border bg-dark-border md:grid-cols-2">
            {projects.data.map((project) => (
              <li key={project.id} className="bg-dark">
                <ProjectCard project={project} now={now} />
              </li>
            ))}
            {projects.data.length % 2 === 1 && <li aria-hidden="true" className="hidden bg-dark md:block" />}
          </ul>
        )}
      </section>

      <div className="grid border-t border-dark-border lg:grid-cols-[minmax(0,1fr)_26rem]">
        <section className="px-6 py-12 md:px-12">
          <h2 className="label mb-6">Activity</h2>
          <ActivityFeed enabled={readable} now={now} />
        </section>
        <aside className="border-t border-dark-border px-6 py-12 md:px-12 lg:border-l lg:border-t-0 lg:px-8">
          <h2 className="label mb-6">Inside Postgres</h2>
          <StorePanel status={status.data} now={now} />
        </aside>
      </div>
    </>
  )
}

function Totals({ projects, status }: { projects: readonly ProjectSummary[]; status: HistoryStatus }) {
  const sum = (pick: (p: ProjectSummary) => number) => projects.reduce((n, p) => n + pick(p), 0)
  const events = status.tables.find((t) => t.name === 'events')
  return (
    <StatStrip
      stats={[
        { label: 'Projects', value: formatInt(projects.length) },
        { label: 'Runs', value: formatInt(sum((p) => p.runs)), note: `${formatInt(sum((p) => p.runs_completed))} completed` },
        {
          label: 'Functions improved',
          value: formatInt(sum((p) => p.functions_improved)),
          note: `of ${formatInt(sum((p) => p.functions_tracked))} tried`,
        },
        {
          label: 'Events stored',
          value: formatInt(events?.rows ?? status.head_position),
          ...(events !== undefined && { note: formatBytes(events.bytes) }),
        },
      ]}
    />
  )
}
