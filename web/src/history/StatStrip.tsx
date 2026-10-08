import type { ReactNode } from 'react'

export interface Stat {
  label: string
  value: ReactNode
  note?: ReactNode
}

/** A row of headline numbers under a page header, split by hairlines. */
export function StatStrip({ stats }: { stats: readonly Stat[] }) {
  return (
    <dl className="grid grid-cols-2 gap-px border-b border-dark-border bg-dark-border md:grid-cols-4">
      {stats.map((stat) => (
        <div key={stat.label} className="bg-dark px-6 py-6 md:px-12 md:py-8 [&:not(:first-child)]:md:px-8">
          <dt className="label">{stat.label}</dt>
          <dd className="mt-3 text-[1.9rem] font-light leading-none tracking-[-0.03em] text-white tabular-nums">
            {stat.value}
          </dd>
          {stat.note !== undefined && <dd className="mt-2 text-sm text-muted">{stat.note}</dd>}
        </div>
      ))}
    </dl>
  )
}
