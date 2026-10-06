// Folds events into a RunModel. One pure reducer with a handler per event type; the
// backend's netzero/pipeline/projection.py folds the same events into run.json.
import { produce, type Draft } from 'immer'
import type { CandidateId, EventOf, EventType, RunEvent } from '../gen/events'
import type { StepStartData, StepStartType } from '../lib/steps'
import type {
  CandidateAttempt,
  CandidateModel,
  FunctionModel,
  LlmModel,
  RunModel,
  StageUsage,
  Step,
  StepStatus,
  TestAttempt,
} from './types'

/** Log entries kept per scope; the oldest are dropped first. */
export const LOG_CAP = 500

const noUsage = (): StageUsage => ({
  calls: 0,
  inputTokens: 0,
  outputTokens: 0,
  cacheReadTokens: 0,
  cacheWriteTokens: 0,
  costUsd: 0,
})

export function initialRunModel(): RunModel {
  return {
    lastSeq: 0,
    runId: null,
    meta: null,
    updatedTs: null,
    state: 'created',
    history: [],
    power: null,
    steps: { powerCalibration: null, clone: null, env: null, discovery: null, triage: null, artifacts: null },
    triage: [],
    preselected: [],
    selection: null,
    functionOrder: [],
    functions: {},
    currentFunctionId: null,
    benchQueue: [],
    logs: {},
    llm: { ...noUsage(), byStage: {}, runCostUsd: 0 },
    error: null,
    terminal: null,
    unknownTypes: {},
  }
}

/** The log buffer a scope writes to: the run, a function, or one of its candidates. */
export const logScopeKey = (functionId: string | null, candidateId: CandidateId | null): string =>
  functionId === null ? 'run' : candidateId === null ? functionId : `${functionId}#${candidateId}`

type M = Draft<RunModel>
type Handlers = { [T in EventType]: (m: M, event: EventOf<T>) => void }

function openStep<S extends StepStartType>(event: {
  seq: number
  ts: number
  type: S
  data: StepStartData<S>
}): Step<S> {
  return {
    bornSeq: event.seq,
    type: event.type,
    status: 'running',
    startedTs: event.ts,
    started: event.data,
    completedSeq: null,
    completedTs: null,
    result: null,
  }
}

/** Close `step` with its completed event; a step that is not running is left alone. */
function closeStep<R extends { ok: boolean }>(
  step: { status: StepStatus; completedSeq: number | null; completedTs: number | null; result: R | null } | null | undefined,
  event: { seq: number; ts: number; data: R },
): void {
  if (step == null || step.status !== 'running') return
  step.status = event.data.ok ? 'ok' : 'failed'
  step.completedSeq = event.seq
  step.completedTs = event.ts
  step.result = event.data
}

function newFunction(functionId: string, qualname: string, bornSeq: number): FunctionModel {
  return {
    bornSeq,
    functionId,
    qualname,
    started: null,
    tests: [],
    capture: null,
    baseline: null,
    candidates: {},
    decision: null,
    merge: null,
    outcome: null,
    llmCostUsd: 0,
  }
}

const qualnameOf = (functionId: string): string => functionId.split(':').at(-1) ?? functionId

function fnOf(m: M, event: RunEvent): Draft<FunctionModel> | undefined {
  return event.function_id === null ? undefined : m.functions[event.function_id]
}

function testAttemptOf(m: M, event: RunEvent): Draft<TestAttempt> | undefined {
  const fn = fnOf(m, event)
  if (fn === undefined) return undefined
  const attempt = event.attempt ?? 0
  const found = fn.tests.find((t) => t.attempt === attempt)
  if (found !== undefined) return found
  const created: TestAttempt = { bornSeq: event.seq, attempt, write: null, runs: [] }
  fn.tests.push(created)
  return created
}

function candidateOf(m: M, event: RunEvent): Draft<CandidateModel> | undefined {
  const fn = fnOf(m, event)
  const cid = event.candidate_id
  if (fn === undefined || cid === null) return undefined
  return (fn.candidates[cid] ??= {
    bornSeq: event.seq,
    candidateId: cid,
    attempts: [],
    rejection: null,
    queued: null,
    bench: null,
  })
}

function candidateAttemptOf(m: M, event: RunEvent): Draft<CandidateAttempt> | undefined {
  const c = candidateOf(m, event)
  if (c === undefined) return undefined
  const attempt = event.attempt ?? 0
  const found = c.attempts.find((a) => a.attempt === attempt)
  if (found !== undefined) return found
  const created: CandidateAttempt = { bornSeq: event.seq, attempt, write: null, check: null }
  c.attempts.push(created)
  return created
}

const sameBenchEntry = (event: RunEvent) => (e: { functionId: string; candidateId: CandidateId }) =>
  e.functionId === event.function_id && e.candidateId === event.candidate_id

function addUsage(u: Draft<StageUsage> | Draft<LlmModel>, data: EventOf<'llm.usage'>['data']): void {
  u.calls += 1
  u.inputTokens += data.input_tokens
  u.outputTokens += data.output_tokens
  u.cacheReadTokens += data.cache_read_input_tokens
  u.cacheWriteTokens += data.cache_creation_input_tokens
  u.costUsd += data.cost_usd
}

/** Nothing runs or waits once the run has ended. */
function end(m: M): void {
  m.benchQueue = []
  m.currentFunctionId = null
}

const handlers: Handlers = {
  'run.created': (m, e) => {
    m.runId = e.run_id
    m.meta = { ...e.data, bornSeq: e.seq, createdTs: e.ts }
  },
  'run.state_changed': (m, e) => {
    m.state = e.data.to_state
    m.history.push({ bornSeq: e.seq, ts: e.ts, from: e.data.from_state, to: e.data.to_state, reason: e.data.reason })
  },
  'run.power.detected': (m, e) => {
    m.power = { bornSeq: e.seq, power: e.data.power }
  },
  'run.power.calibration.started': (m, e) => {
    m.steps.powerCalibration = openStep(e)
  },
  'run.power.calibration.completed': (m, e) => {
    closeStep(m.steps.powerCalibration, e)
    // a measured per-core power replaces the TDP estimate, as the backend's projection does
    if (e.data.ok && e.data.p_core_w !== null && m.power !== null) m.power.power.p_core_w = e.data.p_core_w
  },
  'run.clone.started': (m, e) => {
    m.steps.clone = openStep(e)
  },
  'run.clone.completed': (m, e) => closeStep(m.steps.clone, e),
  'run.env.started': (m, e) => {
    m.steps.env = openStep(e)
  },
  'run.env.completed': (m, e) => closeStep(m.steps.env, e),
  'run.discovery.started': (m, e) => {
    m.steps.discovery = openStep(e)
  },
  'run.discovery.completed': (m, e) => {
    closeStep(m.steps.discovery, e)
    if (e.data.ok && m.triage.length === 0) m.triage = e.data.heuristic_ranked
  },
  'run.triage.started': (m, e) => {
    m.steps.triage = openStep(e)
  },
  'run.triage.completed': (m, e) => {
    closeStep(m.steps.triage, e)
    if (e.data.ok) {
      m.triage = e.data.items
      m.preselected = e.data.preselected
    }
  },
  'run.selection.confirmed': (m, e) => {
    const ids = e.data.function_ids
    m.selection = { bornSeq: e.seq, functionIds: ids, auto: e.data.auto }
    for (const fid of ids) {
      const item = m.triage.find((t) => t.function_id === fid)
      m.functions[fid] ??= newFunction(fid, item?.qualname ?? qualnameOf(fid), e.seq)
    }
    m.functionOrder = [...ids, ...m.functionOrder.filter((fid) => !ids.includes(fid))]
  },
  'run.artifacts.started': (m, e) => {
    m.steps.artifacts = openStep(e)
  },
  'run.artifacts.completed': (m, e) => closeStep(m.steps.artifacts, e),
  'run.completed': (m, e) => {
    m.terminal = { type: e.type, bornSeq: e.seq, ts: e.ts, summary: e.data.summary }
    end(m)
  },
  'run.failed': (m, e) => {
    m.error = e.data.error
    m.terminal = { type: e.type, bornSeq: e.seq, ts: e.ts, stage: e.data.stage, error: e.data.error }
    end(m)
  },
  'run.cancelled': (m, e) => {
    m.terminal = { type: e.type, bornSeq: e.seq, ts: e.ts, atState: e.data.at_state }
    end(m)
  },
  'run.interrupted': (m, e) => {
    m.terminal = {
      type: e.type,
      bornSeq: e.seq,
      ts: e.ts,
      previousState: e.data.previous_state,
      openSteps: e.data.open_steps,
    }
    end(m)
  },

  'function.started': (m, e) => {
    const fid = e.function_id
    if (fid === null) return
    const fn = (m.functions[fid] ??= newFunction(fid, e.data.info.qualname, e.seq))
    if (!m.functionOrder.includes(fid)) m.functionOrder.push(fid)
    fn.started = { bornSeq: e.seq, ts: e.ts, index: e.data.index, total: e.data.total, info: e.data.info }
    m.currentFunctionId = fid
  },
  'function.tests.write.started': (m, e) => {
    const t = testAttemptOf(m, e)
    if (t !== undefined) t.write = openStep(e)
  },
  'function.tests.write.completed': (m, e) => closeStep(testAttemptOf(m, e)?.write, e),
  'function.tests.run.started': (m, e) => {
    testAttemptOf(m, e)?.runs.push(openStep(e))
  },
  'function.tests.run.completed': (m, e) =>
    closeStep(
      testAttemptOf(m, e)?.runs.find((r) => r.status === 'running'),
      e,
    ),
  'function.capture.started': (m, e) => {
    const fn = fnOf(m, e)
    if (fn !== undefined) fn.capture = openStep(e)
  },
  'function.capture.completed': (m, e) => closeStep(fnOf(m, e)?.capture, e),
  'function.baseline.started': (m, e) => {
    const fn = fnOf(m, e)
    if (fn !== undefined) fn.baseline = openStep(e)
  },
  'function.baseline.completed': (m, e) => closeStep(fnOf(m, e)?.baseline, e),
  'function.decision': (m, e) => {
    const fn = fnOf(m, e)
    if (fn !== undefined) fn.decision = { ...e.data, bornSeq: e.seq }
  },
  'function.merge.started': (m, e) => {
    const fn = fnOf(m, e)
    if (fn !== undefined) fn.merge = openStep(e)
  },
  'function.merge.completed': (m, e) => closeStep(fnOf(m, e)?.merge, e),
  'function.completed': (m, e) => {
    const fn = fnOf(m, e)
    if (fn !== undefined) fn.outcome = { ...e.data, bornSeq: e.seq, ts: e.ts }
    m.benchQueue = m.benchQueue.filter((q) => q.functionId !== e.function_id)
    if (m.currentFunctionId === e.function_id) m.currentFunctionId = null
  },

  'candidate.write.started': (m, e) => {
    const a = candidateAttemptOf(m, e)
    if (a !== undefined) a.write = openStep(e)
  },
  'candidate.write.completed': (m, e) => closeStep(candidateAttemptOf(m, e)?.write, e),
  'candidate.check.started': (m, e) => {
    const a = candidateAttemptOf(m, e)
    if (a !== undefined) a.check = openStep(e)
  },
  'candidate.check.completed': (m, e) => closeStep(candidateAttemptOf(m, e)?.check, e),
  'candidate.rejected': (m, e) => {
    const c = candidateOf(m, e)
    if (c !== undefined) c.rejection = { ...e.data, bornSeq: e.seq, attempt: e.attempt }
  },
  'candidate.bench.queued': (m, e) => {
    const c = candidateOf(m, e)
    if (c === undefined || e.function_id === null || e.candidate_id === null) return
    c.queued = { bornSeq: e.seq, position: e.data.position, attempt: e.attempt }
    m.benchQueue.push({
      bornSeq: e.seq,
      functionId: e.function_id,
      candidateId: e.candidate_id,
      attempt: e.attempt,
      position: e.data.position,
      status: 'queued',
    })
  },
  'candidate.bench.started': (m, e) => {
    const c = candidateOf(m, e)
    if (c !== undefined) c.bench = openStep(e)
    const entry = m.benchQueue.find(sameBenchEntry(e))
    if (entry !== undefined) entry.status = 'running'
  },
  'candidate.bench.completed': (m, e) => {
    closeStep(candidateOf(m, e)?.bench, e)
    const isThis = sameBenchEntry(e)
    m.benchQueue = m.benchQueue.filter((q) => !isThis(q))
  },

  'llm.usage': (m, e) => {
    addUsage(m.llm, e.data)
    addUsage((m.llm.byStage[e.data.stage] ??= noUsage()), e.data)
    m.llm.runCostUsd = e.data.run_cost_usd
    const fn = fnOf(m, e)
    if (fn !== undefined) fn.llmCostUsd += e.data.cost_usd
  },
  log: (m, e) => {
    const buffer = (m.logs[logScopeKey(e.function_id, e.candidate_id)] ??= { entries: [], dropped: 0 })
    buffer.entries.push({ ...e.data, seq: e.seq, ts: e.ts, attempt: e.attempt })
    const over = buffer.entries.length - LOG_CAP
    if (over > 0) {
      buffer.entries.splice(0, over)
      buffer.dropped += over
    }
  },
}

/** Every event type the reducer folds; anything else is counted in `unknownTypes`. */
export const HANDLED_TYPES = Object.keys(handlers) as EventType[]

function apply(m: M, event: RunEvent): void {
  // replays and reconnects resend events; the first copy wins
  if (event.seq <= m.lastSeq) return
  m.lastSeq = event.seq
  m.updatedTs = event.ts
  if (!Object.hasOwn(handlers, event.type)) {
    m.unknownTypes[event.type] = (m.unknownTypes[event.type] ?? 0) + 1
    return
  }
  // the map is keyed by type, so the handler always matches its event
  const handle = handlers[event.type] as (m: M, e: RunEvent) => void
  handle(m, event)
}

/**
 * Folds `events` into `state`. Events at or below `state.lastSeq` are skipped; when every
 * event is skipped the same object comes back, so subscribers see no change.
 */
export function reduce(state: RunModel, events: readonly RunEvent[]): RunModel {
  return produce(state, (m) => {
    for (const event of events) apply(m, event)
  })
}
