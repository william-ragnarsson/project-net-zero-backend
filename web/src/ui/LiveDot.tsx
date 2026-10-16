/** The app's one continuous animation: a slow breathe on the active run's sidebar row. */
export function LiveDot() {
  return <span aria-hidden="true" className="inline-block size-1.5 shrink-0 rounded-full bg-ink-2 motion-safe:animate-breathe" />
}
