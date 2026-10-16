import type { ReactNode } from 'react'

export type BadgeTone = 'success' | 'neutral' | 'fail' | 'live' | 'caution'

const TONE: Record<BadgeTone, string> = {
  success: 'bg-accent-tint text-accent',
  neutral: 'bg-hover text-ink-2',
  fail: 'bg-fail-tint text-fail',
  live: 'bg-accent-tint text-accent',
  caution: 'bg-caution-tint text-caution',
}

/** A small tinted label for a state. A live one breathes. */
export function Badge({ tone = 'neutral', children }: { tone?: BadgeTone; children: ReactNode }) {
  return (
    <span className={`inline-flex h-5 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full px-2 text-xs font-medium ${TONE[tone]}`}>
      {tone === 'live' && <span aria-hidden="true" className="size-1.5 rounded-full bg-accent motion-safe:animate-breathe" />}
      {children}
    </span>
  )
}
