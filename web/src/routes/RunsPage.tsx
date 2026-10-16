// /runs: every run, filtered by repository and run type through the URL.
import { useEffect, type ReactNode } from 'react'
import { Link, useLocation, useSearchParams } from 'react-router'
import { useHealth, useRuns, useShowcaseKind } from '../api/queries'
import { Co2 } from '../components/Co2'
import { PageHeader } from '../components/PageHeader'
import type { RunSummary } from '../gen/events'
import { formatGrams, formatPct, formatWhen, NONE } from '../lib/format'
import { filterRuns, isTerminal, repoOf, runStatus, runValue, TONE_CLASS, type RunFilterMode } from '../lib/runs'
import { useTitle } from '../lib/useTitle'
import { SHOWCASE, SHOWCASE_RUNS, startLabel } from '../showcase/config'
import { Badge } from '../ui/Badge'
import { Button, buttonClass } from '../ui/Button'
import { Card } from '../ui/Card'
import { Delayed } from '../ui/Delayed'
import { EmptyState } from '../ui/EmptyState'
import { Icon } from '../ui/Icon'
import { InlineNotice } from '../ui/InlineNotice'
import { Input } from '../ui/Input'
import { Page } from '../ui/Page'
import { Segmented } from '../ui/Segmented'
import { Table, TBody, Td, Th, THead, Tr } from '../ui/Table'

const MODES = [
  { value: 'all', label: 'All' },
  { value: 'live', label: 'Live' },
  { value: 'demo', label: 'Demo' },
] as const satisfies readonly { value: RunFilterMode; label: string }[]

const parseMode = (v: string | null): RunFilterMode => (v === 'live' || v === 'demo' ? v : 'all')

const runHref = (id: string) => `/runs/${encodeURIComponent(id)}`
const accepted = (r: RunSummary) => r.counts_by_outcome.accepted ?? 0

export function RunsPage() {
  useTitle('Runs')
  const runs = useRuns()
  const offline = useHealth().isError
  const kind = useShowcaseKind()
  const location = useLocation()
  const [params, setParams] = useSearchParams()
  const q = params.get('q') ?? ''
  const mode = parseMode(params.get('mode'))
  const hasRuns = (runs.data?.length ?? 0) > 0

  // `/` from another page lands here asking for the filter; it exists only once runs have loaded
  const focusFilter = (location.state as { focusFilter?: boolean } | null)?.focusFilter === true
  useEffect(() => {
    if (focusFilter && hasRuns) document.getElementById('runs-filter')?.focus()
  }, [focusFilter, hasRuns])

  /** Each param is dropped while it holds its default, so a plain `/runs` stays plain. */
  const setParam = (name: 'q' | 'mode', value: string, fallback: string) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (value === fallback) next.delete(name)
        else next.set(name, value)
        return next
      },
      { replace: true },
    )

  let body: ReactNode
  if (runs.data !== undefined) {
    const shown = filterRuns(runs.data, q, mode)
    body = !hasRuns ? (
      <Card>
        <EmptyState
          title="No runs yet"
          body="Each run you start shows up here, with what it changed and what it saved."
          actions={
            <>
              <Link to="/new" className={buttonClass('primary', 'md')}>
                {startLabel('Start a run')}
              </Link>
              <Link to="/replays" className={buttonClass('secondary', 'md')}>
                Watch a replay
              </Link>
            </>
          }
        />
      </Card>
    ) : (
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-3">
          <div className="relative w-full sm:w-64">
            <Icon name="search" className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-3" />
            <Input
              id="runs-filter"
              type="search"
              placeholder="Filter by repository"
              aria-label="Filter by repository"
              autoComplete="off"
              value={q}
              onChange={(e) => setParam('q', e.target.value, '')}
              className="pl-8"
            />
          </div>
          <Segmented label="Run type" options={MODES} value={mode} onChange={(m) => setParam('mode', m, 'all')} />
        </div>
        {shown.length === 0 ? (
          <EmptyState
            title={q.trim() !== '' ? `No runs match “${q.trim()}”` : mode === 'live' ? 'No live runs' : 'No demo runs'}
            actions={
              <Button variant="secondary" onClick={() => setParams({}, { replace: true })}>
                Clear filter
              </Button>
            }
          />
        ) : (
          <>
            <RunTable runs={shown} />
            <RunList runs={shown} />
            <p className="border-t border-line px-5 py-3 text-sm text-ink-3">
              {shown.length === runs.data.length
                ? `${runs.data.length} ${runs.data.length === 1 ? 'run' : 'runs'}`
                : `${shown.length} of ${runs.data.length} runs`}
            </p>
          </>
        )}
      </Card>
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
      <PageHeader
        title="Runs"
        meta={SHOWCASE ? `Every one of the ${kind === undefined ? 'runs on this site' : SHOWCASE_RUNS[kind]}, newest first` : 'Every run on this server, newest first'}
        actions={
          <Link to="/new" className={buttonClass('primary', 'md')}>
            <Icon name="plus" />
            {startLabel('New run')}
          </Link>
        }
      />
      {body}
    </Page>
  )
}

function RunTable({ runs }: { runs: RunSummary[] }) {
  return (
    <div className="hidden px-5 md:block">
      <Table>
        <THead>
          <tr>
            <Th>Repository</Th>
            <Th>Started</Th>
            <Th>State</Th>
            <Th align="right">Functions improved</Th>
            <Th align="right">Reduction</Th>
            <Th align="right">
              <Co2 />e saved / 1M
            </Th>
          </tr>
        </THead>
        <TBody className="[&>tr:last-child]:border-0">
          {runs.map((r) => {
            const repo = repoOf(r)
            const status = runStatus(r)
            const won = accepted(r) >= 1
            return (
              <Tr key={r.id} interactive>
                {/* break-words, not anywhere: `anywhere` drops the min-content width and the column collapses first */}
                <Td className="h-14 break-words">
                  <span className="flex items-center gap-3">
                    <Initial name={repo.name} />
                    <span className="min-w-0">
                      <Link to={runHref(r.id)} className="block text-ink after:absolute after:inset-0 after:content-['']">
                        {repo.name}
                      </Link>
                      <span className="block text-xs text-ink-3">{repo.owner ?? 'Bundled demo'}</span>
                    </span>
                  </span>
                </Td>
                <Td className="whitespace-nowrap text-sm text-ink-2">{formatWhen(r.created_ts)}</Td>
                <Td className="whitespace-nowrap">
                  <Badge tone={status.tone}>{status.label}</Badge>
                </Td>
                {/* a run that ended before choosing has no functions, which is not "0 of 0" */}
                <Td align="right" muted={!(isTerminal(r.state) && r.functions_total > 0)}>
                  {isTerminal(r.state) && r.functions_total > 0 ? `${accepted(r)} of ${r.functions_total}` : NONE}
                </Td>
                <Td align="right" className={won ? 'font-medium text-accent' : 'text-ink-3'}>
                  {won ? formatPct(r.mean_reduction_pct) : NONE}
                </Td>
                <Td align="right" className={won ? 'text-ink-2' : 'text-ink-3'}>
                  {won ? formatGrams(r.g_saved_per_1m_calls) : NONE}
                </Td>
              </Tr>
            )
          })}
        </TBody>
      </Table>
    </div>
  )
}

/** Below 768px the six columns become two quiet lines per run. */
function RunList({ runs }: { runs: RunSummary[] }) {
  return (
    <ul className="px-2 py-1 md:hidden">
      {runs.map((r) => {
        const repo = repoOf(r)
        const value = runValue(r)
        const status = runStatus(r)
        return (
          <li key={r.id}>
            <Link to={runHref(r.id)} className="flex items-center gap-3 rounded-lg px-3 py-2.5 hover:bg-hover">
              <Initial name={repo.name} />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-base text-ink">{repo.name}</span>
                <span className="block truncate text-xs text-ink-3">
                  {repo.owner ?? 'Bundled demo'} · {formatWhen(r.created_ts)}
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
  )
}

function Initial({ name }: { name: string }) {
  return (
    <span
      aria-hidden="true"
      className="grid size-8 shrink-0 place-items-center rounded-lg bg-hover text-sm font-medium uppercase text-ink-2"
    >
      {name.charAt(0)}
    </span>
  )
}
