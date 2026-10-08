import { Link } from 'react-router'
import type { ProjectSummary } from '../gen/events'
import { formatAgo, formatGrams, NONE } from '../lib/format'
import { RUN_STATE } from './labels'
import { MiniTrend } from './MiniTrend'

export function ProjectCard({ project, now }: { project: ProjectSummary; now: number }) {
  const state = project.last_state === null ? null : RUN_STATE[project.last_state]
  const grams = (g: number | null) => (g === null ? NONE : formatGrams(g))
  return (
    <Link
      to={`/history/${encodeURIComponent(project.id)}`}
      className="group flex h-full flex-col p-6 transition-colors hover:bg-dark-card md:p-8"
    >
      <div className="flex items-baseline justify-between gap-4">
        <p className="truncate text-lg text-white transition-colors group-hover:text-neon">{project.name}</p>
        <span className="label shrink-0">{project.kind}</span>
      </div>
      <p className="mt-1 text-sm text-muted">
        {project.runs} {project.runs === 1 ? 'run' : 'runs'}
        {project.last_run_ts !== null && <> · last {formatAgo(project.last_run_ts, now)}</>}
        {state !== null && (
          <>
            {' · '}
            <span style={{ color: state.color }}>{state.text}</span>
          </>
        )}
      </p>
      <div className="mt-6 flex-1">
        <MiniTrend points={project.trend} />
      </div>
      <dl className="mt-5 grid grid-cols-3 gap-4 border-t border-dark-border pt-4 text-sm">
        <div>
          <dt className="label">Improved</dt>
          <dd className="mt-1 text-white tabular-nums">
            {project.functions_improved}
            <span className="text-muted"> of {project.functions_tracked}</span>
          </dd>
        </div>
        <div>
          <dt className="label">Latest run</dt>
          <dd className="mt-1 text-white tabular-nums">{grams(project.latest_g_saved_per_1m_calls)}</dd>
        </div>
        <div>
          <dt className="label">Best run</dt>
          <dd className="mt-1 text-white tabular-nums">{grams(project.best_g_saved_per_1m_calls)}</dd>
        </div>
      </dl>
    </Link>
  )
}
