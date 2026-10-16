import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import { useHealth } from './queries'

/** When the server comes back, refetch everything, so pages recover without a reload. */
export function useHealthEdges() {
  const qc = useQueryClient()
  const { status } = useHealth()
  const last = useRef(status)
  useEffect(() => {
    if (last.current === 'error' && status === 'success') void qc.invalidateQueries()
    last.current = status
  }, [qc, status])
}
