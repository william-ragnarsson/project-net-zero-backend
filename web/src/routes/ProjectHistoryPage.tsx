// /history/:projectId: one repository across its stored runs.
import { Link, useParams } from 'react-router'
import { Co2 } from '../components/Co2'
import { PageHeader } from '../components/PageHeader'
import type { HistoryRun, ProjectDetail } from '../gen/events'
import { ActivityFeed } from '../history/ActivityFeed'
import { FunctionsMatrix, MatrixLegend } from '../history/FunctionsMatrix'
import { RUN_STATE, shortSha } from '../history/labels'
import { RunsChart } from '../history/RunsChart'
import { StatStrip } from '../history/StatStrip'
import { RejectReasons, StrategyFunnel } from '../history/WhatWins'
import { formatDate, formatGrams, formatInt, formatPct, formatTime, formatUsd, NONE } from '../lib/format'
import { ApiFailure, storedReplayHref, useProject } from '../sources/history'

const back = (
  <Link to="/history" className="transition-colors hover:text-neon">
    ← History
  </Link>
)

export function ProjectHistoryPage() {
  const { projectId = '' } = useParams()
  const detail = useProject(projectId)

  if (detail.isPending) return <PageHeader label={back} title="Loading the project" />
  if (detail.isError) {
    const missing = detail.error instanceof ApiFailure && detail.error.status === 404
    return (
      <PageHeader label={back} title={missing ? 'No stored runs for this project.' : "Can't read the history."}>
        <code className="font-mono text-sm text-gray-300">{missing ? projectId : detail.error.message}</code>
      </PageHeader>
    )
  }
  return <ProjectView detail={detail.data} />
}

function ProjectView({ detail }: { detail: ProjectDetail }) {
  const { project, runs } = detail
  const first = runs.at(-1)
  const grams = (g: number | null) => (g === null ? NONE : formatGrams(g))
  const perCall = (
    <>
      <Co2 /> saved per 1M calls
    </>
  )
  return (
    <>
      <PageHeader label={back} title={project.name}>
        {formatInt(project.runs)} {project.runs === 1 ? 'run' : 'runs'}
        {first !== undefined && <> since {formatDate(first.created_ts)}</>}
        {project.url !== null && (
          <>
            {' · '}
            <a href={project.url} target="_blank" rel="noreferrer" className="text-gray-300 hover:text-neon">
              {project.url.replace(/^https?:\/\//, '')}
            </a>
          </>
        )}
      </PageHeader>
      <StatStrip
        stats={[
          { label: 'Runs', value: formatInt(project.runs), note: `${formatInt(project.runs_completed)} completed` },
          {
            label: 'Functions improved',
            value: formatInt(project.functions_improved),
            note: `of ${formatInt(project.functions_tracked)} tried`,
          },
          { label: 'Latest run', value: grams(project.latest_g_saved_per_1m_calls), note: perCall },
          { label: 'Best run', value: grams(project.best_g_saved_per_1m_calls), note: perCall },
        ]}
      />

      <section className="px-6 py-12 md:px-12">
        <h2 className="label">Runs</h2>
        <p className="mb-8 mt-2 max-w-2xl text-sm text-muted">
          Each bar is one run: the <Co2 /> its kept rewrites save per 1M calls. Click one to replay it.
        </p>
        <RunsChart runs={runs} />
        <RunsTable runs={runs} />
      </section>

      <section className="border-t border-dark-border px-6 py-12 md:px-12">
        <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h2 className="label">Functions</h2>
            <p className="mt-2 max-w-2xl text-sm text-muted">
              What each run did with each function. Click a row for the measurements and the diff that was kept.
            </p>
          </div>
          <MatrixLegend />
        </div>
        <FunctionsMatrix functions={detail.functions} runs={runs} />
      </section>

      <div className="grid border-t border-dark-border lg:grid-cols-2">
        <section className="px-6 py-12 md:px-12">
          <h2 className="label">Which rewrites get kept</h2>
          <p className="mb-8 mt-2 text-sm text-muted">
            Every function gets three candidate rewrites, each written with a different strategy.
          </p>
          <StrategyFunnel candidates={detail.candidates} />
        </section>
        <section className="border-t border-dark-border px-6 py-12 md:px-12 lg:border-l lg:border-t-0">
          <h2 className="label">Why rewrites were rejected</h2>
          <p className="mb-8 mt-2 text-sm text-muted">A rewrite is dropped at the first check it fails.</p>
          <RejectReasons rejections={detail.rejections} />
        </section>
      </div>

      <section className="border-t border-dark-border px-6 py-12 md:px-12">
        <h2 className="label mb-6">Activity</h2>
        <div className="max-w-3xl">
          <ActivityFeed enabled project={project.id} now={Date.now()} />
        </div>
      </section>
    </>
  )
}

function RunsTable({ runs }: { runs: readonly HistoryRun[] }) {
  return (
    <div className="mt-10 overflow-x-auto">
      <table className="w-full min-w-[46rem] border-collapse text-sm">
        <thead>
          <tr className="text-left">
            {['Started', 'Version', 'State', 'Improved', 'Mean change', 'Saved per 1M calls', 'LLM cost', ''].map((h, i) => (
              <th key={h || i} className={`label pb-3 pr-4 font-normal ${i >= 3 ? 'text-right' : ''}`}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="tabular-nums">
          {runs.map((run) => {
            const state = RUN_STATE[run.state]
            const accepted = run.counts_by_outcome.accepted ?? 0
            return (
              <tr key={run.run_id} className="border-t border-dark-border">
                <td className="whitespace-nowrap py-3 pr-4 text-gray-300">
                  {formatDate(run.created_ts)} <span className="font-mono text-xs text-muted">{formatTime(run.created_ts)}</span>
                </td>
                <td className="py-3 pr-4 font-mono text-xs text-gray-300">
                  {run.ref ?? shortSha(run.base_sha) ?? NONE}
                  {run.ref !== null && run.base_sha !== null && (
                    <span className="text-muted"> {shortSha(run.base_sha)}</span>
                  )}
                </td>
                <td className="py-3 pr-4" title={run.error ?? undefined}>
                  <span style={{ color: state.color }}>{state.text}</span>
                  {run.error !== null && <span className="block max-w-56 truncate text-xs text-muted">{run.error}</span>}
                </td>
                <td className="py-3 pr-4 text-right font-mono text-xs">
                  {run.functions_total === 0 ? (
                    <span className="text-muted">{NONE}</span>
                  ) : (
                    <>
                      <span className={accepted > 0 ? 'text-neon' : 'text-gray-300'}>{accepted}</span>
                      <span className="text-muted"> of {run.functions_total}</span>
                    </>
                  )}
                </td>
                <td className="py-3 pr-4 text-right font-mono text-xs text-gray-300">{formatPct(run.mean_reduction_pct)}</td>
                <td className="py-3 pr-4 text-right font-mono text-xs text-gray-300">
                  {run.g_saved_per_1m_calls > 0 ? formatGrams(run.g_saved_per_1m_calls) : NONE}
                </td>
                <td className="py-3 pr-4 text-right font-mono text-xs text-muted">{formatUsd(run.llm_cost_usd)}</td>
                <td className="py-3 text-right">
                  <Link
                    to={storedReplayHref(run.run_id)}
                    className="font-mono text-xs text-gray-400 transition-colors hover:text-neon"
                  >
                    replay
                  </Link>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
