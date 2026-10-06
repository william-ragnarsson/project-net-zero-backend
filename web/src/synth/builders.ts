import type { CandidateId, ErrorInfo, EventOf, EventType, RunEvent } from '../gen/events'
import type { StepDoneData, StepDoneType, StepStartType } from '../lib/steps'

export interface Scope {
  function_id?: string | null
  candidate_id?: CandidateId | null
  attempt?: number | null
}

/** What a step's caller supplies; the clock fills in ok, duration_ms and error. */
export type StepResult<S extends StepStartType> = Omit<StepDoneData<S>, 'ok' | 'duration_ms' | 'error'>

/**
 * Builds a grammar-valid event log: a gapless seq, a run_id and a virtual clock that
 * only moves forward. Every event is emitted in full (nulls included), as the backend
 * serializes it.
 */
export class RunBuilder {
  readonly events: RunEvent[] = []
  readonly runId: string
  private seq = 0
  private now: number

  constructor(runId: string, startTs: number) {
    this.runId = runId
    this.now = startTs
  }

  get ts(): number {
    return this.now
  }

  advance(ms: number): void {
    this.advanceTo(this.now + ms)
  }

  advanceTo(ts: number): void {
    this.now = Math.max(this.now, Math.round(ts))
  }

  emit<T extends EventType>(type: T, data: EventOf<T>['data'], scope: Scope = {}): EventOf<T> {
    this.seq += 1
    // key order matches the backend's lines, which start with "seq"
    const event = {
      seq: this.seq,
      ts: this.now,
      run_id: this.runId,
      function_id: scope.function_id ?? null,
      candidate_id: scope.candidate_id ?? null,
      attempt: scope.attempt ?? null,
      type,
      data,
    } as EventOf<T>
    this.events.push(event)
    return event
  }

  start<S extends StepStartType>(type: S, data: EventOf<S>['data'], scope: Scope = {}): Step<S> {
    this.emit(type, data, scope)
    return new Step(this, type, scope, this.now)
  }
}

export class Step<S extends StepStartType> {
  private readonly builder: RunBuilder
  private readonly type: S
  private readonly scope: Scope
  private readonly startedTs: number

  constructor(builder: RunBuilder, type: S, scope: Scope, startedTs: number) {
    this.builder = builder
    this.type = type
    this.scope = scope
    this.startedTs = startedTs
  }

  ok(result: StepResult<S>): EventOf<StepDoneType<S>> {
    return this.close(true, null, result)
  }

  fail(error: ErrorInfo | null, result: StepResult<S>): EventOf<StepDoneType<S>> {
    return this.close(false, error, result)
  }

  private close(ok: boolean, error: ErrorInfo | null, result: StepResult<S>): EventOf<StepDoneType<S>> {
    const done = this.type.replace(/\.started$/, '.completed') as StepDoneType<S>
    const data = { ok, duration_ms: this.builder.ts - this.startedTs, error, ...result }
    // TS cannot resolve the payload of a still-generic event type; StepResult<S> pins it.
    return this.builder.emit(done, data as never, this.scope)
  }
}

/** A cooperative task: yields a delay in ms, or a condition to wait for. */
export type Task<R = void> = Generator<number | (() => boolean), R, void>

/**
 * Runs tasks concurrently on the builder's clock. A waiting condition is resumed as soon
 * as it holds; otherwise the task with the earliest wake-up runs next (ties by task order),
 * so the interleaving is deterministic.
 */
export function runConcurrently(builder: RunBuilder, tasks: Task[]): void {
  interface Slot {
    task: Task
    order: number
    wakeAt: number | null
    until: (() => boolean) | null
  }
  const live: Slot[] = tasks.map((task, order) => ({ task, order, wakeAt: builder.ts, until: null }))

  while (live.length > 0) {
    let next = live.find((s) => s.until?.() === true)
    if (next === undefined) {
      next = live
        .filter((s) => s.wakeAt !== null)
        .sort((a, b) => (a.wakeAt ?? 0) - (b.wakeAt ?? 0) || a.order - b.order)[0]
      if (next === undefined) throw new Error('runConcurrently: every task waits on a condition')
      builder.advanceTo(next.wakeAt ?? builder.ts)
    }
    const step = next.task.next()
    if (step.done) {
      live.splice(live.indexOf(next), 1)
    } else if (typeof step.value === 'number') {
      next.wakeAt = builder.ts + step.value
      next.until = null
    } else {
      next.wakeAt = null
      next.until = step.value
    }
  }
}
