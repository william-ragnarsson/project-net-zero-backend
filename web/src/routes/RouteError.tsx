import { isRouteErrorResponse, useRouteError } from 'react-router'
import { useTitle } from '../lib/useTitle'
import { Button } from '../ui/Button'
import { EmptyState } from '../ui/EmptyState'
import { Page } from '../ui/Page'

/** Catches what a page throws while rendering, so the sidebar stays usable. */
export function RouteError() {
  const error = useRouteError()
  useTitle('Something went wrong')
  const message = isRouteErrorResponse(error)
    ? `${error.status} ${error.statusText}`
    : error instanceof Error
      ? error.message
      : String(error)
  return (
    <Page>
      <EmptyState
        title="Something went wrong"
        actions={
          <Button variant="secondary" onClick={() => window.location.reload()}>
            Reload
          </Button>
        }
      />
      {import.meta.env.DEV && <pre className="mt-6 whitespace-pre-wrap text-left font-mono text-code text-ink-2">{message}</pre>}
    </Page>
  )
}
