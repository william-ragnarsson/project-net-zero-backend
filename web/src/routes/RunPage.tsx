// /runs/:runId follows one run from its event log: hydrate, then SSE until the terminal event.
// The view (finding, choose, optimizing, done) comes from the model alone (run/view.ts).
import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router'
import { useStore } from 'zustand'
import { RUNS_KEY, useRuns } from '../api/queries'
import { PageHeader } from '../components/PageHeader'
import type { RunSource } from '../gen/events'
import { formatClock, formatWhen } from '../lib/format'
import { repoOf } from '../lib/runs'
import { useTitle } from '../lib/useTitle'
import { ChooseView } from '../run/ChooseView'
import { FindingView } from '../run/FindingView'
import { LogTab } from '../run/LogTab'
import { RunActions, type RunNotice } from '../run/RunActions'
import { EndedNotice, RunSummary } from '../run/RunSummary'
import { RunTabs } from '../run/RunTabs'
import { StepIndicator } from '../run/StepIndicator'
import { awaitingSince, CHOOSE_GRACE_MS, pickView, type RunViewKind } from '../run/view'
import type { LiveConnection, LiveStatus } from '../sources/live'
import { useLiveRun } from '../sources/useLiveRun'
import { selectCurrentFunction, selectFunctions, selectTotals } from '../state/selectors'
import type { RunModel } from '../state/types'
import { buttonClass, Button } from '../ui/Button'
import { Delayed } from '../ui/Delayed'
import { Disclosure } from '../ui/Disclosure'
import { InlineNotice } from '../ui/InlineNotice'
import { Page } from '../ui/Page'

const CRUMBS = [{ label: 'Runs', to: '/runs' }]

/** A reconnect shorter than this is not worth a line. */
const RECONNECT_QUIET_MS = 1500

const STEP_OF: Partial<Record<RunViewKind, 0 | 1 | 2>> = { finding: 0, choose: 1, optimizing: 2 }

/**
 * When the choice may show, as display state: set on entering awaiting_selection and cleared on
 * leaving it, with one re-render once the grace is over.
 */
function useAwaitingSince(m: RunModel, hydratedThroughSeq: number): number | null {
  const awaiting = m.state === 'awaiting_selection'
  const [grace, setGrace] = useState<{ awaiting: boolean; since: number | null }>({ awaiting: false, since: null })
  if (grace.awaiting !== awaiting) {
    setGrace({ awaiting, since: awaitingSince(m, hydratedThroughSeq, performance.now()) })
  }
  const since = grace.awaiting === awaiting ? grace.since : null
  const [, setTick] = useState(0)
  useEffect(() => {
    if (since === null || since === -Infinity) return
    const left = CHOOSE_GRACE_MS - (performance.now() - since)
    if (left <= 0) return
    const t = window.setTimeout(() => setTick((n) => n + 1), left + 1)
    return () => window.clearTimeout(t)
  }, [since])
  return since
}

/**
 * The run's elapsed time. While it is live, the server's clock as the stream last showed it
 * (a heartbeat or an event), carried forward here, less the run's start, so a browser clock
 * that is off doesn't matter. Before the stream shows it, the span of event times.
 */
function useRunClock(m: RunModel, conn: LiveConnection | null): number | null {
  const live = m.lastSeq > 0 && m.terminal === null
  const [, setTick] = useState(0)
  useEffect(() => {
    if (!live) return
    let id: number | undefined
    const tick = () => setTick((n) => n + 1)
    const start = () => {
      if (id === undefined && !document.hidden) id = window.setInterval(tick, 1000)
    }
    const stop = () => {
      window.clearInterval(id)
      id = undefined
    }
    const onVisible = () => {
      if (document.hidden) return stop()
      tick()
      start()
    }
    start()
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      stop()
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [live])
  if (m.lastSeq === 0) return null
  const { duration_ms } = selectTotals(m)
  const serverNow = live ? (conn?.serverNow() ?? null) : null
  const createdTs = m.meta?.createdTs ?? null
  return serverNow === null || createdTs === null ? duration_ms : Math.max(duration_ms, serverNow - createdTs)
}

/** True once the stream has been reconnecting (or waiting for the log) for a while. */
function useReconnectLine(status: LiveStatus): boolean {
  const troubled = status.kind === 'reconnecting' || status.kind === 'not_ready'
  const [long, setLong] = useState(false)
  useEffect(() => {
    setLong(false)
    if (!troubled) return
    const t = window.setTimeout(() => setLong(true), RECONNECT_QUIET_MS)
    return () => window.clearTimeout(t)
  }, [troubled])
  return troubled && long
}

function metaLine(source: RunSource, createdTs: number, clockMs: number | null): string {
  const repo = repoOf({ source })
  const parts: string[] = []
  // a demo run's title already says "Demo repository"
  if (source.kind === 'github' && repo.owner !== null) parts.push(repo.full)
  if (source.ref !== null && source.ref !== '') parts.push(source.ref)
  parts.push(`Started ${formatWhen(createdTs)}`)
  if (clockMs !== null) parts.push(formatClock(clockMs))
  return parts.join(' · ')
}

function progressLine(m: RunModel): string {
  const current = selectCurrentFunction(m)
  if (current !== null && current.started !== null) {
    const total = m.selection?.functionIds.length ?? current.started.total
    return `Function ${current.started.index + 1} of ${total} · ${current.qualname}`
  }
  if (m.state === 'finalizing') return 'Writing the patch'
  const t = selectTotals(m)
  return `${t.functions_done} of ${t.functions_total} functions done`
}

export function RunPage() {
  const { runId = '' } = useParams()
  const { session, model: m, epoch, status, conn, retry } = useLiveRun(runId)
  const hydratedThroughSeq = useStore(session, (s) => s.hydratedThroughSeq)
  // the list's entry names the run until its log has loaded
  const cached = useRuns().data?.find((r) => r.id === runId)

  const since = useAwaitingSince(m, hydratedThroughSeq)
  const view = pickView(m, status, since, performance.now())
  const clockMs = useRunClock(m, conn)
  const reconnecting = useReconnectLine(status)

  const [notice, setNotice] = useState<RunNotice | null>(null)
  const [sawChoose, setSawChoose] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const [autoDismissed, setAutoDismissed] = useState(false)
  const onChooseShown = useCallback(() => setSawChoose(true), [])
  const onSubmitted = useCallback(() => setSubmitted(true), [])

  // the sidebar and the runs list pick up each state change without waiting for their poll
  const qc = useQueryClient()
  const ended = m.terminal !== null
  useEffect(() => {
    void qc.invalidateQueries({ queryKey: RUNS_KEY })
  }, [qc, m.state, ended])

  const source = m.meta?.source ?? cached?.source ?? null
  const title = source === null ? null : repoOf({ source }).name
  useTitle(view === 'not_found' ? 'Run not found' : title)

  const anyStarted = useMemo(() => selectFunctions(m).some((f) => f.started !== null), [m])

  if (view === 'not_found') {
    return (
      <Page>
        <PageHeader title="Run not found" meta="There is no run with this id" />
        <Link to="/runs" className={buttonClass('secondary')}>
          All runs
        </Link>
      </Page>
    )
  }

  const inProgress = view === 'finding' || view === 'choose' || view === 'optimizing'
  const step = STEP_OF[view]
  const terminal = m.terminal
  const showTabs = view === 'optimizing' || view === 'done' || (view === 'ended' && anyStarted)
  const showAuto =
    view === 'optimizing' && m.selection?.auto === true && sawChoose && !submitted && !autoDismissed

  return (
    <Page width="wide">
      <PageHeader
        title={title ?? ''}
        crumbs={CRUMBS}
        meta={m.meta === null ? undefined : metaLine(m.meta.source, m.meta.createdTs, clockMs)}
        actions={
          inProgress || view === 'done' || view === 'ended' ? (
            <RunActions view={view} runId={runId} model={m} onNotice={setNotice} />
          ) : undefined
        }
        status={reconnecting ? 'Reconnecting to the live run' : null}
      />
      {inProgress && notice !== null && (
        <div className="mb-6">
          <InlineNotice tone={notice.tone}>{notice.text}</InlineNotice>
        </div>
      )}
      {view === 'loading' && (
        <Delayed>
          <p className="text-base text-ink-3">Loading run</p>
        </Delayed>
      )}
      {view === 'error' && (
        <InlineNotice
          tone="fail"
          action={
            <Button size="sm" onClick={retry}>
              Retry
            </Button>
          }
        >
          Can’t load this run
        </InlineNotice>
      )}
      {step !== undefined && <StepIndicator current={step} />}
      {view === 'done' && terminal?.type === 'run.completed' && <RunSummary summary={terminal.summary} />}
      {view === 'ended' && terminal !== null && <EndedNotice terminal={terminal} />}
      {view === 'optimizing' && <p className="mb-6 text-base text-ink-2">{progressLine(m)}</p>}
      {showAuto && (
        <div className="mb-6">
          <InlineNotice
            action={
              <Button variant="quiet" size="sm" onClick={() => setAutoDismissed(true)}>
                Dismiss
              </Button>
            }
          >
            The recommended functions were selected automatically
          </InlineNotice>
        </div>
      )}
      {view === 'finding' && <FindingView model={m} />}
      {view === 'ended' && !anyStarted && (
        <>
          <FindingView model={m} frozen />
          <Disclosure className="mt-6" summary="Show log">
            <LogTab model={m} />
          </Disclosure>
        </>
      )}
      {view === 'choose' && (
        <ChooseView runId={runId} model={m} onShown={onChooseShown} onSubmitted={onSubmitted} />
      )}
      {showTabs && <RunTabs runId={runId} model={m} epoch={epoch} session={session} ended={ended} />}
    </Page>
  )
}
