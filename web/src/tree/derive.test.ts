import { describe, expect, it } from 'vitest'
import type { EventOf, EventType, RunEvent } from '../gen/events'
import { initialRunModel, reduce } from '../state/reducer'
import type { RunModel } from '../state/types'
import { buildSyntheticRun } from '../synth/scenario'
import { makeEvent } from '../test/events'
import { deriveTree, functionOfId, nodeIds, type FnNode, type Tree } from './derive'

const { events } = buildSyntheticRun()
const fold = (evs: readonly RunEvent[]): RunModel => reduce(initialRunModel(), evs)
const prefix = (seq: number) => events.filter((e) => e.seq <= seq)
const first = <T extends EventType>(type: T, fid?: string): EventOf<T> => {
  const e = events.find((x): x is EventOf<T> => x.type === type && (fid === undefined || x.function_id === fid))
  if (e === undefined) throw new Error(`no ${type}`)
  return e
}
const byName = (t: Tree, qualname: string): FnNode => {
  const f = t.functions.find((x) => x.qualname === qualname)
  if (f === undefined) throw new Error(`no ${qualname}`)
  return f
}
/** Every event after `seq`, renumbered so the log has no gaps after a cut. */
const append = (base: readonly RunEvent[], extra: RunEvent[]): RunEvent[] => {
  const last = base.at(-1)?.seq ?? 0
  return [...base, ...extra.map((e, i) => ({ ...e, seq: last + i + 1 }))]
}

const DEDUPE = 'textkit.dedupe:dedupe_preserve_order'

describe('deriveTree on every prefix of the synthetic run', () => {
  const trees = events.map((_, i) => ({ m: fold(events.slice(0, i + 1)), seq: i + 1 }))

  it('keeps at most one function running, and it is the frontier and the last one drawn', () => {
    for (const { m } of trees) {
      const t = deriveTree(m)
      const running = t.functions.filter((f) => f.status === 'running')
      expect(running.length).toBeLessThanOrEqual(1)
      if (t.frontierId !== null) {
        expect(t.functions.at(-1)?.id).toBe(t.frontierId)
        expect(t.functions.at(-1)?.status).toBe('running')
      }
    }
  })

  it('draws or queues every selected function', () => {
    for (const { m } of trees) {
      if (m.selection === null) continue
      const t = deriveTree(m)
      const queued = t.tail?.tail === 'queued' ? t.tail.queued : 0
      expect(t.functions.length + queued).toBe(m.functionOrder.length)
    }
  })

  it('never removes a node, and a finished function never changes its status', () => {
    let prevIds = new Set<string>()
    const done = new Map<string, string>()
    for (const { m } of trees) {
      const t = deriveTree(m)
      const ids = nodeIds(t)
      for (const id of prevIds) if (id !== 'tail') expect(ids.has(id)).toBe(true)
      prevIds = ids
      for (const f of t.functions) {
        const was = done.get(f.id)
        if (was !== undefined) expect(f.status).toBe(was)
        if (f.status !== 'running') done.set(f.id, f.status)
      }
    }
  })

  it('walks the setup pill through its stages in order', () => {
    const order = ['none', 'clone', 'env', 'discovery', 'triage', 'awaiting', 'selected']
    let at = 0
    for (const { m } of trees) {
      const stage = deriveTree(m).pre.stage
      const i = order.indexOf(stage)
      expect(i).toBeGreaterThanOrEqual(at)
      at = i
    }
    expect(order[at]).toBe('selected')
  })

  it('is the same whether events arrive one by one, in one batch, or twice', () => {
    const one = events.reduce((m, e) => reduce(m, [e]), initialRunModel())
    const all = fold(events)
    expect(deriveTree(all)).toEqual(deriveTree(one))
    expect(deriveTree(reduce(all, events.slice(100)))).toEqual(deriveTree(all))
  })

  it('returns the same tree for the same model', () => {
    const m = fold(events)
    expect(deriveTree(m)).toBe(deriveTree(m))
  })
})

describe('deriveTree at the end of the synthetic run', () => {
  const t = deriveTree(fold(events))

  it('has no frontier, the complete tail and every function in start order', () => {
    expect(t.ended).toBe(true)
    expect(t.frontierId).toBeNull()
    expect(t.tail?.tail).toBe('complete')
    expect(t.functions.map((f) => f.index)).toEqual([0, 1, 2, 3, 4, 5])
  })

  it('maps each outcome onto its nodes', () => {
    const dedupe = byName(t, 'dedupe_preserve_order')
    expect(dedupe.status).toBe('done')
    expect(dedupe.result).toMatchObject({ result: 'merged', winner: 'B' })
    const b = dedupe.candidates.find((c) => c.letter === 'B')
    expect(b).toMatchObject({ verdict: 'merged', tone: 'win', dimmed: false })
    expect(dedupe.candidates.filter((c) => c.dimmed).map((c) => c.letter)).toEqual(['A', 'C'])

    const avg = byName(t, 'moving_average')
    expect(avg.result).toMatchObject({ result: 'reverted', winner: 'A' })
    expect(avg.candidates.find((c) => c.letter === 'A')).toMatchObject({ verdict: 'reverted', tone: 'caution' })

    const topk = byName(t, 'top_k_inplace')
    expect(topk.result?.result).toBe('allrej')
    expect(topk.candidates.map((c) => [c.phase, c.tone])).toEqual([
      ['rejected', 'fail'],
      ['rejected', 'fail'],
      ['rejected', 'fail'],
    ])

    const join = byName(t, 'join_fields')
    expect(join.result?.result).toBe('nochange')
    expect(join.candidates.every((c) => c.dimmed && c.tone === 'muted')).toBe(true)

    for (const name of ['normalize_whitespace', 'parse_records']) {
      const f = byName(t, name)
      expect(f.status).toBe('skipped')
      expect(f.candidates).toEqual([])
      expect(f.result).toBeNull()
    }
  })

  it('splits node ids back into their function', () => {
    expect(functionOfId(`f/${DEDUPE}`)).toBe(DEDUPE)
    expect(functionOfId(`f/${DEDUPE}/B`)).toBe(DEDUPE)
    expect(functionOfId(`f/${DEDUPE}/result`)).toBe(DEDUPE)
    expect(functionOfId('pre')).toBeNull()
  })
})

describe('deriveTree mid-run', () => {
  it('shows queued functions and the live frontier', () => {
    const t = deriveTree(fold(prefix(first('function.baseline.completed', DEDUPE).seq)))
    expect(t.frontierId).toBe(`f/${DEDUPE}`)
    expect(t.functions).toHaveLength(1)
    expect(t.tail).toMatchObject({ tail: 'queued', queued: 5 })
  })

  it('marks a measured candidate that clears the gate as a win before the decision, and only that one', () => {
    const benched = events.filter((e) => e.type === 'candidate.bench.completed' && e.function_id === DEDUPE)
    const t = deriveTree(fold(prefix(benched[1]?.seq ?? 0)))
    const cand = (L: string) => t.functions[0]?.candidates.find((x) => x.letter === L)
    const [c, b] = [cand('C'), cand('B')]
    expect(c).toMatchObject({ verdict: 'not_significant', tone: 'muted', dimmed: false })
    expect(b).toMatchObject({ verdict: 'clears_gate', tone: 'win', dimmed: false })
  })

  it('shows a winner before its merge starts', () => {
    const t = deriveTree(fold(prefix(first('function.decision', DEDUPE).seq)))
    expect(t.functions[0]?.result).toMatchObject({ result: 'winner', winner: 'B' })
    expect(t.functions[0]?.candidates.find((c) => c.letter === 'A')?.verdict).toBe('smaller')
  })

  it('shows a merging winner', () => {
    const t = deriveTree(fold(prefix(first('function.merge.started', DEDUPE).seq)))
    expect(t.functions[0]?.result?.result).toBe('merging')
  })
})

describe('deriveTree on hand-made logs', () => {
  it('orders functions by when they started, not by the selection', () => {
    const sel = first('run.selection.confirmed')
    const ids = sel.data.function_ids
    const swapped = [ids[1] ?? '', ids[0] ?? '', ...ids.slice(2)]
    const evs = prefix(first('function.started', 'datakit.series:moving_average').seq).map((e) =>
      e.seq === sel.seq ? { ...sel, data: { ...sel.data, function_ids: swapped } } : e,
    )
    const m = fold(evs)
    expect(m.functionOrder[0]).toBe('datakit.series:moving_average')
    expect(deriveTree(m).functions.map((f) => f.qualname)).toEqual(['dedupe_preserve_order', 'moving_average'])
  })

  it('calls a significant positive change slower', () => {
    const done = first('candidate.bench.completed', DEDUPE)
    const stats = done.data.stats
    if (stats === null) throw new Error('no stats')
    const slower = { ...done, data: { ...done.data, stats: { ...stats, delta_pct: 12, significant: false, p_value: 0.001 } } }
    const t = deriveTree(fold([...prefix(done.seq - 1), slower]))
    expect(t.functions[0]?.candidates.find((c) => c.letter === 'C')).toMatchObject({
      verdict: 'slower',
      tone: 'muted',
      deltaPct: 12,
    })
  })

  it('shows a failed merge that was not reverted as failed', () => {
    const merge = first('function.merge.completed', DEDUPE)
    const failed = {
      ...merge,
      data: { ...merge.data, ok: false, reverted: false, error: { kind: 'sandbox_error' as const, message: 'merge conflict', detail: null } },
    }
    const t = deriveTree(fold([...prefix(merge.seq - 1), failed]))
    expect(t.functions[0]?.result?.result).toBe('failed')
    expect(t.functions[0]?.candidates.find((c) => c.letter === 'B')?.verdict).toBe('winner')
  })

  it('shows a function cancelled after its decision, and the cancelled run', () => {
    const decision = first('function.decision', DEDUPE)
    const completed = first('function.completed', DEDUPE)
    const evs = append(prefix(decision.seq), [
      { ...completed, data: { ...completed.data, outcome: 'cancelled', winner: null, reason: 'cancelled' } },
      makeEvent(0, 'run.state_changed', { from_state: 'optimizing', to_state: 'cancelled', reason: 'user' }),
      makeEvent(0, 'run.cancelled', { at_state: 'optimizing', reason: 'user' }),
    ])
    const t = deriveTree(fold(evs))
    expect(t.ended).toBe(true)
    expect(t.frontierId).toBeNull()
    expect(t.functions[0]).toMatchObject({ status: 'cancelled', result: { result: 'cancelled' } })
    expect(t.tail?.tail).toBe('cancelled')
  })

  it('keeps an unfinished function after an interrupted run without a frontier', () => {
    const evs = append(prefix(first('function.baseline.completed', DEDUPE).seq), [
      makeEvent(0, 'run.interrupted', { previous_state: 'optimizing', open_steps: [] }),
    ])
    const t = deriveTree(fold(evs))
    expect(t.frontierId).toBeNull()
    expect(t.functions[0]?.status).toBe('running')
    expect(t.tail?.tail).toBe('interrupted')
    expect(t.pre.busy).toBe(false)
  })
})
