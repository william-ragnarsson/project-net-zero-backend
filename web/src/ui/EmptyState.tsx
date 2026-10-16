import type { ReactNode } from 'react'

/** A centred title with one sentence and its actions, for a page with nothing to show yet. */
export function EmptyState({ title, body, actions }: { title: string; body?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="py-16 text-center">
      <h2 className="text-md font-medium text-ink">{title}</h2>
      {body !== undefined && <p className="mx-auto mt-1 max-w-[420px] text-base text-ink-2">{body}</p>}
      {actions !== undefined && <div className="mt-5 flex justify-center gap-2">{actions}</div>}
    </div>
  )
}
