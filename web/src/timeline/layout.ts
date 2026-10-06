// Places a run on a grid of columns and lanes, left to right. The layout is append-only:
// an element's position depends only on events up to the one that created it, so as a run
// grows nothing already drawn moves. Positions are element centers in world units, with
// the trunk at y = 0 and candidate lanes below it.
import type { CandidateId, FunctionOutcome } from '../gen/events'
import { selectTotals } from '../state/selectors'
import type { CandidateModel, FunctionModel, RunModel, StepStatus, TestAttempt } from '../state/types'

export const COL = 104
export const LANE = 64
export const CANDIDATE_IDS: readonly CandidateId[] = ['A', 'B', 'C']
export const LANE_Y: Readonly<Record<CandidateId, number>> = { A: LANE, B: 2 * LANE, C: 3 * LANE }

const PRELUDE = ['clone', 'env', 'discovery', 'triage', 'selection'] as const
export type PreludeStep = (typeof PRELUDE)[number]

/** A function's columns, counted from its `picked` node. */
const AT = { picked: 0, tests: 1, inputs: 2, baseline: 3, write: 4, check: 5, bench: 6, verdict: 7, merge: 8 } as const
/** Empty columns between one block (prelude, function, end) and the next. */
const GAP = 1

export type NodeStatus = 'pending' | 'active' | 'ok' | 'fail' | 'caution' | 'muted'
export type EdgeTone = 'idle' | 'lit' | 'fail' | 'muted'
export type Glyph = 'dot' | 'check' | 'cross' | 'diamond' | 'pen'

export type FunctionPart = 'picked' | 'tests' | 'inputs' | 'baseline' | 'verdict' | 'merge' | 'outcome'
export type CandidatePart = 'write' | 'check' | 'bench' | 'cap'

/** What a node or cap stands for, so a click can open its artifact. */
export type NodeRef =
  | { kind: 'prelude'; step: PreludeStep }
  | { kind: 'function'; functionId: string; part: FunctionPart }
  | { kind: 'candidate'; functionId: string; candidateId: CandidateId; part: CandidatePart }
  | { kind: 'end'; part: 'artifacts' | 'terminal' }

export type Chip =
  | { kind: 'tests'; passed: number; total: number }
  | { kind: 'count'; n: number; noun: string }
  /** g CO₂ per 1M calls; negative for a saving */
  | { kind: 'grams'; g: number }
  | { kind: 'delta'; pct: number; significant: boolean }

interface Placed {
  id: string
  x: number
  y: number
  /** seq of the event that created it; animations key on it */
  bornSeq: number
}

export interface LayoutNode extends Placed {
  ref: NodeRef
  glyph: Glyph
  label: string
  status: NodeStatus
  /** on the path a kept change took into the trunk */
  lit: boolean
  chips: Chip[]
}

/** The ⊘ that ends a rejected candidate or a skipped function. */
export interface LayoutCap extends Placed {
  ref: NodeRef
  label: string
  status: 'fail' | 'muted'
  /** the full reason, for a tooltip */
  detail: string
}

export interface Point {
  x: number
  y: number
}

export interface LayoutEdge {
  id: string
  from: Point
  to: Point
  /** line: straight; elbow: turns at `bendX` with rounded corners; merge: an S-curve */
  shape: 'line' | 'elbow' | 'merge'
  bendX: number
  tone: EdgeTone
  dashed: boolean
  bornSeq: number
}

/** A repair: the step went round again `count` times. */
export interface LayoutLoop extends Placed {
  nodeId: string
  count: number
}

export type LayoutLabel = Placed &
  (
    | {
        kind: 'function'
        functionId: string
        qualname: string
        module: string
        index: number
        total: number
        outcome: FunctionOutcome | null
        deltaPct: number | null
      }
    | { kind: 'lane'; text: string; tone: EdgeTone }
    | { kind: 'note'; text: string; tone: EdgeTone }
  )

export interface TimelineLayout {
  nodes: LayoutNode[]
  edges: LayoutEdge[]
  loops: LayoutLoop[]
  caps: LayoutCap[]
  labels: LayoutLabel[]
  /** right edge of the drawing, with room for the labels of its last column */
  width: number
  /** bottom edge of the drawing, with room for the labels of its lowest lane */
  height: number
  /** the rightmost point drawn, where a live run grows; null before anything is */
  tip: (Point & { bornSeq: number }) | null
}

interface Out {
  nodes: LayoutNode[]
  edges: LayoutEdge[]
  loops: LayoutLoop[]
  caps: LayoutCap[]
  labels: LayoutLabel[]
}

/** A run of columns that belongs to the prelude, one function, or the end of the run. */
interface Block {
  /** every column it uses, with the seq that took it */
  marks: { col: number; bornSeq: number }[]
  /** trunk elements, by column */
  trunk: TrunkMark[]
  /** from this seq on the block no longer grows; null while it still may */
  doneSeq: number | null
  /** the furthest column it can reach while it grows */
  reach: number
  /** whether its own edge already leads the trunk out of it */
  exits: boolean
}

interface TrunkMark {
  id: string
  col: number
  bornSeq: number
}

const byCol = (a: { col: number }, b: { col: number }) => a.col - b.col
const maxOf = (xs: readonly number[], fallback: number): number => xs.reduce((a, x) => Math.max(a, x), fallback)

function settledEnd(block: Block): number {
  const done = block.doneSeq ?? Infinity
  return (
    maxOf(
      block.marks.filter((m) => m.bornSeq <= done).map((m) => m.col),
      0,
    ) +
    1 +
    GAP
  )
}

/**
 * Where a block born at `seq` starts. A block still growing then keeps its whole reach, so
 * whatever it adds later cannot run into the next one.
 */
function nextStart(block: Block, seq: number): number {
  return block.doneSeq !== null && block.doneSeq <= seq ? settledEnd(block) : block.reach + 1 + GAP
}

function stepStatus(step: { status: StepStatus } | null, over: boolean): NodeStatus {
  if (step === null) return 'pending'
  if (step.status === 'running') return over ? 'muted' : 'active'
  return step.status === 'ok' ? 'ok' : 'fail'
}

const byAttempt = (a: { attempt: number }, b: { attempt: number }) => a.attempt - b.attempt

function testsChip(result: { passed: number; failed: number; errors: number } | null | undefined): Chip[] {
  if (result == null) return []
  return [{ kind: 'tests', passed: result.passed, total: result.passed + result.failed + result.errors }]
}

const SKIP_LABEL: Partial<Record<FunctionOutcome, { label: string; status: LayoutCap['status'] }>> = {
  skipped_untestable: { label: 'untestable', status: 'muted' },
  skipped_capture: { label: 'not capturable', status: 'muted' },
  failed: { label: 'failed', status: 'fail' },
  cancelled: { label: 'cancelled', status: 'muted' },
}

/** Outcomes where candidates were weighed and the original stayed. */
const KEPT: ReadonlySet<FunctionOutcome> = new Set(['no_significant_win', 'all_rejected', 'reverted'])

class Builder {
  readonly out: Out = { nodes: [], edges: [], loops: [], caps: [], labels: [] }

  node(block: Block, col: number, node: Omit<LayoutNode, 'x'> & { y: number }): LayoutNode {
    const placed = { ...node, x: col * COL }
    this.out.nodes.push(placed)
    block.marks.push({ col, bornSeq: node.bornSeq })
    if (node.y === 0) block.trunk.push({ id: node.id, col, bornSeq: node.bornSeq })
    return placed
  }

  cap(block: Block, col: number, cap: Omit<LayoutCap, 'x'>): LayoutCap {
    const placed = { ...cap, x: col * COL }
    this.out.caps.push(placed)
    block.marks.push({ col, bornSeq: cap.bornSeq })
    if (cap.y === 0) block.trunk.push({ id: cap.id, col, bornSeq: cap.bornSeq })
    return placed
  }

  edge(edge: Omit<LayoutEdge, 'bendX' | 'dashed'> & Partial<Pick<LayoutEdge, 'bendX' | 'dashed'>>): void {
    this.out.edges.push({ bendX: edge.from.x, dashed: false, ...edge })
  }

  /** Straight trunk edges between a block's trunk elements, in column order. */
  trunkEdges(block: Block, tone: (to: TrunkMark) => EdgeTone = () => 'idle'): void {
    const trunk = [...block.trunk].sort(byCol)
    trunk.slice(1).forEach((to, i) => {
      const from = trunk[i]
      if (from === undefined) return
      this.edge({
        id: `${to.id}<`,
        from: { x: from.col * COL, y: 0 },
        to: { x: to.col * COL, y: 0 },
        shape: 'line',
        tone: tone(to),
        bornSeq: Math.max(from.bornSeq, to.bornSeq),
      })
    })
  }
}

function placePrelude(model: RunModel, b: Builder): Block {
  const block: Block = {
    marks: [],
    trunk: [],
    doneSeq: model.selection?.bornSeq ?? model.terminal?.bornSeq ?? null,
    reach: PRELUDE.length - 1,
    exits: false,
  }
  const over = model.terminal !== null
  const chips: Partial<Record<PreludeStep, Chip[]>> = {
    discovery:
      model.steps.discovery?.result?.ok === true
        ? [{ kind: 'count', n: model.steps.discovery.result.n_functions, noun: 'functions' }]
        : [],
    selection:
      model.selection === null ? [] : [{ kind: 'count', n: model.selection.functionIds.length, noun: 'picked' }],
  }
  const steps: Record<PreludeStep, { bornSeq: number; status: NodeStatus } | null> = {
    clone: model.steps.clone && { bornSeq: model.steps.clone.bornSeq, status: stepStatus(model.steps.clone, over) },
    env: model.steps.env && { bornSeq: model.steps.env.bornSeq, status: stepStatus(model.steps.env, over) },
    discovery: model.steps.discovery && {
      bornSeq: model.steps.discovery.bornSeq,
      status: stepStatus(model.steps.discovery, over),
    },
    triage: model.steps.triage && { bornSeq: model.steps.triage.bornSeq, status: stepStatus(model.steps.triage, over) },
    selection: model.selection && { bornSeq: model.selection.bornSeq, status: 'ok' },
  }
  PRELUDE.forEach((step, col) => {
    const s = steps[step]
    if (s === null) return
    b.node(block, col, {
      id: `run/${step}`,
      ref: { kind: 'prelude', step },
      y: 0,
      bornSeq: s.bornSeq,
      glyph: 'dot',
      label: step,
      status: s.status,
      lit: false,
      chips: chips[step] ?? [],
    })
  })
  b.trunkEdges(block)
  if (model.selection !== null) {
    const from = PRELUDE.length - 1
    b.edge({
      id: 'run/selection>',
      from: { x: from * COL, y: 0 },
      to: { x: settledEnd(block) * COL, y: 0 },
      shape: 'line',
      tone: 'idle',
      bornSeq: model.selection.bornSeq,
    })
    block.exits = true
  }
  return block
}

/** The decision's verdict on significance wins over the bench's own, once there is one. */
function significance(fn: FunctionModel, c: CandidateModel): boolean | null {
  const ranked = fn.decision?.ranking.find((r) => r.candidate_id === c.candidateId)
  return ranked?.significant ?? c.bench?.result?.stats?.significant ?? null
}

interface CandidateView {
  /** on the path into a kept merge */
  lit: boolean
  /** benched fine but the change was not significant */
  faded: boolean
}

function candidateView(fn: FunctionModel, c: CandidateModel): CandidateView {
  const reverted = fn.merge?.result?.reverted === true || fn.outcome?.outcome === 'reverted'
  return {
    lit: fn.decision?.winner === c.candidateId && !reverted,
    faded: c.rejection === null && c.bench?.status === 'ok' && significance(fn, c) === false,
  }
}

function placeCandidate(fn: FunctionModel, c: CandidateModel, start: number, over: boolean, block: Block, b: Builder) {
  const cid = c.candidateId
  const fid = fn.functionId
  const y = LANE_Y[cid]
  const id = (part: CandidatePart) => `${fid}/${cid}/${part}`
  const ref = (part: CandidatePart): NodeRef => ({ kind: 'candidate', functionId: fid, candidateId: cid, part })
  const view = candidateView(fn, c)
  const fade = (s: NodeStatus): NodeStatus => (view.faded && s === 'ok' ? 'muted' : s)
  const laneTone: EdgeTone = view.lit ? 'lit' : view.faded ? 'muted' : 'idle'

  const lane: { col: number; bornSeq: number; id: string; tone: EdgeTone }[] = []
  const attempts = [...c.attempts].sort(byAttempt)
  const first = attempts[0]
  const last = attempts.at(-1)

  if (first !== undefined && last !== undefined) {
    const write = b.node(block, start + AT.write, {
      id: id('write'),
      ref: ref('write'),
      y,
      bornSeq: first.bornSeq,
      glyph: 'pen',
      label: 'write',
      status: fade(stepStatus(last.write, over)),
      lit: view.lit,
      chips: [],
    })
    lane.push({ col: AT.write, bornSeq: write.bornSeq, id: write.id, tone: laneTone })
    const repair = attempts[1]
    if (repair !== undefined) {
      b.out.loops.push({
        id: `${write.id}/loop`,
        nodeId: write.id,
        x: write.x,
        y,
        count: attempts.length - 1,
        bornSeq: repair.bornSeq,
      })
    }
  }

  const firstCheck = attempts.find((a) => a.check !== null)?.check
  const lastCheck = attempts.filter((a) => a.check !== null).at(-1)?.check
  if (firstCheck != null && last !== undefined) {
    // a check from an earlier attempt while the newest one rewrites: it is being repaired
    const status = last.check === null ? (over ? 'muted' : 'caution') : stepStatus(last.check, over)
    const check = b.node(block, start + AT.check, {
      id: id('check'),
      ref: ref('check'),
      y,
      bornSeq: firstCheck.bornSeq,
      glyph: status === 'fail' ? 'cross' : 'check',
      label: 'check',
      status: fade(status),
      lit: view.lit,
      chips: testsChip(lastCheck?.result?.tests),
    })
    lane.push({ col: AT.check, bornSeq: check.bornSeq, id: check.id, tone: laneTone })
  }

  const benchBorn = Math.min(c.queued?.bornSeq ?? Infinity, c.bench?.bornSeq ?? Infinity)
  if (benchBorn !== Infinity) {
    const stats = c.bench?.result?.stats ?? null
    const bench = b.node(block, start + AT.bench, {
      id: id('bench'),
      ref: ref('bench'),
      y,
      bornSeq: benchBorn,
      glyph: 'diamond',
      label: 'bench',
      status: fade(c.bench === null ? (over ? 'muted' : 'pending') : stepStatus(c.bench, over)),
      lit: view.lit,
      chips:
        stats === null
          ? []
          : [{ kind: 'delta', pct: stats.delta_pct, significant: significance(fn, c) ?? stats.significant }],
    })
    lane.push({ col: AT.bench, bornSeq: bench.bornSeq, id: bench.id, tone: laneTone })
  }

  if (c.rejection !== null) {
    const rejectedAt = c.rejection.bornSeq
    const reached = maxOf(
      lane.filter((l) => l.bornSeq <= rejectedAt).map((l) => l.col),
      AT.write - 1,
    )
    const cap = b.cap(block, start + reached + 1, {
      id: id('cap'),
      ref: ref('cap'),
      y,
      bornSeq: rejectedAt,
      label: c.rejection.reason.replaceAll('_', ' '),
      status: 'fail',
      detail: c.rejection.detail,
    })
    lane.push({ col: reached + 1, bornSeq: cap.bornSeq, id: cap.id, tone: 'fail' })
  }

  lane.sort(byCol)
  const head = lane[0]
  if (head === undefined) return
  const bendX = (start + AT.inputs + 0.5) * COL
  b.edge({
    id: `${head.id}<`,
    from: { x: (start + AT.inputs) * COL, y: 0 },
    to: { x: (start + head.col) * COL, y },
    shape: 'elbow',
    bendX,
    tone: head.tone,
    bornSeq: head.bornSeq,
  })
  b.out.labels.push({
    kind: 'lane',
    id: `${fid}/${cid}/lane`,
    x: bendX,
    y,
    bornSeq: head.bornSeq,
    text: cid.toLowerCase(),
    tone: laneTone,
  })
  lane.slice(1).forEach((to, i) => {
    const from = lane[i]
    if (from === undefined) return
    b.edge({
      id: `${to.id}<`,
      from: { x: (start + from.col) * COL, y },
      to: { x: (start + to.col) * COL, y },
      shape: 'line',
      tone: to.tone,
      bornSeq: to.bornSeq,
    })
  })

  if (fn.decision?.winner === cid && fn.merge !== null) {
    const tail = lane.at(-1) ?? head
    b.edge({
      id: `${fid}/merge<${cid}`,
      from: { x: (start + tail.col) * COL, y },
      to: { x: (start + AT.merge) * COL, y: 0 },
      shape: 'merge',
      tone: view.lit ? 'lit' : 'fail',
      bornSeq: fn.merge.bornSeq,
    })
  }
}

function testsStatus(t: TestAttempt, over: boolean): NodeStatus {
  const steps = [t.write, ...t.runs].filter((s) => s !== null)
  if (steps.some((s) => s.status === 'failed')) return 'fail'
  if (t.runs.length > 0 && steps.every((s) => s.status === 'ok')) return 'ok'
  return over ? 'muted' : 'active'
}

function placeFunction(model: RunModel, fn: FunctionModel, start: number, b: Builder): Block {
  const fid = fn.functionId
  const bornSeq = fn.started?.bornSeq ?? fn.bornSeq
  const block: Block = {
    marks: [],
    trunk: [],
    doneSeq: fn.outcome?.bornSeq ?? model.terminal?.bornSeq ?? null,
    reach: start + AT.merge + 1,
    exits: false,
  }
  const over = fn.outcome !== null || model.terminal !== null
  const id = (part: FunctionPart) => `${fid}/${part}`
  const ref = (part: FunctionPart): NodeRef => ({ kind: 'function', functionId: fid, part })
  const trunkNode = (
    part: Exclude<FunctionPart, 'outcome'>,
    node: Omit<LayoutNode, 'id' | 'ref' | 'x' | 'y' | 'lit'> & { lit?: boolean },
  ) => b.node(block, start + AT[part], { lit: false, ...node, id: id(part), ref: ref(part), y: 0 })

  trunkNode('picked', { bornSeq, glyph: 'dot', label: 'picked', status: 'ok', chips: [] })

  const testAttempts = [...fn.tests].sort(byAttempt)
  const firstTests = testAttempts[0]
  const lastTests = testAttempts.at(-1)
  if (lastTests !== undefined && firstTests !== undefined) {
    const status = testsStatus(lastTests, over)
    const result = [...lastTests.runs].reverse().find((r) => r.result?.result != null)?.result?.result
    const tests = trunkNode('tests', {
      bornSeq: firstTests.bornSeq,
      glyph: status === 'fail' ? 'cross' : 'check',
      label: 'tests',
      status,
      chips: testsChip(result),
    })
    const repair = testAttempts[1]
    if (repair !== undefined) {
      const count = testAttempts.length - 1
      b.out.loops.push({ id: `${tests.id}/loop`, nodeId: tests.id, x: tests.x, y: 0, count, bornSeq: repair.bornSeq })
    }
  }

  if (fn.capture !== null) {
    const r = fn.capture.result
    trunkNode('inputs', {
      bornSeq: fn.capture.bornSeq,
      glyph: 'dot',
      label: 'inputs',
      status: stepStatus(fn.capture, over),
      chips: r?.ok === true ? [{ kind: 'count', n: r.n_kept, noun: 'calls' }] : [],
    })
  }

  if (fn.baseline !== null) {
    const g = fn.baseline.result?.original?.g_per_call.mean
    trunkNode('baseline', {
      bornSeq: fn.baseline.bornSeq,
      glyph: 'diamond',
      label: 'baseline',
      status: stepStatus(fn.baseline, over),
      chips: g === undefined ? [] : [{ kind: 'grams', g: g * 1e6 }],
    })
  }

  for (const cid of CANDIDATE_IDS) {
    const c = fn.candidates[cid]
    if (c !== undefined) placeCandidate(fn, c, start, over, block, b)
  }

  const decision = fn.decision
  const reverted = fn.merge?.result?.reverted === true
  if (decision !== null) {
    const status: NodeStatus =
      decision.outcome === 'winner' ? 'ok' : decision.outcome === 'all_rejected' ? 'fail' : 'muted'
    trunkNode('verdict', {
      bornSeq: decision.bornSeq,
      glyph: 'dot',
      label:
        decision.outcome === 'winner' && decision.winner !== null
          ? `winner ${decision.winner.toLowerCase()}`
          : decision.outcome === 'all_rejected'
            ? 'all rejected'
            : 'no win',
      status,
      lit: decision.outcome === 'winner' && !reverted,
      chips: [],
    })
  }

  if (fn.merge !== null) {
    const status = reverted ? 'fail' : stepStatus(fn.merge, over)
    const saved = fn.outcome?.outcome === 'accepted' ? fn.outcome.g_saved_per_1m_calls : null
    trunkNode('merge', {
      bornSeq: fn.merge.bornSeq,
      glyph: status === 'fail' ? 'cross' : 'dot',
      label: reverted ? 'reverted' : status === 'ok' ? 'merged' : 'merge',
      status,
      lit: status === 'ok',
      chips: saved == null ? [] : [{ kind: 'grams', g: -saved }],
    })
  }

  const outcome = fn.outcome
  const skip = outcome === null ? undefined : SKIP_LABEL[outcome.outcome]
  if (outcome !== null && skip !== undefined) {
    const col =
      maxOf(
        block.trunk.filter((t) => t.bornSeq <= outcome.bornSeq).map((t) => t.col),
        start,
      ) + 1
    b.cap(block, col, {
      id: id('outcome'),
      ref: ref('outcome'),
      y: 0,
      bornSeq: outcome.bornSeq,
      label: skip.label,
      status: skip.status,
      detail: outcome.reason,
    })
  }

  b.trunkEdges(block, (to) => (to.id === id('outcome') ? 'muted' : 'idle'))

  b.out.labels.push({
    kind: 'function',
    id: `${fid}/title`,
    x: start * COL,
    y: 0,
    bornSeq,
    functionId: fid,
    qualname: fn.started?.info.qualname ?? fn.qualname,
    module: fn.started?.info.module ?? '',
    index: fn.started?.index ?? 0,
    total: fn.started?.total ?? model.functionOrder.length,
    outcome: outcome?.outcome ?? null,
    deltaPct: outcome?.outcome === 'accepted' ? outcome.delta_pct : null,
  })

  if (outcome !== null) {
    const exit = [...block.trunk].sort(byCol).at(-1)
    if (exit !== undefined) {
      const accepted = outcome.outcome === 'accepted'
      const from = { x: exit.col * COL, y: 0 }
      const to = { x: settledEnd(block) * COL, y: 0 }
      b.edge({
        id: `${fid}>`,
        from,
        to,
        shape: 'line',
        tone: accepted ? 'lit' : 'muted',
        dashed: !accepted,
        bornSeq: outcome.bornSeq,
      })
      if (KEPT.has(outcome.outcome)) {
        b.out.labels.push({
          kind: 'note',
          id: `${fid}/kept`,
          x: (from.x + to.x) / 2,
          y: 0,
          bornSeq: outcome.bornSeq,
          text: 'original kept',
          tone: 'muted',
        })
      }
      block.exits = true
    }
  }
  return block
}

const TERMINAL_LABEL = {
  'run.completed': 'done',
  'run.failed': 'failed',
  'run.cancelled': 'cancelled',
  'run.interrupted': 'interrupted',
} as const

function placeEnd(model: RunModel, start: number, b: Builder): Block {
  const block: Block = { marks: [], trunk: [], doneSeq: null, reach: start + 1, exits: true }
  const artifacts = model.steps.artifacts
  if (artifacts !== null) {
    b.node(block, start, {
      id: 'run/artifacts',
      ref: { kind: 'end', part: 'artifacts' },
      y: 0,
      bornSeq: artifacts.bornSeq,
      glyph: 'pen',
      label: 'patch',
      status: stepStatus(artifacts, model.terminal !== null),
      lit: false,
      chips: [],
    })
  }
  const terminal = model.terminal
  if (terminal !== null) {
    const completed = terminal.type === 'run.completed'
    const saved = completed ? selectTotals(model).g_saved_per_1m_calls : 0
    b.node(block, artifacts !== null && artifacts.bornSeq < terminal.bornSeq ? start + 1 : start, {
      id: 'run/end',
      ref: { kind: 'end', part: 'terminal' },
      y: 0,
      bornSeq: terminal.bornSeq,
      glyph: completed ? 'check' : 'cross',
      label: TERMINAL_LABEL[terminal.type],
      status: completed
        ? 'ok'
        : terminal.type === 'run.interrupted'
          ? 'caution'
          : terminal.type === 'run.failed'
            ? 'fail'
            : 'muted',
      lit: completed && saved > 0,
      chips: saved > 0 ? [{ kind: 'grams', g: -saved }] : [],
    })
  }
  b.trunkEdges(block, () => (terminal?.type === 'run.completed' ? 'lit' : 'idle'))
  return block
}

/** Joins a block to the one before it when that one stopped without an edge of its own. */
function connect(prev: Block, next: Block, b: Builder): void {
  const head = [...next.trunk].sort(byCol)[0]
  if (prev.exits || head === undefined) return
  const tail = [...prev.trunk]
    .filter((t) => t.bornSeq < head.bornSeq)
    .sort(byCol)
    .at(-1)
  if (tail === undefined) return
  b.edge({
    id: `${head.id}<<`,
    from: { x: tail.col * COL, y: 0 },
    to: { x: head.col * COL, y: 0 },
    shape: 'line',
    tone: 'muted',
    dashed: true,
    bornSeq: head.bornSeq,
  })
}

function bounds(out: Out): Pick<TimelineLayout, 'width' | 'height' | 'tip'> {
  const points: (Point & { bornSeq: number })[] = [
    ...out.nodes,
    ...out.caps,
    ...out.edges.map((e) => ({ ...e.to, bornSeq: e.bornSeq })),
  ]
  let tip: TimelineLayout['tip'] = null
  for (const p of points) {
    if (tip === null || p.x > tip.x || (p.x === tip.x && p.bornSeq > tip.bornSeq))
      tip = { x: p.x, y: p.y, bornSeq: p.bornSeq }
  }
  return {
    width:
      maxOf(
        points.map((p) => p.x),
        0,
      ) + COL,
    height:
      maxOf(
        points.map((p) => p.y),
        0,
      ) + LANE,
    tip,
  }
}

export function layout(model: RunModel): TimelineLayout {
  const b = new Builder()
  let prev = placePrelude(model, b)

  const started = model.functionOrder
    .flatMap((fid) => {
      const fn = model.functions[fid]
      return fn?.started == null ? [] : [{ fn, seq: fn.started.bornSeq }]
    })
    .sort((a, c) => a.seq - c.seq)
  for (const { fn, seq } of started) {
    const block = placeFunction(model, fn, nextStart(prev, seq), b)
    connect(prev, block, b)
    prev = block
  }

  const endSeq = Math.min(model.steps.artifacts?.bornSeq ?? Infinity, model.terminal?.bornSeq ?? Infinity)
  if (endSeq !== Infinity) connect(prev, placeEnd(model, nextStart(prev, endSeq), b), b)

  return { ...b.out, ...bounds(b.out) }
}
