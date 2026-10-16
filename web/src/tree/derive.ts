// The run as a tree of nodes: the setup pill, one layer per started function (its pill, its
// candidates and its result), and a tail label. A pure fold of the RunModel; display text lives
// in text.ts and geometry in layout.ts.
import type { BenchStats, CandidateId, RankingEntry } from '../gen/events'
import { selectFunctions } from '../state/selectors'
import type { CandidateModel, FunctionModel, RunModel } from '../state/types'

/** 'pre' | `f/${fid}` | `f/${fid}/${L}` | `f/${fid}/result` | 'tail' */
export type NodeId = string
export type Tone = 'win' | 'muted' | 'caution' | 'fail' | 'plain'

export interface PreNode {
  id: 'pre'
  kind: 'pre'
  stage: 'clone' | 'env' | 'discovery' | 'triage' | 'awaiting' | 'selected' | 'none'
  busy: boolean
}

export type FnStatus = 'running' | 'done' | 'skipped' | 'failed' | 'cancelled'

export interface FnNode {
  id: `f/${string}`
  kind: 'function'
  functionId: string
  /** 0-based position in the selection */
  index: number
  qualname: string
  status: FnStatus
  candidates: CandNode[]
  result: ResultNode | null
}

export type CandPhase =
  | 'writing'
  | 'repairing'
  | 'written'
  | 'checking'
  | 'checked'
  | 'check_failed'
  | 'queued'
  | 'measuring'
  | 'measured'
  | 'rejected'
  | 'failed'

export type CandVerdict =
  | null
  | 'winner'
  | 'merging'
  | 'merged'
  | 'reverted'
  | 'clears_gate'
  | 'smaller'
  | 'below_gate'
  | 'slower'
  | 'not_significant'

export interface CandNode {
  id: string
  kind: 'candidate'
  functionId: string
  letter: CandidateId
  phase: CandPhase
  verdict: CandVerdict
  /** a measured candidate that lost once the decision is in */
  dimmed: boolean
  /** colour of its delta */
  tone: Tone
  deltaPct: number | null
}

export type ResultKind = 'winner' | 'merging' | 'merged' | 'reverted' | 'nochange' | 'allrej' | 'failed' | 'cancelled'

export interface ResultNode {
  id: string
  kind: 'result'
  functionId: string
  result: ResultKind
  winner: CandidateId | null
}

export type TailKind = 'queued' | 'patch' | 'complete' | 'failed' | 'cancelled' | 'interrupted'

export interface TailNode {
  id: 'tail'
  kind: 'tail'
  tail: TailKind
  /** selected functions that have not started */
  queued: number
}

export type TreeNode = PreNode | FnNode | CandNode | ResultNode | TailNode

export interface Tree {
  pre: PreNode
  /** in start order: the open function is always the last one */
  functions: FnNode[]
  tail: TailNode | null
  /** the one open function, while the run goes on */
  frontierId: NodeId | null
  ended: boolean
  lastSeq: number
}

export const LETTERS: readonly CandidateId[] = ['A', 'B', 'C']

export const fnId = (fid: string): `f/${string}` => `f/${fid}`
export const candId = (fid: string, letter: CandidateId): NodeId => `f/${fid}/${letter}`
export const resultId = (fid: string): NodeId => `f/${fid}/result`

/** The gate a candidate must clear, in percent (positive). */
export const gateOf = (m: RunModel, f?: FunctionModel): number =>
  m.meta?.settings.min_effect_pct ?? f?.decision?.rule.min_effect_pct ?? 0

/** The significance level a candidate's p-value must beat. */
export const alphaOf = (m: RunModel, f?: FunctionModel): number =>
  m.meta?.settings.alpha ?? f?.decision?.rule.alpha ?? 0.05

export const rankOf = (f: FunctionModel, letter: CandidateId): RankingEntry | undefined =>
  f.decision?.ranking.find((r) => r.candidate_id === letter)

/** Bench stats once the bench finished ok. */
export const benchOf = (c: CandidateModel): BenchStats | null =>
  c.bench?.status === 'ok' ? (c.bench.result?.stats ?? null) : null

export function candPhase(c: CandidateModel): CandPhase {
  if (c.rejection !== null) return 'rejected'
  if (c.bench !== null) return c.bench.status === 'running' ? 'measuring' : c.bench.status === 'ok' ? 'measured' : 'failed'
  if (c.queued !== null) return 'queued'
  const att = c.attempts.at(-1)
  const check = att?.check ?? null
  if (check !== null) return check.status === 'running' ? 'checking' : check.status === 'ok' ? 'checked' : 'check_failed'
  const write = att?.write ?? null
  if (write?.status === 'failed') return 'failed'
  if (write?.status === 'ok') return 'written'
  return att !== undefined && (att.attempt > 0 || write?.started.kind === 'repair') ? 'repairing' : 'writing'
}

/** Whether a winning merge was undone. */
export const isReverted = (f: FunctionModel): boolean =>
  f.merge?.result?.reverted === true || f.outcome?.outcome === 'reverted'

function candNode(m: RunModel, f: FunctionModel, c: CandidateModel): CandNode {
  const letter = c.candidateId
  const phase = candPhase(c)
  const b = phase === 'measured' ? benchOf(c) : null
  const gate = gateOf(m, f)
  const decision = f.decision
  const winner = decision?.winner ?? null
  let verdict: CandVerdict = null
  let tone: Tone = phase === 'rejected' || phase === 'failed' || phase === 'check_failed' ? 'fail' : 'plain'
  if (decision !== null && winner === letter) {
    const merge = f.merge
    verdict = isReverted(f)
      ? 'reverted'
      : merge === null || merge.status === 'failed'
        ? 'winner'
        : merge.status === 'running'
          ? 'merging'
          : 'merged'
    tone = verdict === 'reverted' ? 'caution' : 'win'
  } else if (b !== null) {
    // `significant` already includes the gate; a real change that misses it shows in the p-value
    const d = b.delta_pct
    const rank = rankOf(f, letter)
    const clears = (rank?.significant ?? b.significant) && d <= -gate
    const p = rank?.p_holm ?? b.p_holm ?? b.p_value
    const real = p !== null && p < alphaOf(m, f)
    if (clears) verdict = decision !== null && winner !== null ? 'smaller' : 'clears_gate'
    else if (real && d <= 0) verdict = 'below_gate'
    else if (real) verdict = 'slower'
    else verdict = 'not_significant'
    tone = decision === null && clears ? 'win' : 'muted'
  }
  return {
    id: candId(f.functionId, letter),
    kind: 'candidate',
    functionId: f.functionId,
    letter,
    phase,
    verdict,
    dimmed: decision !== null && phase === 'measured' && winner !== letter,
    tone,
    deltaPct: b?.delta_pct ?? null,
  }
}

function resultKind(f: FunctionModel): ResultKind | null {
  const decision = f.decision
  if (decision === null) return null
  const outcome = f.outcome?.outcome
  if (outcome === 'cancelled') return 'cancelled'
  // a reverted merge completes with ok false, so it is checked before a failed one
  if (isReverted(f)) return 'reverted'
  if (f.merge?.status === 'failed' || outcome === 'failed') return 'failed'
  if (f.merge?.status === 'ok') return 'merged'
  if (f.merge?.status === 'running') return 'merging'
  if (decision.winner !== null && f.merge === null) return 'winner'
  if (decision.outcome === 'no_significant_win') return 'nochange'
  return 'allrej'
}

function fnStatus(f: FunctionModel): FnStatus {
  const o = f.outcome?.outcome
  if (o === undefined) return 'running'
  if (o.startsWith('skipped')) return 'skipped'
  if (o === 'failed' || o === 'cancelled') return o
  return 'done'
}

function fnNode(m: RunModel, f: FunctionModel): FnNode {
  const fid = f.functionId
  const candidates = LETTERS.flatMap((L) => {
    const c = f.candidates[L]
    return c === undefined ? [] : [candNode(m, f, c)]
  })
  const kind = candidates.length > 0 ? resultKind(f) : null
  return {
    id: fnId(fid),
    kind: 'function',
    functionId: fid,
    index: f.started?.index ?? 0,
    qualname: f.started?.info.qualname ?? f.qualname,
    status: fnStatus(f),
    candidates,
    result:
      kind === null
        ? null
        : { id: resultId(fid), kind: 'result', functionId: fid, result: kind, winner: f.decision?.winner ?? null },
  }
}

function preNode(m: RunModel, ended: boolean): PreNode {
  const s = m.steps
  let stage: PreNode['stage']
  // the furthest step reached; between two steps the pill keeps naming the earlier one
  if (m.selection !== null) stage = 'selected'
  else if (s.triage?.status === 'ok' || m.state === 'awaiting_selection') stage = 'awaiting'
  else if (s.triage !== null || s.discovery?.status === 'ok') stage = 'triage'
  else if (s.discovery !== null) stage = 'discovery'
  else if (s.env !== null) stage = 'env'
  else if (m.meta !== null) stage = 'clone'
  else stage = 'none'
  return { id: 'pre', kind: 'pre', stage, busy: stage !== 'selected' && !ended }
}

function tailNode(m: RunModel, queued: number): TailNode | null {
  const t = m.terminal
  const node = (tail: TailKind): TailNode => ({ id: 'tail', kind: 'tail', tail, queued })
  if (t !== null) {
    return node(
      t.type === 'run.completed'
        ? 'complete'
        : t.type === 'run.failed'
          ? 'failed'
          : t.type === 'run.cancelled'
            ? 'cancelled'
            : 'interrupted',
    )
  }
  if (m.selection === null) return null
  if (queued > 0) return node('queued')
  if (m.steps.artifacts?.status === 'running') return node('patch')
  return null
}

const CACHE = new WeakMap<RunModel, Tree>()

export function deriveTree(m: RunModel): Tree {
  const hit = CACHE.get(m)
  if (hit !== undefined) return hit
  const all = selectFunctions(m)
  const started = all
    .filter((f): f is FunctionModel & { started: NonNullable<FunctionModel['started']> } => f.started !== null)
    .sort((a, b) => a.started.bornSeq - b.started.bornSeq)
  const queued = all.filter((f) => f.started === null && f.outcome === null).length
  const ended = m.terminal !== null
  const functions = started.map((f) => fnNode(m, f))
  if (import.meta.env.DEV && functions.filter((f) => f.status === 'running' && !ended).length > 1) {
    // the grammar keeps one function open at a time; more means the log or the fold is wrong
    console.warn('net-zero tree: more than one function is running', functions)
  }
  const tree: Tree = {
    pre: preNode(m, ended),
    functions,
    tail: tailNode(m, queued),
    frontierId: !ended && m.currentFunctionId !== null ? fnId(m.currentFunctionId) : null,
    ended,
    lastSeq: m.lastSeq,
  }
  CACHE.set(m, tree)
  return tree
}

/** Every node id in the tree. */
export function nodeIds(t: Tree): Set<NodeId> {
  const ids = new Set<NodeId>([t.pre.id])
  for (const f of t.functions) {
    ids.add(f.id)
    for (const c of f.candidates) ids.add(c.id)
    if (f.result !== null) ids.add(f.result.id)
  }
  if (t.tail !== null) ids.add(t.tail.id)
  return ids
}

/** The function id inside a node id, or null for pre and tail. */
export function functionOfId(id: NodeId): string | null {
  if (!id.startsWith('f/')) return null
  const rest = id.slice(2)
  const m = /^(.*)\/(A|B|C|result)$/.exec(rest)
  return m?.[1] ?? rest
}
