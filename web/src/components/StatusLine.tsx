import { useQueryClient } from '@tanstack/react-query'
import { HEALTH_KEY, useHealth } from '../api/queries'

/**
 * The one quiet line under a page header. Offline wins over the page's own status (the run
 * page's reconnect text), so only one message ever shows.
 */
export function StatusLine({ status }: { status?: string | null | undefined }) {
  const qc = useQueryClient()
  const health = useHealth()
  const offline = health.isError

  const retry = async () => {
    await qc.refetchQueries({ queryKey: HEALTH_KEY })
    if (qc.getQueryState(HEALTH_KEY)?.status === 'success') void qc.invalidateQueries()
  }

  const text = offline ? 'Can’t reach the net-zero server' : (status ?? null)
  return (
    <p role="status" aria-live="polite" className="basis-full text-sm text-ink-2 empty:hidden">
      {text}
      {offline && (
        <button type="button" onClick={() => void retry()} className="ml-2 text-ink underline-offset-2 hover:underline">
          Retry
        </button>
      )}
    </p>
  )
}
