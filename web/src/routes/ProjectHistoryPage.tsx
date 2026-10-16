// /history/:projectId: one repository across its stored runs.
import { Link, useParams } from 'react-router'
import { Co2 } from '../components/Co2'
import { PageHeader } from '../components/PageHeader'
import type { HistoryRun, ProjectDetail } from '../gen/events'
import { ActivityFeed } from '../history/ActivityFeed'
import { FunctionsMatrix, MatrixLegend } from '../history/FunctionsMatrix'
import { runState, shortSha } from '../history/labels'
import { RunsChart } from '../history/RunsChart'
import { StatStrip } from '../history/StatStrip'
import { RejectReasons, StrategyFunnel } from '../history/WhatWins'
import { formatDate, formatGrams, formatInt, formatPct, formatUsd, formatWhen, NONE } from '../lib/format'
import { useTitle } from '../lib/useTitle'
import { ApiFailure, storedReplayHref, useProject } from '../sources/history'
import { Badge } from '../ui/Badge'
import { Button } from '../ui/Button'
import { Card, CardHeader } from '../ui/Card'
import { Delayed } from '../ui/Delayed'
import { InlineNotice } from '../ui/InlineNotice'
import { Page } from '../ui/Page'
import { Table, TBody, Td, Th, THead, Tr } from '../ui/Table'

const CRUMBS = [{ label: 'History', to: '/history' }]

export function ProjectHistoryPage() {
  const { projectId = '' } = useParams()
  const detail = useProject(projectId)
  useTitle(detail.data?.project.name ?? 'History')

  if (detail.isPending) {
    return (
      <Page width="wide">
        <Delayed>
          <p className="text-base text-ink-3">Loading</p>
        </Delayed>
      </Page>
    )
  }
  if (detail.isError) {
    const missing = detail.error instanceof ApiFailure && detail.error.status === 404
    return (
      <Page>
        <PageHeader title={missing ? 'Project not found' : 'History'} crumbs={CRUMBS} />
        {missing ? (
          <p className="text-base text-ink-2">
            No stored runs for <span className="font-mono text-code text-ink">{projectId}</span>.
          </p>
        ) : (
          <InlineNotice
            tone="fail"
            action={
              <Button size="sm" onClick={() => void detail.refetch()}>
                Retry
              </Button>
            }
          >
            Can’t read the history: {detail.error.message}
          </InlineNotice>
        )}
      </Page>
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
    <Page width="wide">
      <PageHeader
        title={project.name}
        crumbs={CRUMBS}
        meta={
          <>
            {formatInt(project.runs)} {project.runs === 1 ? 'run' : 'runs'}
            {first !== undefined && <> since {formatDate(first.created_ts)}</>}
            {project.url !== null && (
              <>
                {' · '}
                <a
                  href={project.url}
                  target="_blank"
                  rel="noreferrer"
                  className="underline-offset-2 hover:text-ink hover:underline"
                >
                  {project.url.replace(/^https?:\/\//, '')}
                </a>
              </>
            )}
          </>
        }
      />
      <div className="space-y-6">
        <StatStrip
          stats={[
            { label: 'Runs', icon: 'runs', value: formatInt(project.runs), note: `${formatInt(project.runs_completed)} completed` },
            {
              label: 'Functions improved',
              icon: 'code',
              value: formatInt(project.functions_improved),
              note: `of ${formatInt(project.functions_tracked)} tried`,
            },
            { label: 'Latest run', icon: 'leaf', value: grams(project.latest_g_saved_per_1m_calls), note: perCall },
            { label: 'Best run', icon: 'trend-down', value: grams(project.best_g_saved_per_1m_calls), note: perCall },
          ]}
        />

        <Card>
          <CardHeader
            title="Runs"
            description={
              <>
                Each bar is one run: the <Co2 /> its kept rewrites save per 1M calls. Click one to replay it.
              </>
            }
          />
          <div className="px-5 pb-5">
            <RunsChart projectId={project.id} runs={runs} />
          </div>
          <RunsTable projectId={project.id} runs={runs} />
        </Card>

        <Card>
          <CardHeader
            title="Functions"
            description="What each run did with each function. Open a row for the measurements and the diff that was kept."
            action={
              <div className="hidden md:block">
                <MatrixLegend />
              </div>
            }
          />
          <FunctionsMatrix functions={detail.functions} runs={runs} />
        </Card>

        <div className="grid items-start gap-6 lg:grid-cols-2">
          <Card>
            <CardHeader
              title="Which rewrites get kept"
              description="Every function gets three rewrites, each written with a different strategy"
            />
            <div className="px-5 pb-5 pt-1">
              <StrategyFunnel candidates={detail.candidates} />
            </div>
          </Card>
          <Card>
            <CardHeader title="Why rewrites were rejected" description="A rewrite is dropped at the first check it fails" />
            <div className="px-5 pb-5 pt-1">
              <RejectReasons rejections={detail.rejections} />
            </div>
          </Card>
        </div>

        <Card>
          <CardHeader title="Activity" description="Open any line to replay its run from that point" />
          <ActivityFeed enabled project={project.id} now={Date.now()} />
        </Card>
      </div>
    </Page>
  )
}

function RunsTable({ projectId, runs }: { projectId: string; runs: readonly HistoryRun[] }) {
  return (
    <div className="border-t border-line px-5">
      <Table className="min-w-[46rem]">
        <THead>
          <tr>
            <Th>Started</Th>
            <Th>Version</Th>
            <Th>State</Th>
            <Th align="right">Improved</Th>
            <Th align="right">Mean change</Th>
            <Th align="right">Saved per 1M calls</Th>
            <Th align="right">LLM cost</Th>
          </tr>
        </THead>
        <TBody className="[&>tr:last-child]:border-0">
          {runs.map((run) => {
            const state = runState(run.state)
            const accepted = run.counts_by_outcome.accepted ?? 0
            return (
              <Tr key={run.run_id} interactive>
                <Td className="whitespace-nowrap">
                  <Link
                    to={storedReplayHref(projectId, run.run_id)}
                    aria-label={`Replay the run from ${formatWhen(run.created_ts)}`}
                    className="text-ink after:absolute after:inset-0 after:content-['']"
                  >
                    {formatWhen(run.created_ts)}
                  </Link>
                </Td>
                <Td className="font-mono text-code text-ink-2">
                  {run.ref ?? shortSha(run.base_sha) ?? NONE}
                  {run.ref !== null && run.base_sha !== null && <span className="text-ink-3"> {shortSha(run.base_sha)}</span>}
                </Td>
                <Td title={run.error ?? undefined}>
                  <Badge tone={state.tone}>{state.text}</Badge>
                  {run.error !== null && <span className="mt-0.5 block max-w-56 truncate text-xs text-ink-3">{run.error}</span>}
                </Td>
                <Td align="right" className="text-sm">
                  {run.functions_total === 0 ? (
                    <span className="text-ink-3">{NONE}</span>
                  ) : (
                    <>
                      <span className={accepted > 0 ? 'font-medium text-accent' : 'text-ink'}>{accepted}</span>
                      <span className="text-ink-3"> of {run.functions_total}</span>
                    </>
                  )}
                </Td>
                <Td align="right" className="text-sm text-ink-2">
                  {formatPct(run.mean_reduction_pct)}
                </Td>
                <Td align="right" className="text-sm text-ink-2">
                  {run.g_saved_per_1m_calls > 0 ? formatGrams(run.g_saved_per_1m_calls) : NONE}
                </Td>
                <Td align="right" muted className="text-sm">
                  {formatUsd(run.llm_cost_usd)}
                </Td>
              </Tr>
            )
          })}
        </TBody>
      </Table>
    </div>
  )
}
