// /history/:projectId/runs/:runId?speed=&at=&node= plays one run's log as Postgres stored it.
import { useParams } from 'react-router'
import { PageHeader } from '../components/PageHeader'
import { formatWhen } from '../lib/format'
import { useTitle } from '../lib/useTitle'
import { storedRunSrc, useProject } from '../sources/history'
import { Page } from '../ui/Page'
import { ReplayLoader } from './ReplayPage'

export function StoredRunPage() {
  const { projectId = '', runId = '' } = useParams()
  // the project's detail names the project and dates the run; the replay doesn't wait for it
  const detail = useProject(projectId)
  const name = detail.data?.project.name ?? projectId
  const run = detail.data?.runs.find((r) => r.run_id === runId)
  const title = run === undefined ? runId : formatWhen(run.created_ts)
  useTitle(`${title} · ${name}`)

  return (
    <Page width="wide">
      <PageHeader
        title={title}
        crumbs={[
          { label: 'History', to: '/history' },
          { label: name, to: `/history/${encodeURIComponent(projectId)}` },
        ]}
        meta="Replayed from the log stored in Postgres"
      />
      <ReplayLoader src={storedRunSrc(runId)} />
    </Page>
  )
}
