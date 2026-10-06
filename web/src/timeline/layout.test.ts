import { describe, expect, it } from 'vitest'
import type { RunEvent } from '../gen/events'
import { initialRunModel, reduce } from '../state/reducer'
import { buildSyntheticRun } from '../synth/scenario'
import { makeEvent } from '../test/events'
import { COL, LANE, layout, type TimelineLayout } from './layout'

const { events } = buildSyntheticRun()

/** The layout after every prefix of `evs`, folding one event at a time. */
function layouts(evs: readonly RunEvent[]): TimelineLayout[] {
  let model = initialRunModel()
  return evs.map((e) => {
    model = reduce(model, [e])
    return layout(model)
  })
}

const final = layout(reduce(initialRunModel(), events))

const DEDUPE = 'textkit.dedupe:dedupe_preserve_order'
const MOVING = 'datakit.series:moving_average'
const TOP_K = 'datakit.rank:top_k_inplace'
const JOIN = 'textkit.fields:join_fields'
const NORMALIZE = 'textkit.normalize:normalize_whitespace'
const PARSE = 'datakit.io_free:parse_records'

const nodeOf = (l: TimelineLayout, id: string) => {
  const n = l.nodes.find((x) => x.id === id)
  if (n === undefined) throw new Error(`no node ${id}`)
  return n
}
const capOf = (l: TimelineLayout, id: string) => {
  const c = l.caps.find((x) => x.id === id)
  if (c === undefined) throw new Error(`no cap ${id}`)
  return c
}
const edgeOf = (l: TimelineLayout, id: string) => {
  const e = l.edges.find((x) => x.id === id)
  if (e === undefined) throw new Error(`no edge ${id}`)
  return e
}
const col = (x: number) => x / COL

/** Every placed thing as `kind:id`, so the stability check covers all of them. */
function positions(l: TimelineLayout): Map<string, string> {
  const out = new Map<string, string>()
  for (const n of l.nodes) out.set(`node:${n.id}`, `${n.x},${n.y}`)
  for (const c of l.caps) out.set(`cap:${c.id}`, `${c.x},${c.y}`)
  for (const lp of l.loops) out.set(`loop:${lp.id}`, `${lp.x},${lp.y}`)
  for (const lb of l.labels) out.set(`label:${lb.id}`, `${lb.x},${lb.y}`)
  for (const e of l.edges) out.set(`edge:${e.id}`, `${e.from.x},${e.from.y}>${e.bendX}>${e.to.x},${e.to.y}`)
  return out
}

function expectAppendOnly(sequence: readonly TimelineLayout[]) {
  const seen = new Map<string, string>()
  let width = 0
  let tipX = -Infinity
  sequence.forEach((l, i) => {
    const cells = [...l.nodes, ...l.caps].map((n) => `${n.x},${n.y}`)
    expect(new Set(cells).size, `overlapping elements after event ${i}`).toBe(cells.length)
    const ids = [...l.nodes, ...l.caps, ...l.edges, ...l.loops, ...l.labels].map((x) => x.id)
    expect(new Set(ids).size, `duplicate ids after event ${i}`).toBe(ids.length)
    for (const [id, at] of positions(l)) {
      const before = seen.get(id)
      if (before !== undefined) expect(at, `${id} moved at event ${i}`).toBe(before)
      seen.set(id, at)
    }
    expect(l.width).toBeGreaterThanOrEqual(width)
    width = l.width
    expect(l.tip?.x ?? -Infinity).toBeGreaterThanOrEqual(tipX)
    tipX = l.tip?.x ?? -Infinity
  })
}

describe('layout', () => {
  it('never moves, removes or overlaps what it placed as the synthetic run grows', () => {
    const sequence = layouts(events)
    expectAppendOnly(sequence)
    for (const [i, l] of sequence.entries()) {
      const next = sequence[i + 1]
      if (next === undefined) continue
      const ids = new Set(positions(next).keys())
      const lost = [...positions(l).keys()].filter((id) => !ids.has(id))
      expect(lost, `removed after event ${i + 1}`).toEqual([])
    }
  })

  it('keeps its own reach for a function that is still running when the next one starts', () => {
    const dropped = events.filter((e) => !(e.type === 'function.completed' && e.function_id === DEDUPE))
    const sequence = layouts(dropped)
    expectAppendOnly(sequence)
    const l = sequence.at(-1)
    if (l === undefined) throw new Error('no layout')
    const start = col(nodeOf(l, `${DEDUPE}/picked`).x)
    expect(col(nodeOf(l, `${MOVING}/picked`).x)).toBe(start + 11)
    expect(edgeOf(l, `${MOVING}/picked<<`)).toMatchObject({ dashed: true, tone: 'muted' })
  })

  it('lays the prelude on the first trunk columns and starts the first function after a gap', () => {
    const steps = ['clone', 'env', 'discovery', 'triage', 'selection']
    steps.forEach((step, i) => expect(nodeOf(final, `run/${step}`)).toMatchObject({ x: i * COL, y: 0 }))
    expect(nodeOf(final, `${DEDUPE}/picked`)).toMatchObject({ x: 6 * COL, y: 0 })
    expect(edgeOf(final, 'run/selection>').to).toEqual({ x: 6 * COL, y: 0 })
  })

  it('draws an accepted function as a winner curving into a neon merge', () => {
    const start = col(nodeOf(final, `${DEDUPE}/picked`).x)
    expect(nodeOf(final, `${DEDUPE}/B/bench`)).toMatchObject({ x: (start + 6) * COL, y: 2 * LANE, lit: true })
    expect(edgeOf(final, `${DEDUPE}/merge<B`)).toMatchObject({ shape: 'merge', tone: 'lit' })
    const merge = nodeOf(final, `${DEDUPE}/merge`)
    expect(merge).toMatchObject({ x: (start + 8) * COL, status: 'ok', lit: true, label: 'merged' })
    expect(merge.chips[0]).toMatchObject({ kind: 'grams' })
    expect(edgeOf(final, `${DEDUPE}>`)).toMatchObject({ tone: 'lit', dashed: false })
    expect(nodeOf(final, `${DEDUPE}/verdict`)).toMatchObject({ label: 'winner b', lit: true })
    expect(nodeOf(final, `${DEDUPE}/A/bench`)).toMatchObject({ status: 'ok', lit: false })
  })

  it('fades a candidate that passed but was not significant', () => {
    const c = nodeOf(final, `${DEDUPE}/C/bench`)
    expect(c.status).toBe('muted')
    expect(c.chips[0]).toMatchObject({ kind: 'delta', significant: false })
    expect(edgeOf(final, `${DEDUPE}/C/bench<`).tone).toBe('muted')
  })

  it('shows repairs as loops on the node that went round again', () => {
    expect(final.loops.find((l) => l.nodeId === `${DEDUPE}/tests`)?.count).toBe(1)
    expect(final.loops.find((l) => l.nodeId === `${DEDUPE}/A/write`)?.count).toBe(1)
    expect(final.loops.find((l) => l.nodeId === `${NORMALIZE}/tests`)?.count).toBe(2)
  })

  it('marks a reverted merge as failed and keeps the original', () => {
    expect(nodeOf(final, `${MOVING}/merge`)).toMatchObject({ status: 'fail', lit: false, label: 'reverted' })
    expect(edgeOf(final, `${MOVING}/merge<A`).tone).toBe('fail')
    expect(nodeOf(final, `${MOVING}/A/bench`).lit).toBe(false)
    expect(edgeOf(final, `${MOVING}>`)).toMatchObject({ tone: 'muted', dashed: true })
    expect(final.labels.find((l) => l.id === `${MOVING}/kept`)).toMatchObject({ text: 'original kept' })
  })

  it('ends each rejected candidate in a cap one column past where it got to', () => {
    const start = col(nodeOf(final, `${TOP_K}/picked`).x)
    const reasons = { A: 'syntax', B: 'tests failed', C: 'differential mismatch' }
    for (const [cid, label] of Object.entries(reasons)) {
      const check = nodeOf(final, `${TOP_K}/${cid}/check`)
      expect(check.status).toBe('fail')
      expect(capOf(final, `${TOP_K}/${cid}/cap`)).toMatchObject({
        x: (start + 6) * COL,
        y: check.y,
        label,
        status: 'fail',
      })
      expect(final.nodes.some((n) => n.id === `${TOP_K}/${cid}/bench`)).toBe(false)
    }
    expect(nodeOf(final, `${TOP_K}/verdict`)).toMatchObject({ status: 'fail', label: 'all rejected' })
    expect(final.labels.find((l) => l.id === `${TOP_K}/kept`)).toBeDefined()
  })

  it('keeps the original without a significant win', () => {
    for (const cid of ['A', 'B', 'C']) expect(nodeOf(final, `${JOIN}/${cid}/bench`).status).toBe('muted')
    expect(nodeOf(final, `${JOIN}/verdict`)).toMatchObject({ status: 'muted', label: 'no win' })
    expect(final.nodes.some((n) => n.id === `${JOIN}/merge`)).toBe(false)
    expect(edgeOf(final, `${JOIN}>`)).toMatchObject({ tone: 'muted', dashed: true })
  })

  it('stubs a skipped function with its reason', () => {
    const tests = nodeOf(final, `${NORMALIZE}/tests`)
    expect(tests.status).toBe('fail')
    expect(capOf(final, `${NORMALIZE}/outcome`)).toMatchObject({
      x: tests.x + COL,
      y: 0,
      label: 'untestable',
      status: 'muted',
    })
    const inputs = nodeOf(final, `${PARSE}/inputs`)
    expect(inputs.status).toBe('fail')
    expect(capOf(final, `${PARSE}/outcome`)).toMatchObject({ x: inputs.x + COL, label: 'not capturable' })
    expect(edgeOf(final, `${PARSE}/outcome<`).tone).toBe('muted')
    expect(final.labels.find((l) => l.id === `${PARSE}/kept`)).toBeUndefined()
  })

  it('ends at the terminal node, which is the tip', () => {
    const end = nodeOf(final, 'run/end')
    expect(end).toMatchObject({ label: 'done', status: 'ok' })
    expect(final.tip).toMatchObject({ x: end.x, y: 0 })
    expect(final.width).toBe(end.x + COL)
  })

  it('shows running steps as active, and as muted once the run has stopped', () => {
    const benchStarted = events.findIndex((e) => e.type === 'candidate.bench.started')
    const prefix = events.slice(0, benchStarted + 1)
    const bench = events[benchStarted]
    if (bench === undefined) throw new Error('no bench in the synthetic run')
    const id = `${bench.function_id}/${bench.candidate_id}/bench`
    expect(nodeOf(layout(reduce(initialRunModel(), prefix)), id).status).toBe('active')
    const cancelled = makeEvent(bench.seq + 1, 'run.cancelled', { at_state: 'optimizing', reason: 'user' })
    const stopped = layout(reduce(initialRunModel(), [...prefix, cancelled]))
    expect(nodeOf(stopped, id).status).toBe('muted')
    expect(nodeOf(stopped, 'run/end')).toMatchObject({ label: 'cancelled', status: 'muted' })
  })

  it('is empty before the run has done anything', () => {
    expect(layout(initialRunModel())).toMatchObject({ nodes: [], edges: [], tip: null })
  })
})
