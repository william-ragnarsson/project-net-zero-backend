import type { HTMLAttributes, ReactNode } from 'react'

const join = (base: string, extra: string | undefined) => (extra === undefined ? base : `${base} ${extra}`)

/** A white panel on the grey page. Most of the app's content sits in one. */
export function Card({ className, ...rest }: HTMLAttributes<HTMLElement>) {
  return <section className={join('rounded-xl border border-line bg-surface shadow-card', className)} {...rest} />
}

/** A card's title row: a title, an optional line under it, and an optional action on the right. */
export function CardHeader({
  title,
  description,
  action,
  className,
}: {
  title: ReactNode
  description?: ReactNode
  action?: ReactNode
  className?: string
}) {
  return (
    <div className={join('flex items-start justify-between gap-4 px-5 pb-3 pt-4', className)}>
      <div className="min-w-0">
        <h2 className="text-md font-medium text-ink">{title}</h2>
        {description !== undefined && <p className="mt-0.5 text-sm text-ink-3">{description}</p>}
      </div>
      {action !== undefined && <div className="flex shrink-0 items-center gap-2">{action}</div>}
    </div>
  )
}

/** A quiet bottom strip for a card: a note or a link. */
export function CardFooter({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={join('border-t border-line px-5 py-3 text-sm text-ink-3', className)} {...rest} />
}
