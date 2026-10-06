// The run as the UI sees it: every event folded into one immutable model. Each element
// records `bornSeq`, the seq of the event that created it, so views can animate arrivals.
import type {
  CandidateId,
  CandidateRejectedData,
  ErrorInfo,
  FunctionCompletedData,
  FunctionDecisionData,
  FunctionInfo,
  LlmStage,
  LogData,
  OpenStep,
  PowerInfo,
  RunCreatedData,
  RunState,
  RunTotals,
  TriageItem,
} from '../gen/events'
import type { StepDoneData, StepStartData, StepStartType } from '../lib/steps'

export interface Born {
  bornSeq: number
}

export type StepStatus = 'running' | 'ok' | 'failed'

/** A started/completed pair; `result` stays null while it runs. */
export interface Step<S extends StepStartType> extends Born {
  type: S
  status: StepStatus
  startedTs: number
  started: StepStartData<S>
  completedSeq: number | null
  completedTs: number | null
  result: StepDoneData<S> | null
}

export interface StateChange extends Born {
  ts: number
  from: RunState | null
  to: RunState
  reason: string | null
}

export interface TestAttempt extends Born {
  attempt: number
  write: Step<'function.tests.write.started'> | null
  /** the generated tests run twice on the original, to catch flaky ones */
  runs: Step<'function.tests.run.started'>[]
}

export interface CandidateAttempt extends Born {
  attempt: number
  write: Step<'candidate.write.started'> | null
  check: Step<'candidate.check.started'> | null
}

export interface CandidateModel extends Born {
  candidateId: CandidateId
  attempts: CandidateAttempt[]
  rejection: (CandidateRejectedData & Born & { attempt: number | null }) | null
  /** where it joined the bench queue; the bench itself is `bench` */
  queued: (Born & { position: number; attempt: number | null }) | null
  bench: Step<'candidate.bench.started'> | null
}

export interface FunctionModel extends Born {
  functionId: string
  qualname: string
  /** null until the function starts; selected functions exist from the selection on */
  started: (Born & { ts: number; index: number; total: number; info: FunctionInfo }) | null
  tests: TestAttempt[]
  capture: Step<'function.capture.started'> | null
  baseline: Step<'function.baseline.started'> | null
  candidates: Partial<Record<CandidateId, CandidateModel>>
  decision: (FunctionDecisionData & Born) | null
  merge: Step<'function.merge.started'> | null
  outcome: (FunctionCompletedData & Born & { ts: number }) | null
  llmCostUsd: number
}

export interface BenchQueueEntry extends Born {
  functionId: string
  candidateId: CandidateId
  attempt: number | null
  /** candidates ahead of it when it queued */
  position: number
  status: 'queued' | 'running'
}

export interface LogEntry extends Omit<LogData, 'lines'> {
  seq: number
  ts: number
  attempt: number | null
  lines: string[]
}

/** The newest `LOG_CAP` entries of one scope; `dropped` counts the ones pushed out. */
export interface LogBuffer {
  entries: LogEntry[]
  dropped: number
}

export interface StageUsage {
  calls: number
  inputTokens: number
  outputTokens: number
  cacheReadTokens: number
  cacheWriteTokens: number
  costUsd: number
}

export interface LlmModel extends StageUsage {
  byStage: Partial<Record<LlmStage, StageUsage>>
  /** the backend's running total (`run_cost_usd`), authoritative over the sum of costs */
  runCostUsd: number
}

export type RunSteps = {
  powerCalibration: Step<'run.power.calibration.started'> | null
  clone: Step<'run.clone.started'> | null
  env: Step<'run.env.started'> | null
  discovery: Step<'run.discovery.started'> | null
  triage: Step<'run.triage.started'> | null
  artifacts: Step<'run.artifacts.started'> | null
}

export type Terminal = Born & { ts: number } & (
    | { type: 'run.completed'; summary: RunTotals }
    | { type: 'run.failed'; stage: RunState; error: ErrorInfo }
    | { type: 'run.cancelled'; atState: RunState }
    | { type: 'run.interrupted'; previousState: RunState; openSteps: OpenStep[] }
  )

export interface RunModel {
  /** the last seq folded in; 0 before any event */
  lastSeq: number
  runId: string | null
  meta: (RunCreatedData & Born & { createdTs: number }) | null
  updatedTs: number | null
  state: RunState
  history: StateChange[]
  power: (Born & { power: PowerInfo }) | null
  steps: RunSteps
  /** heuristic ranking after discovery, replaced by the model's ratings after triage */
  triage: TriageItem[]
  preselected: string[]
  selection: (Born & { functionIds: string[]; auto: boolean }) | null
  functionOrder: string[]
  functions: Record<string, FunctionModel>
  currentFunctionId: string | null
  benchQueue: BenchQueueEntry[]
  /** keyed by `logScopeKey` */
  logs: Record<string, LogBuffer>
  llm: LlmModel
  error: ErrorInfo | null
  terminal: Terminal | null
  /** event types this build does not know (a newer backend), with how often each arrived */
  unknownTypes: Record<string, number>
}
