// /runs: every run, filtered by repository and run type through the URL.
import { useEffect, type ReactNode } from 'react'
import { Link, useLocation, useSearchParams } from 'react-router'
import { useHealth, useRuns } from '../api/queries'
import { Co2 } from '../components/Co2'
import { PageHeader } from '../components/PageHeader'
import type { RunSummary } from '../gen/events'
import { formatGrams, formatPct, formatWhen, NONE } from '../lib/format'
import { filterRuns, isTerminal, repoOf, runValue, stateLabel, TONE_CLASS, type RunFilterMode } from '../lib/runs'
import { useTitle } from '../lib/useTitle'
import { Button, buttonClass } from '../ui/Button'
import { Delayed } from '../ui/Delayed'
import { EmptyState } from '../ui/EmptyState'
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
      <EmptyState
        title="No runs yet"
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
      <>
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <Input
            id="runs-filter"
            type="search"
            placeholder="Filter by repository"
            aria-label="Filter by repository"
            autoComplete="off"
            value={q}
            onChange={(e) => setParam('q', e.target.value, '')}
            className="sm:w-64"
          />
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
          </>
        )}
      </>
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
      <PageHeader title="Runs" />
      {body}
    </Page>
  )
}

function RunTable({ runs }: { runs: RunSummary[] }) {
  return (
    <div className="hidden md:block">
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
        <TBody>
          {runs.map((r) => {
            const repo = repoOf(r)
            const won = accepted(r) >= 1
            return (
              <Tr key={r.id} interactive>
                {/* break-words, not anywhere: `anywhere` drops the min-content width and the column collapses first */}
                <Td className="break-words">
                  <Link to={runHref(r.id)} className="text-ink after:absolute after:inset-0 after:content-['']">
                    {repo.name}
                  </Link>
                  {repo.owner !== null && <span className="ml-2 text-sm text-ink-3">{repo.owner}</span>}
                </Td>
                <Td className="whitespace-nowrap text-sm text-ink-2">{formatWhen(r.created_ts)}</Td>
                <Td className={`whitespace-nowrap ${r.state === 'failed' ? 'text-fail' : 'text-ink-2'}`}>{stateLabel(r.state)}</Td>
                {/* a run that ended before choosing has no functions, which is not "0 of 0" */}
                <Td align="right" muted={!(isTerminal(r.state) && r.functions_total > 0)}>
                  {isTerminal(r.state) && r.functions_total > 0 ? `${accepted(r)} of ${r.functions_total}` : NONE}
                </Td>
                <Td align="right" className={won ? 'text-accent' : 'text-ink-3'}>
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
    <ul className="divide-y divide-line md:hidden">
      {runs.map((r) => {
        const repo = repoOf(r)
        const value = runValue(r)
        return (
          <li key={r.id}>
            <Link to={runHref(r.id)} className="-mx-3 flex flex-col gap-0.5 rounded-lg px-3 py-3 hover:bg-hover">
              <span className="flex items-baseline justify-between gap-3">
                <span className="min-w-0 truncate text-base text-ink">{repo.name}</span>
                <span className={`shrink-0 text-sm ${TONE_CLASS[value.tone]}`}>{value.text}</span>
              </span>
              <span className="text-xs text-ink-3">
                {repo.owner ?? 'Demo'} · {formatWhen(r.created_ts)} · {stateLabel(r.state)}
              </span>
            </Link>
          </li>
        )
      })}
    </ul>
  )
}
