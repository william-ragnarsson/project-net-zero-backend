// Hand-made events for tests that need a case the synthetic run does not contain.
import type { EventOf, EventType, RunEvent } from '../gen/events'

type Scope = Partial<Pick<RunEvent, 'function_id' | 'candidate_id' | 'attempt' | 'ts' | 'run_id'>>

export function makeEvent<T extends EventType>(seq: number, type: T, data: EventOf<T>['data'], scope: Scope = {}): EventOf<T> {
  // the union cannot be narrowed through a generic `type`, so the envelope is asserted
  return {
    seq,
    ts: 1_767_225_600_000 + seq * 1000,
    run_id: 'test-run',
    function_id: null,
    candidate_id: null,
    attempt: null,
    ...scope,
    type,
    data,
  } as EventOf<T>
}

export const makeLog = (seq: number, line: string, scope: Scope = {}): EventOf<'log'> =>
  makeEvent(seq, 'log', { level: 'info', source: 'pytest', stream: 'stdout', lines: [line], truncated: false }, scope)

/** An event of a type this build does not know, as a newer backend might send. */
export const makeUnknown = (seq: number, type: string): RunEvent =>
  ({ ...makeLog(seq, ''), type, data: {} }) as unknown as RunEvent
