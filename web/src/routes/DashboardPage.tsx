// /: what the user's own runs have saved, then what is running and what changed lately.
import { useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { useHealth, useRunDetails, useRuns } from '../api/queries'
import { Co2 } from '../components/Co2'
import { PageHeader } from '../components/PageHeader'
import type { FunctionRecord, RunDetail, RunSummary } from '../gen/events'
import { formatGrams, formatPct, formatWhen, NONE } from '../lib/format'
import { isActive, progressText, repoOf, runValue, selectImpact, TONE_CLASS, type Impact } from '../lib/runs'
import { useTitle } from '../lib/useTitle'
import { Button, buttonClass } from '../ui/Button'
import { Delayed } from '../ui/Delayed'
import { Disclosure } from '../ui/Disclosure'
import { EmptyState } from '../ui/EmptyState'
import { Field } from '../ui/Field'
import { InlineNotice } from '../ui/InlineNotice'
import { Input } from '../ui/Input'
import { Page } from '../ui/Page'
import { Table, TBody, Td, Tr } from '../ui/Table'

const DETAILS = 6
const IMPROVEMENTS = 6
const RECENT = 5

const ROW_LINK = "after:absolute after:inset-0 after:content-['']"
const QUIET_LINK = 'text-base text-ink-2 underline underline-offset-2 hover:text-ink'

const runHref = (id: string) => `/runs/${encodeURIComponent(id)}`
const count = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

export function DashboardPage() {
  useTitle(null)
  const runs = useRuns()
  const offline = useHealth().isError
  const impact = selectImpact(runs.data ?? [])
  const details = useRunDetails(impact.runs.slice(0, DETAILS))

  let body: ReactNode
  if (runs.data !== undefined) {
    body =
      runs.data.length === 0 ? (
        <EmptyState
          title="No runs yet"
          body="Start a run on a GitHub repository, or watch a recorded one first."
          actions={
            <>
              <Link to="/new" className={buttonClass('primary', 'md')}>
                Start a run
              </Link>
              <Link to="/replays" className={buttonClass('secondary', 'md')}>
                Watch a replay
              </Link>
            </>
          }
        />
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
    <Page>
      <PageHeader title="Dashboard" />
      {/* a reading column: each row's numbers stay close to its name on wide screens */}
      <div className="max-w-[760px]">{body}</div>
    </Page>
  )
}

function Overview({ runs, impact, details }: { runs: RunSummary[]; impact: Impact; details: RunDetail[] }) {
  const active = runs.find(isActive)
  const recent = [...runs].sort((a, b) => b.created_ts - a.created_ts).slice(0, RECENT)
  const improvements = details
    .flatMap((d) => d.functions.filter((f) => f.outcome === 'accepted').map((f) => ({ run: d, fn: f })))
    .sort((a, b) => (b.fn.g_saved_per_1m_calls ?? 0) - (a.fn.g_saved_per_1m_calls ?? 0))
    .slice(0, IMPROVEMENTS)

  return (
    <>
      {impact.functions > 0 ? (
        <Total impact={impact} />
      ) : (
        <div>
          <p className="text-base text-ink-2">No improvements from your own repositories yet</p>
          {/* while a run is active, "Running now" is the next step and the form would be disabled */}
          {active === undefined && (
            <Link to="/new" className={`mt-2 inline-block ${QUIET_LINK}`}>
              Start a run
            </Link>
          )}
        </div>
      )}

      {active !== undefined && (
        <Section title="Running now">
          <Link
            to={runHref(active.id)}
            className="flex items-center justify-between gap-4 rounded-lg border border-line bg-surface px-4 py-3 transition-colors duration-150 hover:bg-hover"
          >
            <span className="min-w-0 truncate text-base text-ink">{repoOf(active).name}</span>
            <span className="shrink-0 text-sm text-ink-2">{progressText(active)}</span>
          </Link>
        </Section>
      )}

      {improvements.length > 0 && (
        <Section title="Recent improvements">
          <Table>
            <TBody>
              {improvements.map(({ run, fn }) => (
                <Improvement key={`${run.id}:${fn.function_id}`} run={run} fn={fn} />
              ))}
            </TBody>
          </Table>
        </Section>
      )}

      <Section title="Recent runs">
        <Table>
          <TBody>
            {recent.map((r) => {
              const repo = repoOf(r)
              const value = runValue(r)
              return (
                <Tr key={r.id} interactive>
                  <Td className="[overflow-wrap:anywhere]">
                    <Link to={runHref(r.id)} className={`text-ink ${ROW_LINK}`}>
                      {repo.name}
                    </Link>
                    {repo.owner !== null && <span className="ml-2 hidden text-sm text-ink-3 sm:inline">{repo.owner}</span>}
                  </Td>
                  <Td className="whitespace-nowrap text-sm text-ink-2">{formatWhen(r.created_ts)}</Td>
                  <Td align="right" className={`whitespace-nowrap text-sm ${TONE_CLASS[value.tone]}`}>
                    {value.text}
                  </Td>
                </Tr>
              )
            })}
          </TBody>
        </Table>
        <Link to="/runs" className={`mt-3 inline-block ${QUIET_LINK}`}>
          All runs
        </Link>
      </Section>
    </>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mt-10">
      <h2 className="mb-3 text-md font-medium text-ink">{title}</h2>
      {children}
    </section>
  )
}

function Total({ impact }: { impact: Impact }) {
  const [calls, setCalls] = useState('1000000')
  const perYear = Number(calls.replace(/[\s,_]/g, ''))
  const valid = calls.trim() !== '' && Number.isFinite(perYear) && perYear >= 0

  return (
    <div>
      <p className="text-xl font-normal tabular-nums text-ink">
        {formatGrams(impact.grams)} <Co2 />e
      </p>
      <p className="mt-1 text-sm text-ink-2">saved per 1M calls of each improved function</p>
      <p className="mt-3 text-base text-ink-2">
        From {count(impact.functions, 'function', 'functions')} improved in{' '}
        {count(impact.repositories, 'repository', 'repositories')}
      </p>
      {impact.excludedDemo > 0 && <p className="mt-1 text-xs text-ink-3">Demo runs are not counted</p>}
      <Disclosure className="mt-4" summary="What does this mean?">
        <div className="max-w-[560px] space-y-3 text-sm text-ink-2">
          <p>
            Each improved function now uses less energy per call; this adds up their savings as if each ran 1M times.
          </p>
          <Field label="Calls per year for each function" htmlFor="calls-per-year">
            <Input
              id="calls-per-year"
              inputMode="numeric"
              autoComplete="off"
              value={calls}
              onChange={(e) => setCalls(e.target.value)}
              className="max-w-[192px] tabular-nums"
            />
          </Field>
          <p>
            That would avoid{' '}
            <span className="tabular-nums text-ink">{valid ? formatGrams((impact.grams * perYear) / 1e6) : NONE}</span>{' '}
            <Co2 />e a year
          </p>
        </div>
      </Disclosure>
    </div>
  )
}

function Improvement({ run, fn }: { run: RunDetail; fn: FunctionRecord }) {
  const g = fn.g_saved_per_1m_calls
  return (
    <Tr interactive>
      <Td className="[overflow-wrap:anywhere]">
        <Link
          to={`${runHref(run.id)}?tab=changes&fn=${encodeURIComponent(fn.function_id)}`}
          className={`text-ink ${ROW_LINK}`}
        >
          {fn.qualname}
        </Link>
        <span className="ml-2 text-sm text-ink-3">{repoOf(run).name}</span>
      </Td>
      <Td align="right" className="text-accent">
        {formatPct(fn.delta_pct)}
      </Td>
      <Td align="right" className="hidden whitespace-nowrap text-sm text-ink-2 sm:table-cell">
        {g === null ? NONE : `${formatGrams(g)} per 1M`}
      </Td>
    </Tr>
  )
}
