// Whether the viewer asked for reduced motion. False where matchMedia does not exist (tests, SSR).
import { useSyncExternalStore } from 'react'

const QUERY = '(prefers-reduced-motion: reduce)'

const media = (): MediaQueryList | null =>
  typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia(QUERY) : null

function subscribe(onChange: () => void): () => void {
  const mq = media()
  if (mq === null) return () => {}
  mq.addEventListener('change', onChange)
  return () => mq.removeEventListener('change', onChange)
}

const snapshot = (): boolean => media()?.matches ?? false

export const useReducedMotion = (): boolean => useSyncExternalStore(subscribe, snapshot, () => false)
