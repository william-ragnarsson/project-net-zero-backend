import { Link } from 'react-router'
import { PageHeader } from '../components/PageHeader'

export function NotFoundPage() {
  return (
    <PageHeader label="404" title="Nothing lives at this address.">
      <Link to="/" className="text-neon hover:text-neon-dim">
        Back to the start
      </Link>
    </PageHeader>
  )
}
