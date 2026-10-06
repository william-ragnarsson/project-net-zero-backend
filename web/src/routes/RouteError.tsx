import { isRouteErrorResponse, useRouteError } from 'react-router'
import { PageHeader } from '../components/PageHeader'

/** Catches what a page throws while rendering, so the header and frame stay usable. */
export function RouteError() {
  const error = useRouteError()
  const message = isRouteErrorResponse(error)
    ? `${error.status} ${error.statusText}`
    : error instanceof Error
      ? error.message
      : String(error)
  return (
    <PageHeader label="Error" title="This page broke.">
      <code className="font-mono text-sm text-fail">{message}</code>
    </PageHeader>
  )
}
