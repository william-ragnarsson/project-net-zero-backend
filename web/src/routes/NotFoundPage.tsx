import { Link } from 'react-router'
import { useTitle } from '../lib/useTitle'
import { buttonClass } from '../ui/Button'
import { EmptyState } from '../ui/EmptyState'
import { Page } from '../ui/Page'

export function NotFoundPage() {
  useTitle('Page not found')
  return (
    <Page>
      <EmptyState
        title="Page not found"
        body="There’s nothing at this address."
        actions={
          <Link to="/" className={buttonClass('secondary', 'md')}>
            Go to the dashboard
          </Link>
        }
      />
    </Page>
  )
}
