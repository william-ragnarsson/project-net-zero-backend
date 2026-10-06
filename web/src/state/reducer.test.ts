import { describe, expect, it } from 'vitest'
import type { EventOf, EventType, RunEvent } from '../gen/events'
import schema from '../gen/schema.json'
import { Rand } from '../synth/random'
import { buildSyntheticRun } from '../synth/scenario'
import { makeEvent, makeLog, makeUnknown } from '../test/events'
import { HANDLED_TYPES, initialRunModel, LOG_CAP, logScopeKey, reduce } from './reducer'
import { selectActiveSteps, selectFunctionRows, selectHud, selectTotals } from './selectors'
import type { RunModel } from './types'

const { events, summary } = buildSyntheticRun()
const fold = (evs: readonly RunEvent[], from: RunModel = initialRunModel()) => reduce(from, evs)
const oneByOne = (evs: readonly RunEvent[]) => evs.reduce((m, e) => reduce(m, [e]), initialRunModel())
const allOf = <T extends EventType>(type: T) => events.filter((e): e is EventOf<T> => e.type === type)
const selection = allOf('run.selection.confirmed')[0]

describe('reduce', () => {
  it('handles exactly the event types of the backend contract', () => {
    const contract = Object.keys(schema.properties.event.discriminator.mapping)
    expect([...HANDLED_TYPES].sort()).toEqual(contract.sort())
  })

  it("matches run.completed's summary at the end of the synthetic run", () => {
    const m = fold(events)
    expect(m.terminal).toMatchObject({ type: 'run.completed', summary })
    expect(selectTotals(m)).toEqual(summary)
  })

  it('computes the same totals before the terminal event arrives', () => {
    const m = fold(events.slice(0, -1))
    const createdTs = events[0]?.ts ?? 0
    expect(m.terminal).toBeNull()
    expect(selectTotals(m)).toEqual({ ...summary, duration_ms: (m.updatedTs ?? 0) - createdTs })
  })

  it('reaches the same state whether events arrive one by one or in batches', () => {
    const expected = oneByOne(events)
    expect(fold(events)).toEqual(expected)
    for (const seed of [1, 2, 3, 4, 5]) {
      const rand = new Rand('batches', seed)
      let m = initialRunModel()
      for (let i = 0; i < events.length; ) {
        const n = rand.randint(1, 40)
        m = reduce(m, events.slice(i, i + n))
        i += n
      }
      expect(m).toEqual(expected)
    }
  })

  it('returns the same object when every event is a duplicate', () => {
    const m = fold(events.slice(0, 120))
    expect(reduce(m, events.slice(40, 120))).toBe(m)
    expect(reduce(m, [])).toBe(m)
  })

  it('skips the already-seen part of an overlapping batch', () => {
    const m = fold(events.slice(0, 120))
    expect(reduce(m, events.slice(100, 160))).toEqual(fold(events.slice(120, 160), m))
  })

  it('counts unknown event types and otherwise ignores them', () => {
    const before = fold(events.slice(0, 10))
    const seq = before.lastSeq
    const m = reduce(before, [makeUnknown(seq + 1, 'run.teleported'), makeUnknown(seq + 2, 'run.teleported')])
    expect(m.unknownTypes).toEqual({ 'run.teleported': 2 })
    expect({ ...m, unknownTypes: {}, lastSeq: seq, updatedTs: before.updatedTs }).toEqual(before)
  })

  it('records the seq of the event that created each element', () => {
    const m = fold(events)
    expect(m.selection?.bornSeq).toBe(selection?.seq)
    for (const fn of Object.values(m.functions)) {
      expect(fn.bornSeq).toBe(selection?.seq)
      const started = events.find((e) => e.type === 'function.started' && e.function_id === fn.functionId)
      expect(fn.started?.bornSeq).toBe(started?.seq)
    }
  })

  it('keeps functions in selection order with one outcome each', () => {
    const rows = selectFunctionRows(fold(events))
    expect(rows.map((r) => r.functionId)).toEqual(selection?.data.function_ids)
    expect(rows.every((r) => r.phase === 'done')).toBe(true)
    const counts: Record<string, number> = {}
    for (const r of rows) if (r.outcome !== null) counts[r.outcome] = (counts[r.outcome] ?? 0) + 1
    expect(counts).toEqual(summary.counts_by_outcome)
  })

  it("drops a function's bench queue entries when it completes", () => {
    let m = initialRunModel()
    let longest = 0
    for (const e of events) {
      m = reduce(m, [e])
      longest = Math.max(longest, m.benchQueue.length)
      if (e.type === 'function.completed') expect(m.benchQueue.filter((q) => q.functionId === e.function_id)).toEqual([])
    }
    expect(longest).toBeGreaterThan(1)
    expect(m.benchQueue).toEqual([])
  })

  it('empties the bench queue and ends every step on a terminal event', () => {
    const queuedAt = events.findIndex((e) => e.type === 'candidate.bench.queued')
    const m = fold(events.slice(0, queuedAt + 1))
    expect(m.benchQueue).toHaveLength(1)
    expect(selectActiveSteps(m).length).toBeGreaterThan(0)

    const cancelled = reduce(m, [makeEvent(m.lastSeq + 1, 'run.cancelled', { at_state: 'optimizing', reason: 'user' })])
    expect(cancelled.benchQueue).toEqual([])
    expect(cancelled.currentFunctionId).toBeNull()
    expect(cancelled.terminal).toMatchObject({ type: 'run.cancelled', atState: 'optimizing' })
    expect(selectActiveSteps(cancelled)).toEqual([])
  })

  it('keeps the error of a failed run', () => {
    const error = { kind: 'clone_error', message: 'repository not found', detail: null } as const
    const m = fold([...events.slice(0, 5), makeEvent(events[4]!.seq + 1, 'run.failed', { stage: 'cloning', error })])
    expect(m.error).toEqual(error)
    expect(m.terminal).toMatchObject({ type: 'run.failed', stage: 'cloning', error })
  })

  it('keeps the newest LOG_CAP entries per scope and counts the rest', () => {
    const fid = 'pkg.mod:fn'
    const logs = Array.from({ length: LOG_CAP + 5 }, (_, i) => makeLog(i + 1, `line ${i + 1}`, { function_id: fid }))
    const m = fold([...logs, makeLog(LOG_CAP + 6, 'run line')])
    const buffer = m.logs[logScopeKey(fid, null)]
    expect(buffer?.entries).toHaveLength(LOG_CAP)
    expect(buffer?.dropped).toBe(5)
    expect(buffer?.entries[0]?.lines).toEqual(['line 6'])
    expect(m.logs.run?.entries.map((e) => e.lines)).toEqual([['run line']])
  })

  it('sums LLM usage per stage and keeps the backend running cost', () => {
    const usage = allOf('llm.usage')
    const m = fold(events)
    expect(selectHud(m).llmCalls).toBe(usage.length)
    expect(selectHud(m).llmCostUsd).toBe(usage.at(-1)?.data.run_cost_usd)
    const stages = new Set(usage.map((u) => u.data.stage))
    expect(Object.keys(m.llm.byStage).sort()).toEqual([...stages].sort())
    const calls = Object.values(m.llm.byStage).reduce((n, s) => n + s.calls, 0)
    expect(calls).toBe(usage.length)
  })
})
