// /dashboard: what the user's own runs have saved, what is running now, and what changed lately.
import { useId, useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { useHealth, useRunDetails, useRuns, useShowcaseKind } from '../api/queries'
import { Co2 } from '../components/Co2'
import { PageHeader } from '../components/PageHeader'
import type { FunctionRecord, RunDetail, RunSummary } from '../gen/events'
import { formatAgo, formatGrams, formatPct, NONE } from '../lib/format'
import {
  isActive,
  outcomeTotals,
  progressText,
  repoOf,
  runStatus,
  runValue,
  selectImpact,
  stageIndex,
  STAGES,
  TONE_CLASS,
  type Impact,
  type OutcomeGroup,
} from '../lib/runs'
import { useTitle } from '../lib/useTitle'
import { SHOWCASE, SHOWCASE_RUNS, startLabel } from '../showcase/config'
import { Badge } from '../ui/Badge'
import { Button, buttonClass } from '../ui/Button'
import { Card, CardHeader } from '../ui/Card'
import { Delayed } from '../ui/Delayed'
import { Icon, type IconName } from '../ui/Icon'
import { InlineNotice } from '../ui/InlineNotice'
import { Input } from '../ui/Input'
import { Page } from '../ui/Page'
import { Table, TBody, Td, Th, THead, Tr } from '../ui/Table'

const DETAILS = 6
const IMPROVEMENTS = 6
const RECENT = 5
const REPOSITORIES = 6

const ROW_LINK = "after:absolute after:inset-0 after:content-['']"

const runHref = (id: string) => `/runs/${encodeURIComponent(id)}`
const count = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

export function DashboardPage() {
  useTitle('Dashboard')
  const runs = useRuns()
  const offline = useHealth().isError
  const kind = useShowcaseKind()
  const impact = selectImpact(runs.data ?? [])
  const details = useRunDetails(impact.runs.slice(0, DETAILS))

  let body: ReactNode
  if (runs.data !== undefined) {
    body =
      runs.data.length === 0 ? (
        <GetStarted />
      ) : (
        <Overview
          runs={runs.data}
          impact={impact}
          details={details.flatMap((d) => (d.data === undefined ? [] : [d.data]))}
        />
      )
  } else if (runs.isError) {
    // offline, the header's status line says so instead
    body = offline ? null : (
      <InlineNotice
        tone="fail"
        action={
          <Button size="sm" onClick={() => void runs.refetch()}>
            Retry
          </Button>
        }
      >
        Can’t load runs
      </InlineNotice>
    )
  } else {
    body = (
      <Delayed>
        <p className="text-base text-ink-3">Loading</p>
      </Delayed>
    )
  }

  return (
    <Page width="wide">
      <PageHeader
        title="Dashboard"
        meta={
          SHOWCASE
            ? `What net zero saved in the ${kind === undefined ? 'runs on this site' : SHOWCASE_RUNS[kind]}`
            : 'What net zero has saved in your repositories, and what it is working on'
        }
        actions={
          <>
            <Link to="/replays" className={buttonClass('secondary', 'md')}>
              <Icon name="replays" className="text-ink-3" />
              Replays
            </Link>
            <Link to="/new" className={buttonClass('primary', 'md')}>
              <Icon name="plus" />
              {startLabel('New run')}
            </Link>
          </>
        }
      />
      {body}
    </Page>
  )
}

function Overview({ runs, impact, details }: { runs: RunSummary[]; impact: Impact; details: RunDetail[] }) {
  const active = runs.find(isActive)
  const recent = [...runs].sort((a, b) => b.created_ts - a.created_ts).slice(0, RECENT)
  const counted = impact.runs.flatMap((id) => runs.filter((r) => r.id === id))
  const improvements = details
    .flatMap((d) => d.functions.filter((f) => f.outcome === 'accepted').map((f) => ({ run: d, fn: f })))
    .sort((a, b) => (b.fn.g_saved_per_1m_calls ?? 0) - (a.fn.g_saved_per_1m_calls ?? 0))
    .slice(0, IMPROVEMENTS)
  const finished = runs.filter((r) => r.state === 'completed').length
  const running = runs.filter(isActive).length

  return (
    <div className="space-y-6">
      {active !== undefined && <RunningNow run={active} />}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          featured
          icon="leaf"
          label={
            <>
              <Co2 />e saved
            </>
          }
          value={impact.functions > 0 ? formatGrams(impact.grams) : NONE}
          sub="per 1M calls of each improved function"
          note={impact.excludedDemo > 0 ? 'Demo runs are not counted' : undefined}
        />
        <Stat
          icon="code"
          label="Functions improved"
          value={String(impact.functions)}
          sub={`in ${count(impact.repositories, 'repository', 'repositories')}`}
        />
        <Stat
          icon="trend-down"
          label="Average reduction"
          value={formatPct(impact.meanPct)}
          sub={
            <>
              <Co2 />e per call, across improved functions
            </>
          }
        />
        <Stat
          icon="runs"
          label="Runs"
          value={String(runs.length)}
          sub={running > 0 ? `${running} running, ${finished} completed` : `${finished} completed`}
        />
      </div>

      <div className="grid items-start gap-6 lg:grid-cols-3">
        <div className="min-w-0 space-y-6 lg:col-span-2">
          {impact.functions > 0 ? (
            <SavingsByRepository runs={counted} />
          ) : (
            <NothingYet showStart={active === undefined} />
          )}
          {improvements.length > 0 && <TopImprovements rows={improvements} />}
        </div>
        <div className="min-w-0 space-y-6">
          <RecentRuns runs={recent} />
          <Outcomes runs={counted} />
          {impact.functions > 0 && <YearlyEstimate grams={impact.grams} />}
        </div>
      </div>
    </div>
  )
}

function Stat({
  icon,
  label,
  value,
  sub,
  note,
  featured = false,
}: {
  icon: IconName
  label: ReactNode
  value: string
  sub: ReactNode
  note?: string | undefined
  featured?: boolean
}) {
  const id = useId()
  return (
    <Card
      aria-labelledby={id}
      className={`p-5 ${featured ? 'border-accent-soft bg-gradient-to-br from-accent-tint to-surface' : ''}`}
    >
      <div className="flex items-start justify-between gap-3">
        <h2 id={id} className="text-sm font-medium text-ink-2">
          {label}
        </h2>
        <span
          className={`grid size-8 shrink-0 place-items-center rounded-lg ${featured ? 'bg-accent text-white' : 'bg-hover text-ink-2'}`}
        >
          <Icon name={icon} />
        </span>
      </div>
      <p className="mt-2 text-xl font-semibold tabular-nums tracking-tight text-ink">{value}</p>
      <p className="mt-1 text-sm text-ink-3">{sub}</p>
      {note !== undefined && <p className="mt-3 text-xs text-ink-3">{note}</p>}
    </Card>
  )
}

function RunningNow({ run }: { run: RunSummary }) {
  const repo = repoOf(run)
  const status = runStatus(run)
  const stage = stageIndex(run.state)
  const optimizing = run.state === 'optimizing' && run.functions_total > 0
  const done = optimizing ? Math.min(100, (run.functions_done / run.functions_total) * 100) : 0

  return (
    <section>
      <h2 className="sr-only">Running now</h2>
      <Link
        to={runHref(run.id)}
        className="block rounded-xl border border-accent-soft bg-surface p-5 shadow-card transition-shadow duration-150 hover:shadow-lift"
      >
        <span className="flex flex-wrap items-start justify-between gap-4">
          <span className="min-w-0">
            <span className="flex items-center gap-2">
              <Badge tone={status.tone}>{run.state === 'awaiting_selection' ? status.label : 'Running now'}</Badge>
              <span className="text-sm text-ink-3">started {formatAgo(run.created_ts)}</span>
            </span>
            <span className="mt-2 block truncate text-md font-medium text-ink">{repo.full}</span>
            <span className="mt-0.5 block text-sm text-ink-2">{progressText(run)}</span>
          </span>
          <span className={buttonClass('secondary', 'sm')}>
            Open run
            <Icon name="arrow-right" size={14} />
          </span>
        </span>
        <ol className="mt-5 grid grid-cols-4 gap-2" aria-label="Stages">
          {STAGES.map((label, i) => (
            <li key={label} className="min-w-0" aria-current={i === stage ? 'step' : undefined}>
              <span className="block h-1.5 overflow-hidden rounded-full bg-hover">
                {i < stage && <span className="block h-full bg-accent" />}
                {i === stage &&
                  (optimizing ? (
                    <span className="block h-full rounded-full bg-accent" style={{ width: `${done}%` }} />
                  ) : (
                    <span className="block h-full bg-accent-soft motion-safe:animate-breathe" />
                  ))}
              </span>
              <span className={`mt-1.5 block truncate text-xs ${i <= stage ? 'text-ink' : 'text-ink-3'}`}>{label}</span>
            </li>
          ))}
        </ol>
      </Link>
    </section>
  )
}

function SavingsByRepository({ runs }: { runs: RunSummary[] }) {
  const rows = runs
    .filter((r) => r.g_saved_per_1m_calls > 0)
    .sort((a, b) => b.g_saved_per_1m_calls - a.g_saved_per_1m_calls)
    .slice(0, REPOSITORIES)
  const max = rows[0]?.g_saved_per_1m_calls ?? 1

  return (
    <Card>
      <CardHeader
        title="Savings by repository"
        description={
          <>
            <Co2 />e saved per 1M calls, from the latest completed run of each repository
          </>
        }
      />
      <ul className="space-y-1 px-2 pb-3">
        {rows.map((r) => {
          const repo = repoOf(r)
          const improved = r.counts_by_outcome.accepted ?? 0
          return (
            <li key={r.id}>
              <Link to={runHref(r.id)} className="block rounded-lg px-3 py-2.5 transition-colors duration-150 hover:bg-hover">
                <span className="flex items-baseline justify-between gap-4">
                  <span className="min-w-0 truncate text-base text-ink">
                    {repo.name}
                    {repo.owner !== null && <span className="ml-2 text-sm text-ink-3">{repo.owner}</span>}
                  </span>
                  <span className="shrink-0 text-sm tabular-nums text-ink-2">
                    <span className="font-medium text-ink">{formatGrams(r.g_saved_per_1m_calls)}</span>
                    <span className="ml-2 hidden sm:inline">{count(improved, 'function', 'functions')}</span>
                  </span>
                </span>
                <span className="mt-2 block h-2 overflow-hidden rounded-full bg-hover">
                  <span
                    className="block h-full rounded-full bg-accent"
                    style={{ width: `${Math.max(2, (r.g_saved_per_1m_calls / max) * 100)}%` }}
                  />
                </span>
              </Link>
            </li>
          )
        })}
      </ul>
    </Card>
  )
}

function NothingYet({ showStart }: { showStart: boolean }) {
  return (
    <Card className="px-5 py-10 text-center">
      <span className="mx-auto grid size-10 place-items-center rounded-xl bg-accent-tint text-accent">
        <Icon name="leaf" size={20} />
      </span>
      <h2 className="mt-4 text-md font-medium text-ink">
        {SHOWCASE ? 'No improvements in these runs' : 'No improvements from your own repositories yet'}
      </h2>
      <p className="mx-auto mt-1 max-w-[420px] text-pretty text-base text-ink-2">
        {SHOWCASE
          ? 'Savings count only runs on GitHub repositories. Run net zero on your own code to see yours.'
          : 'When a run on one of your GitHub repositories finds a faster version of a function, its savings show up here.'}
      </p>
      {/* while a run is active, "Running now" is the next step and the form would be disabled */}
      {showStart && (
        <div className="mt-5 flex justify-center gap-2">
          <Link to="/new" className={buttonClass('primary', 'md')}>
            {startLabel('Start a run')}
          </Link>
        </div>
      )}
    </Card>
  )
}

function TopImprovements({ rows }: { rows: { run: RunDetail; fn: FunctionRecord }[] }) {
  return (
    <Card>
      <CardHeader title="Top improvements" description="The functions with the biggest savings in your latest runs" />
      <div className="px-5 pb-2">
        <Table>
          <THead>
            <tr>
              <Th>Function</Th>
              <Th align="right">Per call</Th>
              <Th align="right" className="hidden sm:table-cell">
                Saved per 1M calls
              </Th>
            </tr>
          </THead>
          <TBody className="[&>tr:last-child]:border-0">
            {rows.map(({ run, fn }) => (
              <Improvement key={`${run.id}:${fn.function_id}`} run={run} fn={fn} />
            ))}
          </TBody>
        </Table>
      </div>
    </Card>
  )
}

function Improvement({ run, fn }: { run: RunDetail; fn: FunctionRecord }) {
  const g = fn.g_saved_per_1m_calls
  return (
    <Tr interactive>
      <Td className="[overflow-wrap:anywhere]">
        <Link
          to={`${runHref(run.id)}?tab=changes&fn=${encodeURIComponent(fn.function_id)}`}
          className={`font-mono text-code text-ink ${ROW_LINK}`}
        >
          {fn.qualname}
        </Link>
        <span className="ml-2 text-sm text-ink-3">{repoOf(run).name}</span>
      </Td>
      <Td align="right">
        <Badge tone="success">{formatPct(fn.delta_pct)}</Badge>
      </Td>
      <Td align="right" className="hidden whitespace-nowrap text-sm text-ink-2 sm:table-cell">
        {g === null ? NONE : `${formatGrams(g)} per 1M`}
      </Td>
    </Tr>
  )
}

function RecentRuns({ runs }: { runs: RunSummary[] }) {
  return (
    <Card>
      <CardHeader
        title="Recent runs"
        action={
          <Link to="/runs" className="text-sm text-ink-2 hover:text-ink">
            All runs
          </Link>
        }
      />
      <ul className="px-2 pb-2">
        {runs.map((r) => {
          const repo = repoOf(r)
          const status = runStatus(r)
          const value = runValue(r)
          return (
            <li key={r.id}>
              <Link
                to={runHref(r.id)}
                className="flex items-center gap-3 rounded-lg px-3 py-2 transition-colors duration-150 hover:bg-hover"
              >
                <span
                  aria-hidden="true"
                  className="grid size-8 shrink-0 place-items-center rounded-lg bg-hover text-sm font-medium uppercase text-ink-2"
                >
                  {repo.name.charAt(0)}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-base text-ink">{repo.name}</span>
                  <span className="block truncate text-xs text-ink-3">
                    {repo.owner ?? 'Bundled demo'} · {formatAgo(r.created_ts)}
                  </span>
                </span>
                {r.state === 'completed' ? (
                  <span className={`shrink-0 text-sm tabular-nums ${TONE_CLASS[value.tone]}`}>{value.text}</span>
                ) : (
                  <Badge tone={status.tone}>{status.label}</Badge>
                )}
              </Link>
            </li>
          )
        })}
      </ul>
    </Card>
  )
}

const OUTCOMES: { key: OutcomeGroup; label: string; color: string }[] = [
  { key: 'improved', label: 'Improved', color: 'bg-accent' },
  { key: 'no_win', label: 'No significant win', color: 'bg-control' },
  { key: 'rejected', label: 'Rewrites rejected', color: 'bg-fail-soft' },
  { key: 'skipped', label: 'Skipped', color: 'bg-caution-soft' },
  { key: 'stopped', label: 'Failed or cancelled', color: 'bg-ink-3' },
]

function Outcomes({ runs }: { runs: RunSummary[] }) {
  const totals = outcomeTotals(runs)
  const total = OUTCOMES.reduce((sum, o) => sum + totals[o.key], 0)
  if (total === 0) return null
  const shown = OUTCOMES.filter((o) => totals[o.key] > 0)

  return (
    <Card>
      <CardHeader title="Function outcomes" description="Every function tried in the runs counted above" />
      <div className="px-5 pb-5">
        <div className="flex h-2.5 gap-0.5 overflow-hidden rounded-full">
          {shown.map((o) => (
            <span key={o.key} className={o.color} style={{ width: `${(totals[o.key] / total) * 100}%` }} />
          ))}
        </div>
        <ul className="mt-4 space-y-2">
          {shown.map((o) => (
            <li key={o.key} className="flex items-center gap-2.5 text-sm">
              <span aria-hidden="true" className={`size-2 rounded-full ${o.color}`} />
              <span className="text-ink-2">{o.label}</span>
              <span className="ml-auto tabular-nums text-ink">{totals[o.key]}</span>
            </li>
          ))}
        </ul>
      </div>
    </Card>
  )
}

function YearlyEstimate({ grams }: { grams: number }) {
  const [calls, setCalls] = useState('1000000')
  const perYear = Number(calls.replace(/[\s,_]/g, ''))
  const valid = calls.trim() !== '' && Number.isFinite(perYear) && perYear >= 0
  const id = useId()

  return (
    <Card aria-labelledby={id}>
      <CardHeader
        title={<span id={id}>Yearly estimate</span>}
        description="Each improved function now uses less energy per call. Say how often each one runs."
      />
      <div className="space-y-4 px-5 pb-5">
        <div className="flex flex-col gap-1.5">
          <label htmlFor="calls-per-year" className="text-sm font-medium text-ink">
            Calls per year for each function
          </label>
          <Input
            id="calls-per-year"
            inputMode="numeric"
            autoComplete="off"
            value={calls}
            onChange={(e) => setCalls(e.target.value)}
            className="tabular-nums"
          />
        </div>
        <div className="rounded-lg bg-accent-tint px-4 py-3">
          <p className="text-sm text-ink-2">That would avoid</p>
          <p className="mt-0.5 text-ink">
            <span className="text-lg font-semibold tabular-nums tracking-tight">
              {valid ? formatGrams((grams * perYear) / 1e6) : NONE}
            </span>{' '}
            <span className="text-sm text-ink-2">
              <Co2 />e a year
            </span>
          </p>
        </div>
      </div>
    </Card>
  )
}

const FIRST_STEPS: { icon: IconName; title: string; body: string }[] = [
  {
    icon: 'replays',
    title: 'Watch a replay',
    body: 'See a whole run play back, from finding functions to the final patch. Needs nothing set up.',
  },
  {
    icon: 'plus',
    title: 'Start a run',
    body: 'Paste a public GitHub repository. Live runs need an Anthropic API key; the demo repository doesn’t.',
  },
  {
    icon: 'file',
    title: 'Review the patch',
    body: 'Every kept rewrite comes with its tests and the measurements behind it.',
  },
]

function GetStarted() {
  return (
    <Card className="overflow-hidden">
      <div className="border-b border-line bg-gradient-to-br from-accent-tint to-surface px-6 py-8 sm:px-8">
        <h2 className="text-title font-semibold tracking-tight text-ink">No runs yet</h2>
        <p className="mt-2 max-w-[520px] text-pretty text-md text-ink-2">
          Start with a replay to see what a run looks like, or point net zero at one of your repositories.
        </p>
        <div className="mt-5 flex flex-wrap gap-2">
          <Link to="/new" className={buttonClass('primary', 'md')}>
            {startLabel('Start a run')}
          </Link>
          <Link to="/replays" className={buttonClass('secondary', 'md')}>
            Watch a replay
          </Link>
        </div>
      </div>
      <ol className="grid gap-px bg-line sm:grid-cols-3">
        {FIRST_STEPS.map((s, i) => (
          <li key={s.title} className="bg-surface px-6 py-5">
            <span className="flex items-center gap-2 text-sm text-ink-3">
              <Icon name={s.icon} size={14} />
              Step {i + 1}
            </span>
            <h3 className="mt-2 text-base font-medium text-ink">{s.title}</h3>
            <p className="mt-1 text-pretty text-sm text-ink-2">{s.body}</p>
          </li>
        ))}
      </ol>
    </Card>
  )
}
