import type { EventOf, EventType } from '../gen/events'

/** `X.started` types closed by an `X.completed` that carries ok / duration_ms / error. */
export type StepStartType = Exclude<Extract<EventType, `${string}.started`>, 'function.started'>

export type StepDoneType<S extends StepStartType> = S extends `${infer P}.started`
  ? Extract<`${P}.completed`, EventType>
  : never

export type StepStartData<S extends StepStartType> = EventOf<S>['data']

export type StepDoneData<S extends StepStartType> = EventOf<StepDoneType<S>>['data']
