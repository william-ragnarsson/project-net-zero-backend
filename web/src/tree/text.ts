// What each tree node says: pill faces, live card footers, tooltips and aria labels. Every
// string comes from the RunModel. A string that starts a pill, label or footer is in sentence
// case; words that follow a label in the same pill stay lowercase. No full stops.
import type { CandidateCheckCompletedData, CandidateId, PytestResult } from '../gen/events'
import { formatClock, formatMass, formatPct, NONE } from '../lib/format'
import type { CandidateModel, FunctionModel, RunModel } from '../state/types'
import {
  benchOf,
  gateOf,
  rankOf,
  type CandNode,
  type FnNode,
  type PreNode,
  type ResultNode,
  type TailNode,
  type Tone,
} from './derive'

/** The mark that goes with a status. Glyph.tsx draws only `ok` and `x`; the words carry the rest. */
export type Glyph = 'spin' | 'ok' | 'x' | 'dash' | 'rv' | 'mark-win' | 'mark-lo' | 'mark-ns' | 'mark-rv' | 'mark-q'

export interface Status {
  text: string
  tone: Tone
  glyph: Glyph | null
  /** the step is still running: drawn in a softer colour, never animated */
  busy: boolean
}

const status = (text: string, tone: Tone = 'muted', glyph: Glyph | null = null, busy = false): Status => ({
  text,
  tone,
  glyph,
  busy,
})

/** A busy status once the run has ended loses its spinner: nothing runs any more. */
const settle = (s: Status, live: boolean): Status =>
  live || !s.busy ? s : { ...s, busy: false, glyph: s.glyph === 'spin' ? null : s.glyph }

export const pad2 = (n: number): string => String(n).padStart(2, '0')

/** The first letter capitalised, for a string that starts a footer, label or tooltip. */
export const sentence = (s: string): string => s.charAt(0).toUpperCase() + s.slice(1)

export const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? '' : 's'}`

export const pytestTotal = (r: PytestResult): number => r.passed + r.failed + r.errors

export const lastSegment = (nodeid: string): string => nodeid.split('::').at(-1) ?? nodeid

// ---------------------------------------------------------------- pre

export function nFunctions(m: RunModel): number | null {
  return m.steps.discovery?.result?.n_functions ?? null
}

export function preText(pre: PreNode, m: RunModel): Status {
  const n = nFunctions(m)
  const nText = n === null ? '' : `${n} `
  switch (pre.stage) {
    case 'selected':
      return status(
        n === null
          ? `${plural(m.selection?.functionIds.length ?? 0, 'function')} selected`
          : `${m.selection?.functionIds.length ?? 0} of ${plural(n, 'function')} selected`,
        'plain',
      )
    case 'awaiting':
      return settle(status(`${n ?? 0} found · waiting for selection`, 'muted', 'spin', true), pre.busy)
    case 'triage':
      return settle(
        status(m.steps.triage === null ? 'ranking functions' : `ranking ${nText}functions`, 'muted', 'spin', true),
        pre.busy,
      )
    case 'discovery':
      return settle(status('finding functions', 'muted', 'spin', true), pre.busy)
    case 'env':
      return settle(status('setting up the environment', 'muted', 'spin', true), pre.busy)
    case 'clone':
      return settle(status('cloning the repository', 'muted', 'spin', true), pre.busy)
    case 'none':
      return settle(status('starting', 'muted', 'spin', true), pre.busy)
  }
}

// ---------------------------------------------------------------- functions

export function skipText(f: FunctionModel): string {
  const o = f.outcome?.outcome ?? ''
  if (o === 'skipped_untestable') return 'untestable'
  if (o === 'skipped_capture') return f.capture?.result?.deterministic === false ? 'not deterministic' : 'capture failed'
  return o.replace(/^skipped_?/, '').replace(/_/g, ' ') || 'skipped'
}

/** The baseline grams per call, once measured. */
export const baselineG = (f: FunctionModel): number | null =>
  f.baseline?.status === 'ok' ? (f.baseline.result?.original?.g_per_call.mean ?? null) : null

/** The tests' latest finished run on the original. */
function lastTestRun(f: FunctionModel) {
  const t = f.tests.at(-1)
  const run = t?.runs.at(-1)
  return { t, run }
}

/** The footer of a function's live card. */
export function fnStatus(node: FnNode, f: FunctionModel, live: boolean): Status & { mass: string | null } {
  const plain = (s: Status) => ({ ...settle({ ...s, text: sentence(s.text) }, live), mass: null })
  if (node.status === 'skipped') return plain(status(`skipped · ${skipText(f)}`, 'fail', 'x'))
  if (node.status === 'failed') return plain(status(`failed · ${f.outcome?.reason ?? ''}`, 'fail', 'x'))
  if (node.status === 'cancelled') return plain(status('cancelled', 'muted', 'dash'))
  const g = baselineG(f)
  if (g !== null) return { ...status('Baseline'), mass: `${formatMass(g)}/call` }
  if (f.baseline?.status === 'running') return plain(status('measuring baseline', 'muted', 'spin', true))
  if (f.capture?.status === 'running') return plain(status('capturing calls', 'muted', 'spin', true))
  const cap = f.capture?.result
  if (cap != null) {
    return plain(
      cap.ok
        ? status(`${plural(cap.n_kept, 'call')} captured`)
        : status(cap.deterministic ? 'capture failed' : 'captured calls are not deterministic', 'fail', 'x'),
    )
  }
  const { t, run } = lastTestRun(f)
  if (t !== undefined) {
    if (run?.status === 'running') return plain(status('running tests', 'muted', 'spin', true))
    const r = run?.result?.result
    if (run !== undefined && r != null) {
      const total = pytestTotal(r)
      return plain(
        run.status === 'ok'
          ? status(`${plural(total, 'test')} pass`)
          : status(`${r.passed}/${total} tests pass on the original`, 'fail', 'x'),
      )
    }
    if (t.write?.status === 'running') {
      return plain(status(t.attempt > 0 ? `rewriting tests · try ${t.attempt + 1}` : 'writing tests', 'muted', 'spin', true))
    }
    if (t.write?.status === 'ok') return plain(status('tests written'))
    if (t.write?.status === 'failed') return plain(status('tests could not be written', 'fail', 'x'))
  }
  return plain(status('starting', 'muted', 'spin', true))
}

/** The tail of a function pill after its name: a skip reason or the baseline, always in µg so pills compare. */
export function fnPillTail(node: FnNode, f: FunctionModel): { text: string; kind: 'sub' | 'num' } | null {
  if (node.status === 'skipped') return { text: `skipped · ${skipText(f)}`, kind: 'sub' }
  if (node.status === 'failed') return { text: 'failed', kind: 'sub' }
  if (node.status === 'cancelled') return { text: 'cancelled', kind: 'sub' }
  const g = baselineG(f)
  return g === null ? null : { text: `${formatMass(g)}/call`, kind: 'num' }
}

export function fnAria(node: FnNode, f: FunctionModel, pinned: boolean): string {
  let aria = `Function ${node.index + 1}, ${node.qualname}`
  if (node.status === 'skipped') aria += `, skipped, ${skipText(f)}`
  else if (node.status === 'failed') aria += `, failed, ${f.outcome?.reason ?? ''}`
  else if (node.status === 'cancelled') aria += ', cancelled'
  else {
    const g = baselineG(f)
    if (g !== null) aria += `, baseline ${formatMass(g)} per call`
  }
  return pinned ? aria : `${aria}. Show details`
}

export function fnTip(node: FnNode, f: FunctionModel): string {
  if (node.status !== 'running' && node.status !== 'done') return f.outcome?.reason || sentence(skipText(f))
  const info = f.started?.info
  const where = info === undefined ? node.qualname : `${info.file}:${info.line}`
  // the baseline left the pill face, so the tooltip carries it
  const g = baselineG(f)
  return g === null ? where : `${where} · baseline ${formatMass(g)}/call`
}

// ---------------------------------------------------------------- candidates

/** The latest attempt whose code was written. */
export function writtenAttempt(c: CandidateModel) {
  for (let i = c.attempts.length - 1; i >= 0; i--) {
    const a = c.attempts[i]
    if (a?.write?.status === 'ok' && a.write.result?.candidate != null) return a
  }
  return null
}

/** Whether the card shows code yet, or only the strategy it will try. */
export const isWriting = (node: CandNode): boolean =>
  node.phase === 'writing' || node.phase === 'repairing' || (node.phase === 'failed' && node.deltaPct === null)

export function candTitle(node: CandNode, c: CandidateModel): { text: string; hint: boolean } {
  const att = writtenAttempt(c)
  if (att === null || node.phase === 'writing' || node.phase === 'repairing') {
    return { text: c.attempts.at(-1)?.write?.started.strategy_hint ?? '', hint: true }
  }
  return { text: att.write?.result?.candidate?.strategy ?? '', hint: false }
}

export function checkFail(check: CandidateCheckCompletedData | null | undefined): string {
  if (check == null) return 'check failed'
  if (check.static !== null && !check.static.ok) return 'syntax error'
  const t = check.tests
  if (t !== null && t.failed + t.errors > 0) return `${t.failed + t.errors}/${pytestTotal(t)} tests failed`
  if (check.differential !== null && check.differential.mismatches.length > 0) return 'differs on captured calls'
  if (check.error !== null) return check.error.message
  return 'check failed'
}

const REJECT_TEXT: Record<string, string> = {
  syntax: 'syntax error',
  tests_failed: 'tests failed',
  differential_mismatch: 'differs on captured calls',
}

export function rejText(c: CandidateModel): string {
  const rj = c.rejection
  if (rj === null) return ''
  const a = c.attempts.find((x) => x.attempt === (rj.attempt ?? 0)) ?? c.attempts.at(-1)
  const check = a?.check
  if (check?.status === 'failed') return checkFail(check.result)
  return REJECT_TEXT[rj.reason] ?? rj.reason.replace(/_/g, ' ')
}

/** Why a candidate failed without a rejection: its write or its bench errored. */
export function failText(c: CandidateModel): string {
  const bench = c.bench?.status === 'failed' ? c.bench.result?.error?.message : undefined
  const write = c.attempts.at(-1)?.write?.result?.error?.message
  return bench ?? write ?? 'failed'
}

/** The face of a candidate pill. */
export interface CandFace {
  /** words while busy: writing, repairing, checking, measuring */
  sub: string | null
  glyph: Glyph | null
  /** the measured change, coloured by `node.tone` */
  delta: string | null
}

export function candFace(node: CandNode): CandFace {
  const face = (sub: string | null, glyph: Glyph | null = null, delta: string | null = null): CandFace => ({
    sub,
    glyph,
    delta,
  })
  switch (node.phase) {
    case 'writing':
      return face('writing')
    case 'repairing':
      return face('repairing')
    case 'measuring':
      return face('measuring')
    case 'checking':
      return face('checking')
    case 'written':
    case 'checked':
    case 'queued':
      return face(null, 'mark-q')
    case 'check_failed':
    case 'rejected':
    case 'failed':
      return face(null, 'x')
    case 'measured': {
      const delta = formatPct(node.deltaPct)
      const v = node.verdict
      const mark: Glyph | null =
        v === 'reverted'
          ? 'mark-rv'
          : v === 'winner' || v === 'merging' || v === 'merged'
            ? 'mark-win'
            : node.dimmed
              ? v === 'smaller'
                ? 'mark-lo'
                : 'mark-ns'
              : null
      return face(null, mark, delta)
    }
  }
}

export interface CandFoot extends Status {
  /** "Repair · try n", before the status, which then stays lowercase */
  repair: string | null
  /** the bench is running; nothing draws for it, the text says so */
  progress: boolean
}

export function candFoot(node: CandNode, c: CandidateModel, f: FunctionModel, live: boolean): CandFoot {
  const foot = (s: Status, repair: string | null = null, progress = false): CandFoot => ({
    ...settle(repair === null ? { ...s, text: sentence(s.text) } : s, live),
    repair,
    progress: progress && live,
  })
  const att = c.attempts.at(-1)
  switch (node.phase) {
    case 'writing':
      return foot(status('writing…', 'muted', null, true))
    case 'repairing':
      return foot(status('writing…', 'muted', null, true), `Repair · try ${(att?.attempt ?? 1) + 1}`)
    case 'written':
      return foot(status('written · queued for checks', 'muted', 'mark-q'))
    case 'checking':
      return foot(status('checking tests · captured calls', 'muted', 'spin', true))
    case 'checked':
      return foot(status('checks pass', 'muted', 'mark-q'))
    case 'check_failed':
      return foot(status(checkFail(att?.check?.result), 'fail', 'x'))
    case 'queued':
      return foot(status('queued for measuring', 'muted', 'mark-q'))
    case 'measuring':
      return foot(status(`measuring · ${plural(c.bench?.started.n_trials ?? 0, 'trial')}`), null, true)
    case 'rejected':
      return foot(status(rejText(c), 'fail', 'x'))
    case 'failed':
      return foot(status(failText(c), 'fail', 'x'))
    case 'measured':
      break
  }
  const before = f.decision === null
  switch (node.verdict) {
    case 'winner':
      return f.merge?.status === 'failed'
        ? foot(status('winner · merge failed', 'fail', 'x'))
        : foot(status('winner', 'plain', 'mark-win'))
    case 'merging':
      return foot(status('winner · merging', 'muted', 'spin', true))
    case 'merged':
      return foot(status('winner · merged', 'plain', 'ok'))
    case 'reverted':
      return foot(status('merged, then reverted', 'caution', 'rv'))
    case 'smaller':
      return foot(status(`smaller saving than ${f.decision?.winner ?? NONE}`))
    case 'clears_gate':
      return foot(status(before ? 'measured · clears the gate' : 'clears the gate'))
    case 'below_gate':
      return foot(status(before ? 'measured · below the gate' : 'below the gate'))
    case 'slower':
      return foot(status(before ? 'measured · slower' : 'slower'))
    case 'not_significant':
    case null:
      return foot(status(before ? 'measured · not significant' : 'not significant'))
  }
}

export function candAria(node: CandNode, c: CandidateModel, pinned: boolean): string {
  let aria = `Candidate ${node.letter}, ${candTitle(node, c).text}`
  if (node.deltaPct !== null) aria += `, ${formatPct(node.deltaPct)}`
  if (node.phase === 'rejected') aria += `, rejected: ${rejText(c)}`
  if (node.phase === 'failed') aria += `, failed: ${failText(c)}`
  if (node.verdict === 'reverted') aria += ', winner, reverted after merge'
  else if (node.verdict === 'winner' || node.verdict === 'merging' || node.verdict === 'merged') aria += ', winner'
  return pinned ? aria : `${aria}. Show details`
}

export function candTip(node: CandNode, c: CandidateModel): string {
  const title = candTitle(node, c).text
  const tail = node.phase === 'rejected' ? ` · ${rejText(c)}` : node.phase === 'failed' ? ` · ${failText(c)}` : ''
  return `${node.letter} · ${title}${tail}`
}

// ---------------------------------------------------------------- results

export interface ResultFace extends Status {
  /** the merged change, shown as a win */
  delta: string | null
}

/** The merged change of the winner. */
export function mergedDelta(f: FunctionModel): number | null {
  const w = f.decision?.winner ?? null
  return f.outcome?.delta_pct ?? (w === null ? null : (rankOf(f, w)?.delta_pct ?? null))
}

export function resultFace(node: ResultNode, f: FunctionModel, live: boolean): ResultFace {
  const w = node.winner ?? NONE
  const face = (s: Status, delta: string | null = null): ResultFace => ({ ...settle(s, live), delta })
  switch (node.result) {
    case 'winner':
      return face(status(`Winner ${w}`, 'plain', 'mark-win'))
    case 'merging':
      return face(status(`Merging ${w}`, 'muted', 'spin', true))
    case 'merged':
      return face(status('Merged', 'plain', 'ok'), formatPct(mergedDelta(f)))
    case 'reverted':
      return face(status('Reverted', 'caution', 'rv'))
    case 'nochange':
      return face(status('No change', 'muted', 'dash'))
    case 'allrej':
      return face(status('All rejected', 'muted', 'dash'))
    case 'failed':
      return face(status(f.merge?.status === 'failed' ? 'Merge failed' : 'Failed', 'fail', 'x'))
    case 'cancelled':
      return face(status('Cancelled', 'muted', 'dash'))
  }
}

/** The best measured candidate when none won. */
export function bestBelowGate(f: FunctionModel) {
  return (
    f.decision?.ranking
      .filter((r) => r.delta_pct !== null)
      .sort((a, b) => (a.delta_pct ?? 0) - (b.delta_pct ?? 0))[0] ?? null
  )
}

export function resultTip(node: ResultNode, f: FunctionModel, m: RunModel): string {
  const w = node.winner ?? NONE
  const merge = f.merge?.result
  const gate = gateOf(m, f)
  switch (node.result) {
    case 'winner':
      return `${w} wins · ${formatPct(mergedDelta(f))} CO2e per call`
    case 'merging':
      return `Merging ${w} and re-running the suite`
    case 'merged': {
      const parts = [`${w} merged`]
      if (merge?.suite != null) parts.push(`${plural(merge.suite.passed, 'test')} pass`)
      if (merge?.differential != null) {
        const n = merge.differential.n_samples
        parts.push(`${n - merge.differential.mismatches.length}/${n} captured calls identical`)
      }
      return parts.join(' · ')
    }
    case 'reverted': {
      const df = merge?.differential
      return df == null
        ? `${w} merged, then reverted`
        : `${w} merged, then reverted: ${df.mismatches.length} of ${df.n_samples} captured calls differ`
    }
    case 'nochange': {
      const best = bestBelowGate(f)
      return best === null
        ? 'No candidate cleared the gate · the original stays'
        : `Best ${best.candidate_id} ${formatPct(best.delta_pct)}, below the ${gate}% gate · the original stays`
    }
    case 'allrej':
      return 'Every candidate failed a check · the original stays'
    case 'failed':
      return f.merge?.result?.error?.message ?? f.outcome?.reason ?? 'Failed'
    case 'cancelled':
      return 'Cancelled · the original stays'
  }
}

const RESULT_WORDS: Record<ResultNode['result'], string> = {
  winner: 'winner',
  merging: 'merging',
  merged: 'merged',
  reverted: 'merged, then reverted',
  nochange: 'no change',
  allrej: 'all candidates rejected',
  failed: 'failed',
  cancelled: 'cancelled',
}

export function resultAria(node: ResultNode, pinned: boolean): string {
  const words = node.result === 'winner' ? `winner ${node.winner ?? ''}` : RESULT_WORDS[node.result]
  return `Result: ${words}${pinned ? '' : '. Show details'}`
}

// ---------------------------------------------------------------- tail

export interface TailText {
  text: string
  tone: Tone
  /** the dot at the end of the trunk: queued, live or ended */
  dot: 'q' | 'live' | 'end'
  /** the trunk to it is dotted: nothing has happened there yet */
  dotted: boolean
}

export function tailText(tail: TailNode, m: RunModel): TailText {
  const t = m.terminal
  switch (tail.tail) {
    case 'queued':
      return { text: `+${tail.queued} queued`, tone: 'muted', dot: 'q', dotted: true }
    case 'patch':
      return { text: 'Writing the patch', tone: 'plain', dot: 'live', dotted: false }
    case 'complete':
      return {
        text: `Run complete · ${formatClock(t?.type === 'run.completed' ? t.summary.duration_ms : 0)}`,
        tone: 'muted',
        dot: 'end',
        dotted: false,
      }
    case 'failed':
      return {
        text: t?.type === 'run.failed' ? `Run failed · ${t.stage.replace(/_/g, ' ')}` : 'Run failed',
        tone: 'fail',
        dot: 'end',
        dotted: false,
      }
    case 'cancelled':
      return { text: 'Run cancelled', tone: 'muted', dot: 'end', dotted: false }
    case 'interrupted':
      return { text: 'Run interrupted', tone: 'muted', dot: 'end', dotted: false }
  }
}

/** A tail's longer explanation, for its tooltip. */
export function tailTip(tail: TailNode, m: RunModel): string | null {
  const t = m.terminal
  if (tail.tail === 'failed' && t?.type === 'run.failed') return t.error.message
  if (tail.tail === 'queued') return `${plural(tail.queued, 'function')} waiting to start`
  return null
}

// ---------------------------------------------------------------- shared

/** The letter of a candidate's measured stats, for charts. */
export const benchDelta = (c: CandidateModel): number | null => benchOf(c)?.delta_pct ?? null

export type { CandidateId }
