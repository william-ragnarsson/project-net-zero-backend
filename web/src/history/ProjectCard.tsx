import { Link } from 'react-router'
import type { ProjectSummary } from '../gen/events'
import { formatAgo, formatGrams, NONE } from '../lib/format'
import { Badge } from '../ui/Badge'
import { Card } from '../ui/Card'
import { runState } from './labels'
import { MiniTrend } from './MiniTrend'

export function ProjectCard({ project, now }: { project: ProjectSummary; now: number }) {
  const state = project.last_state === null ? null : runState(project.last_state)
  const grams = (g: number | null) => (g === null ? NONE : formatGrams(g))
  return (
    <Card className="relative flex h-full flex-col p-5 transition-colors duration-150 hover:border-control">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate text-md font-medium text-ink">
            <Link
              to={`/history/${encodeURIComponent(project.id)}`}
              className="after:absolute after:inset-0 after:rounded-xl focus-visible:outline-none"
            >
              {project.name}
            </Link>
          </h3>
          <p className="mt-0.5 text-sm text-ink-3">
            {project.runs} {project.runs === 1 ? 'run' : 'runs'}
            {project.last_run_ts !== null && <> · last {formatAgo(project.last_run_ts, now)}</>}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {project.kind === 'demo' && <Badge>Demo</Badge>}
          {state !== null && <Badge tone={state.tone}>{state.text}</Badge>}
        </div>
      </div>
      <div className="mt-5 flex-1">
        <MiniTrend points={project.trend} />
      </div>
      <dl className="mt-4 grid grid-cols-3 gap-4 border-t border-line pt-4 text-sm">
        <div>
          <dt className="text-ink-3">Improved</dt>
          <dd className="mt-0.5 tabular-nums text-ink">
            {project.functions_improved}
            <span className="text-ink-3"> of {project.functions_tracked}</span>
          </dd>
        </div>
        <div>
          <dt className="text-ink-3">Latest run</dt>
          <dd className="mt-0.5 tabular-nums text-ink">{grams(project.latest_g_saved_per_1m_calls)}</dd>
        </div>
        <div>
          <dt className="text-ink-3">Best run</dt>
          <dd className="mt-0.5 tabular-nums text-ink">{grams(project.best_g_saved_per_1m_calls)}</dd>
        </div>
      </dl>
    </Card>
  )
}
