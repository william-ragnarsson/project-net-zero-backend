// /replay?src= is the old replay link: it sends the viewer on to the replay's own page.
import { Navigate, useSearchParams } from 'react-router'
import { useReplayIndex } from '../sources/queries'
import { resolveReplayRedirect } from '../sources/replay'

export function ReplayRedirect() {
  const [params] = useSearchParams()
  const index = useReplayIndex()
  if (index.isPending) return null
  return <Navigate replace to={resolveReplayRedirect(index.data ?? [], params, window.location.origin)} />
}
