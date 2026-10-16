import type { ReactNode } from 'react'
import { Icon } from './Icon'

/** The one style for details on demand: grey text and a small chevron, no box. */
export function Disclosure({
  summary,
  defaultOpen = false,
  className,
  children,
}: {
  summary: ReactNode
  defaultOpen?: boolean
  className?: string
  children: ReactNode
}) {
  return (
    <details className={className === undefined ? 'group' : `group ${className}`} open={defaultOpen}>
      <summary className="inline-flex cursor-pointer list-none items-center gap-1 text-sm text-ink-2 transition-colors duration-150 hover:text-ink [&::-webkit-details-marker]:hidden">
        {summary}
        <Icon name="chevron-down" size={12} className="transition-transform duration-150 group-open:rotate-180" />
      </summary>
      <div className="mt-3">{children}</div>
    </details>
  )
}
