import { useCallback, useEffect, useState, useSyncExternalStore } from 'react'
import { useStore } from 'zustand'
import { createRunSession } from '../state/store'
import { connectLive, type LiveConnection, type LiveStatus } from './live'

const LOADING: LiveStatus = { kind: 'loading' }
const noopSubscribe = () => () => {}
const loading = () => LOADING

/** Follows one run: a session per page (the route is keyed by run id) and one live connection. */
export function useLiveRun(runId: string) {
  const [session] = useState(createRunSession)
  const [attempt, setAttempt] = useState(0)
  const [conn, setConn] = useState<LiveConnection | null>(null)

  // StrictMode runs this twice; the second connection re-hydrates, which is harmless
  useEffect(() => {
    const c = connectLive(session, runId)
    setConn(c)
    return c.stop
  }, [session, runId, attempt])

  const status = useSyncExternalStore(conn?.subscribe ?? noopSubscribe, conn?.getStatus ?? loading)
  const model = useStore(session, (s) => s.state)
  const epoch = useStore(session, (s) => s.epoch)
  const retry = useCallback(() => setAttempt((n) => n + 1), [])

  return { session, model, epoch, status, conn, retry }
}
