// Derived views of a RunModel. Each returns a fresh value: components memoize on the model
// (`useMemo(() => selectX(model), [model])`) rather than selecting these from the store.
import type { CandidateId, CI, FunctionOutcome, RunState, RunTotals } from '../gen/events'
import type { StepStartType } from '../lib/steps'
import type { CandidateModel, FunctionModel, LogEntry, RunModel, Step } from './types'

const sum = (xs: readonly number[]): number => xs.reduce((a, x) => a + x, 0)

const isDefined = <T>(x: T | undefined): x is T => x !== undefined

export const selectFunctions = (m: RunModel): FunctionModel[] =>
  m.functionOrder.map((fid) => m.functions[fid]).filter(isDefined)

/**
 * The run's totals by the backend's projection rules (netzero/pipeline/projection.py), so
 * they match `run.completed`'s summary. Its `duration_ms` is the server's measured run time
 * and wins once known; until then it is the span of event timestamps.
 */
export function selectTotals(m: RunModel): RunTotals {
  const outcomes = selectFunctions(m).flatMap((f) => (f.outcome === null ? [] : [f.outcome]))
  const counts: RunTotals['counts_by_outcome'] = {}
  for (const o of outcomes) counts[o.outcome] = (counts[o.outcome] ?? 0) + 1
  const accepted = outcomes.filter((o) => o.outcome === 'accepted' && o.delta_pct !== null)
  const createdTs = m.meta?.createdTs ?? m.updatedTs ?? 0
  return {
    functions_total: m.functionOrder.length,
    functions_done: outcomes.length,
    counts_by_outcome: counts,
    mean_reduction_pct: accepted.length > 0 ? sum(accepted.map((o) => o.delta_pct ?? 0)) / accepted.length : null,
    g_saved_per_1m_calls: sum(accepted.map((o) => o.g_saved_per_1m_calls ?? 0)),
    kwh_saved_per_1m_calls: sum(
      outcomes.flatMap((o) =>
        o.outcome === 'accepted' && o.kwh_saved_per_1m_calls !== null ? [o.kwh_saved_per_1m_calls] : [],
      ),
    ),
    llm_cost_usd: m.llm.runCostUsd,
    duration_ms:
      m.terminal?.type === 'run.completed' ? m.terminal.summary.duration_ms : (m.updatedTs ?? createdTs) - createdTs,
  }
}

export const selectCurrentFunction = (m: RunModel): FunctionModel | null =>
  m.currentFunctionId === null ? null : (m.functions[m.currentFunctionId] ?? null)

export interface ActiveStep {
  /** stable across renders: the step's type plus its scope */
  key: string
  type: StepStartType
  functionId: string | null
  candidateId: CandidateId | null
  attempt: number | null
  startedTs: number
  bornSeq: number
}

/** Every step still running, oldest first; none once the run has ended. */
export function selectActiveSteps(m: RunModel): ActiveStep[] {
  if (m.terminal !== null) return []
  const out: ActiveStep[] = []
  const add = (
    step: Pick<Step<StepStartType>, 'type' | 'status' | 'startedTs' | 'bornSeq'> | null,
    functionId: string | null = null,
    candidateId: CandidateId | null = null,
    attempt: number | null = null,
  ) => {
    if (step === null || step.status !== 'running') return
    out.push({
      key: [step.type, functionId ?? '', candidateId ?? '', attempt ?? ''].join('|'),
      type: step.type,
      functionId,
      candidateId,
      attempt,
      startedTs: step.startedTs,
      bornSeq: step.bornSeq,
    })
  }
  Object.values(m.steps).forEach((step) => add(step))
  const fn = selectCurrentFunction(m)
  if (fn !== null) {
    const fid = fn.functionId
    for (const t of fn.tests) {
      add(t.write, fid, null, t.attempt)
      t.runs.forEach((r) => add(r, fid, null, t.attempt))
    }
    add(fn.capture, fid)
    add(fn.baseline, fid)
    for (const c of Object.values(fn.candidates)) {
      for (const a of c.attempts) {
        add(a.write, fid, c.candidateId, a.attempt)
        add(a.check, fid, c.candidateId, a.attempt)
      }
      add(c.bench, fid, c.candidateId, c.queued?.attempt ?? null)
    }
    add(fn.merge, fid)
  }
  return out.sort((a, b) => a.bornSeq - b.bornSeq)
}

export type CandidatePhase =
  | 'writing'
  | 'checking'
  /** passed its checks; waits for the baseline before it queues */
  | 'checked'
  | 'queued'
  | 'benching'
  | 'benched'
  | 'rejected'
  | 'failed'

/** Where a candidate is now, from its latest attempt. */
export function candidatePhase(c: CandidateModel): CandidatePhase {
  if (c.rejection !== null) return 'rejected'
  if (c.bench !== null) {
    return c.bench.status === 'running' ? 'benching' : c.bench.status === 'ok' ? 'benched' : 'failed'
  }
  if (c.queued !== null) return 'queued'
  const last = c.attempts.at(-1)
  const check = last?.check ?? null
  if (check !== null) return check.status === 'running' ? 'checking' : check.status === 'ok' ? 'checked' : 'failed'
  return last?.write?.status === 'failed' ? 'failed' : 'writing'
}

export type FunctionPhase = 'pending' | 'tests' | 'capture' | 'candidates' | 'deciding' | 'merge' | 'done'

function functionPhase(f: FunctionModel): FunctionPhase {
  if (f.outcome !== null) return 'done'
  if (f.started === null) return 'pending'
  if (f.merge !== null) return 'merge'
  if (f.decision !== null) return 'deciding'
  if (f.baseline !== null || Object.keys(f.candidates).length > 0) return 'candidates'
  if (f.capture !== null) return 'capture'
  return 'tests'
}

export interface FunctionRow {
  functionId: string
  qualname: string
  bornSeq: number
  phase: FunctionPhase
  outcome: FunctionOutcome | null
  winner: CandidateId | null
  deltaPct: number | null
  deltaCiPct: CI | null
  gSavedPer1mCalls: number | null
  reason: string
  candidates: Partial<Record<CandidateId, CandidatePhase>>
  llmCostUsd: number
}

export const selectFunctionRows = (m: RunModel): FunctionRow[] =>
  selectFunctions(m).map((f) => ({
    functionId: f.functionId,
    qualname: f.qualname,
    bornSeq: f.bornSeq,
    phase: functionPhase(f),
    outcome: f.outcome?.outcome ?? null,
    winner: f.outcome?.winner ?? f.decision?.winner ?? null,
    deltaPct: f.outcome?.delta_pct ?? null,
    deltaCiPct: f.outcome?.delta_ci_pct ?? null,
    gSavedPer1mCalls: f.outcome?.g_saved_per_1m_calls ?? null,
    reason: f.outcome?.reason ?? '',
    candidates: Object.fromEntries(Object.values(f.candidates).map((c) => [c.candidateId, candidatePhase(c)])),
    llmCostUsd: f.llmCostUsd,
  }))

export interface Hud {
  state: RunState
  /** 1-based position of the function being optimized, else null */
  currentIndex: number | null
  functionsDone: number
  functionsTotal: number
  accepted: number
  meanReductionPct: number | null
  gSavedPer1mCalls: number
  kwhSavedPer1mCalls: number
  llmCostUsd: number
  llmCalls: number
  llmTokens: number
  benchQueued: number
  elapsedMs: number
}

export function selectHud(m: RunModel): Hud {
  const t = selectTotals(m)
  const current = selectCurrentFunction(m)
  return {
    state: m.state,
    currentIndex: current?.started ? current.started.index + 1 : null,
    functionsDone: t.functions_done,
    functionsTotal: t.functions_total,
    accepted: t.counts_by_outcome.accepted ?? 0,
    meanReductionPct: t.mean_reduction_pct,
    gSavedPer1mCalls: t.g_saved_per_1m_calls,
    kwhSavedPer1mCalls: t.kwh_saved_per_1m_calls,
    llmCostUsd: t.llm_cost_usd,
    llmCalls: m.llm.calls,
    llmTokens: m.llm.inputTokens + m.llm.outputTokens + m.llm.cacheReadTokens + m.llm.cacheWriteTokens,
    benchQueued: m.benchQueue.filter((q) => q.status === 'queued').length,
    elapsedMs: t.duration_ms,
  }
}

/** The newest `n` log entries across every scope, oldest first. */
export const selectRecentLogs = (m: RunModel, n: number): LogEntry[] =>
  Object.values(m.logs)
    .flatMap((buffer) => buffer.entries.slice(-n))
    .sort((a, b) => a.seq - b.seq)
    .slice(-n)
