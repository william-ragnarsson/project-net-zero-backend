import { describe, expect, it } from 'vitest'
import type { RunEvent } from '../gen/events'
import type { LiveStatus } from '../sources/live'
import { initialRunModel, reduce } from '../state/reducer'
import { buildSyntheticRun } from '../synth/scenario'
import { makeEvent } from '../test/events'
import { awaitingSince, CHOOSE_GRACE_MS, pickView } from './view'

const { events } = buildSyntheticRun()
const RUN = events[0]!.run_id
const upTo = (seq: number) => reduce(initialRunModel(), events.filter((e) => e.seq <= seq))
const seqOf = (pred: (e: RunEvent) => boolean) => events.find(pred)!.seq

const AWAITING = seqOf((e) => e.type === 'run.state_changed' && e.data.to_state === 'awaiting_selection')
const SELECTED = seqOf((e) => e.type === 'run.selection.confirmed')
const FINALIZING = seqOf((e) => e.type === 'run.state_changed' && e.data.to_state === 'finalizing')

const streaming: LiveStatus = { kind: 'streaming' }
const NOW = 10_000

/** the run ends at `seq` with the given terminal event */
function endedWith(seq: number, terminal: RunEvent) {
  const to = terminal.type === 'run.failed' ? 'failed' : terminal.type === 'run.cancelled' ? 'cancelled' : 'interrupted'
  const m = upTo(seq)
  return reduce(m, [
    makeEvent(seq + 1, 'run.state_changed', { from_state: m.state, to_state: to, reason: null }, { run_id: RUN }),
    { ...terminal, seq: seq + 2 },
  ])
}

describe('pickView', () => {
  it('says not found first, even over a model', () => {
    expect(pickView(upTo(10), { kind: 'not_found' }, null, NOW)).toBe('not_found')
  })

  it('shows an error only when nothing has loaded', () => {
    const error: LiveStatus = { kind: 'error', message: 'boom' }
    expect(pickView(initialRunModel(), error, null, NOW)).toBe('error')
    expect(pickView(upTo(10), error, null, NOW)).toBe('finding')
  })

  it('is loading until the first event, also while the log is not ready', () => {
    expect(pickView(initialRunModel(), { kind: 'loading' }, null, NOW)).toBe('loading')
    expect(pickView(initialRunModel(), { kind: 'not_ready', retryInMs: 1000 }, null, NOW)).toBe('loading')
  })

  it('is finding while the run sets up', () => {
    expect(pickView(upTo(3), streaming, null, NOW)).toBe('finding')
    expect(pickView(upTo(AWAITING - 1), streaming, null, NOW)).toBe('finding')
  })

  it('waits out the grace before the choice', () => {
    const m = upTo(AWAITING)
    expect(m.state).toBe('awaiting_selection')
    expect(pickView(m, streaming, null, NOW)).toBe('finding')
    expect(pickView(m, streaming, NOW - CHOOSE_GRACE_MS + 1, NOW)).toBe('finding')
    expect(pickView(m, streaming, NOW - CHOOSE_GRACE_MS, NOW)).toBe('choose')
  })

  it('shows a hydrated choice at once, even at time 0', () => {
    expect(pickView(upTo(AWAITING), streaming, -Infinity, 0)).toBe('choose')
  })

  it('is optimizing from the selection on, finalizing included', () => {
    const selected = upTo(SELECTED)
    expect(selected.state).toBe('awaiting_selection')
    expect(pickView(selected, streaming, -Infinity, NOW)).toBe('optimizing')
    const finalizing = upTo(FINALIZING)
    expect(finalizing.state).toBe('finalizing')
    expect(pickView(finalizing, streaming, null, NOW)).toBe('optimizing')
  })

  it('is done after run.completed and ended after any other terminal', () => {
    expect(pickView(upTo(Infinity), { kind: 'ended' }, null, NOW)).toBe('done')
    const failed = makeEvent(0, 'run.failed', { stage: 'triaging', error: { kind: 'internal', message: 'x', detail: null } }, { run_id: RUN })
    const cancelled = makeEvent(0, 'run.cancelled', { at_state: 'optimizing', reason: 'user' }, { run_id: RUN })
    const interrupted = makeEvent(0, 'run.interrupted', { previous_state: 'optimizing', open_steps: [] }, { run_id: RUN })
    expect(pickView(endedWith(AWAITING - 3, failed), { kind: 'ended' }, null, NOW)).toBe('ended')
    expect(pickView(endedWith(SELECTED + 20, cancelled), { kind: 'ended' }, null, NOW)).toBe('ended')
    expect(pickView(endedWith(SELECTED + 20, interrupted), { kind: 'ended' }, null, NOW)).toBe('ended')
    // a terminal model wins over a reconnecting status
    expect(pickView(endedWith(AWAITING, cancelled), { kind: 'reconnecting', attempt: 2 }, -Infinity, NOW)).toBe('ended')
  })
})

describe('awaitingSince', () => {
  it('is null outside awaiting_selection', () => {
    expect(awaitingSince(upTo(10), 10, NOW)).toBeNull()
    expect(awaitingSince(upTo(SELECTED + 1), SELECTED + 1, NOW)).toBeNull()
  })

  it('is -Infinity when the state came from the hydrated log', () => {
    expect(awaitingSince(upTo(AWAITING), AWAITING, NOW)).toBe(-Infinity)
  })

  it('is now when the state arrived live', () => {
    expect(awaitingSince(upTo(AWAITING), AWAITING - 1, NOW)).toBe(NOW)
  })
})
