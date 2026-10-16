import { useId, type ReactNode } from 'react'
import { Card } from '../ui/Card'
import { Icon, type IconName } from '../ui/Icon'

export interface Stat {
  label: string
  icon: IconName
  value: ReactNode
  note?: ReactNode
}

/** A row of headline numbers under a page header, one card each. */
export function StatStrip({ stats }: { stats: readonly Stat[] }) {
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {stats.map((stat) => (
        <StatCard key={stat.label} stat={stat} />
      ))}
    </div>
  )
}

function StatCard({ stat }: { stat: Stat }) {
  const id = useId()
  return (
    <Card aria-labelledby={id} className="p-5">
      <div className="flex items-start justify-between gap-3">
        <h2 id={id} className="text-sm font-medium text-ink-2">
          {stat.label}
        </h2>
        <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-hover text-ink-2">
          <Icon name={stat.icon} />
        </span>
      </div>
      <p className="mt-2 text-xl font-semibold tabular-nums tracking-tight text-ink">{stat.value}</p>
      {stat.note !== undefined && <p className="mt-1 text-sm text-ink-3">{stat.note}</p>}
    </Card>
  )
}
