import { Fragment, type ReactNode } from 'react'
import { Link } from 'react-router'
import { StatusLine } from './StatusLine'

/** Every page starts with this: the title (or breadcrumb), one meta line, up to two actions. */
export function PageHeader({
  title,
  crumbs,
  meta,
  actions,
  status,
}: {
  title: ReactNode
  crumbs?: { label: string; to: string }[]
  meta?: ReactNode
  actions?: ReactNode
  status?: string | null
}) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3 pb-6 lg:pb-8">
      <div className="min-w-0">
        {crumbs !== undefined && crumbs.length > 0 ? (
          <nav aria-label="Breadcrumb" className="flex min-w-0 items-baseline gap-2 text-lg tracking-tight">
            {crumbs.map((c) => (
              <Fragment key={c.to}>
                <Link to={c.to} className="text-ink-2 hover:text-ink">
                  {c.label}
                </Link>
                <span className="text-ink-3">/</span>
              </Fragment>
            ))}
            <h1 className="truncate font-semibold text-ink">{title}</h1>
          </nav>
        ) : (
          <h1 className="text-title font-semibold tracking-tight text-ink">{title}</h1>
        )}
        {meta !== undefined && <div className="mt-1 text-base text-ink-2">{meta}</div>}
      </div>
      {actions !== undefined && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      <StatusLine status={status} />
    </header>
  )
}
