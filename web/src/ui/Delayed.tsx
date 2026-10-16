import { useEffect, useState, type ReactNode } from 'react'

/** Shows its children only after `ms`, so a fast load never flashes "Loading". */
export function Delayed({ ms = 300, children }: { ms?: number; children: ReactNode }) {
  const [shown, setShown] = useState(false)
  useEffect(() => {
    const t = setTimeout(() => setShown(true), ms)
    return () => clearTimeout(t)
  }, [ms])
  return shown ? children : null
}
