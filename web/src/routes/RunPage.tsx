import { Link, useParams } from 'react-router'
import { PageHeader } from '../components/PageHeader'

export function RunPage() {
  const { runId = '' } = useParams()
  return (
    <PageHeader label="Run" title={<span className="font-mono text-[0.7em]">{runId}</span>}>
      The live view of a run is not built yet.{' '}
      <Link to="/replay" className="text-neon hover:text-neon-dim">
        Watch a replay
      </Link>{' '}
      to see what it will show.
    </PageHeader>
  )
}
