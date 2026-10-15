import { describe, expect, it } from 'vitest'
import type { CandidateId, EventOf, EventType, RunEvent } from '../gen/events'
import { initialRunModel, reduce } from '../state/reducer'
import type { RunModel } from '../state/types'
import { buildSyntheticRun } from '../synth/scenario'
import { makeEvent } from '../test/events'
import { deriveTree, type FnNode } from './derive'
import {
  candAria,
  candFace,
  candFoot,
  candTip,
  candTitle,
  fnAria,
  fnPillTail,
  fnStatus,
  fnTip,
  preText,
  resultFace,
  resultTip,
  skipText,
  tailText,
  tailTip,
} from './text'

const { events } = buildSyntheticRun()
const fold = (evs: readonly RunEvent[]): RunModel => reduce(initialRunModel(), evs)
const at = (seq: number) => fold(events.filter((e) => e.seq <= seq))
const seqOf = <T extends EventType>(type: T, fid: string, n = 0): number => {
  const e = events.filter((x): x is EventOf<T> => x.type === type && x.function_id === fid)[n]
  if (e === undefined) throw new Error(`no ${type}`)
  return e.seq
}

const DEDUPE = 'textkit.dedupe:dedupe_preserve_order'
const AVG = 'datakit.series:moving_average'
const TOPK = 'datakit.rank:top_k_inplace'
const JOIN = 'textkit.fields:join_fields'
const NORM = 'textkit.normalize:normalize_whitespace'
const PARSE = 'datakit.io_free:parse_records'

function fnOf(m: RunModel, fid: string): { node: FnNode; f: NonNullable<RunModel['functions'][string]> } {
  const node = deriveTree(m).functions.find((x) => x.functionId === fid)
  const f = m.functions[fid]
  if (node === undefined || f === undefined) throw new Error(`no ${fid}`)
  return { node, f }
}

const status = (m: RunModel, fid: string, live = true) => {
  const { node, f } = fnOf(m, fid)
  return fnStatus(node, f, live)
}

function cand(m: RunModel, fid: string, letter: CandidateId, live = true) {
  const { node, f } = fnOf(m, fid)
  const c = node.candidates.find((x) => x.letter === letter)
  const model = f.candidates[letter]
  if (c === undefined || model === undefined) throw new Error(`no ${fid}/${letter}`)
  return { face: candFace(c), foot: candFoot(c, model, f, live), title: candTitle(c, model), aria: candAria(c, model, false) }
}

describe('function texts through a function', () => {
  it('follows the tests, the capture and the baseline', () => {
    const steps: [number, string, string | null][] = [
      [seqOf('function.started', DEDUPE), 'Starting', 'spin'],
      [seqOf('function.tests.write.started', DEDUPE), 'Writing tests', 'spin'],
      [seqOf('function.tests.write.completed', DEDUPE), 'Tests written', null],
      [seqOf('function.tests.run.started', DEDUPE), 'Running tests', 'spin'],
      [seqOf('function.tests.run.completed', DEDUPE), '3/4 tests pass on the original', 'x'],
      [seqOf('function.tests.write.started', DEDUPE, 1), 'Rewriting tests · try 2', 'spin'],
      [seqOf('function.tests.run.completed', DEDUPE, 2), '4 tests pass', null],
      [seqOf('function.capture.started', DEDUPE), 'Capturing calls', 'spin'],
      [seqOf('function.capture.completed', DEDUPE), '7 calls captured', null],
      [seqOf('function.baseline.started', DEDUPE), 'Measuring baseline', 'spin'],
    ]
    for (const [seq, text, glyph] of steps) expect(status(at(seq), DEDUPE)).toMatchObject({ text, glyph })
    const baseline = status(at(seqOf('function.baseline.completed', DEDUPE)), DEDUPE)
    expect(baseline.text).toBe('Baseline')
    expect(baseline.mass).toMatch(/^\d[\d.]* µg\/call$/)
  })

  it('names why a function was skipped', () => {
    const m = fold(events)
    expect(skipText(fnOf(m, NORM).f)).toBe('untestable')
    expect(skipText(fnOf(m, PARSE).f)).toBe('not deterministic')
    expect(status(m, NORM)).toMatchObject({ text: 'Skipped · untestable', tone: 'fail', glyph: 'x' })
    const { node, f } = fnOf(m, PARSE)
    expect(fnPillTail(node, f)).toEqual({ text: 'skipped · not deterministic', kind: 'sub' })
  })

  it('writes every pill baseline in micrograms, however small', () => {
    const { node, f } = fnOf(fold(events), JOIN)
    expect(fnPillTail(node, f)?.text).toMatch(/ µg\/call$/)
    expect(fnAria(node, f, false)).toMatch(/^Function 4, join_fields, baseline .* µg per call\. Show details$/)
  })

  it('drops the spinner once the run has ended', () => {
    const m = at(seqOf('function.capture.started', DEDUPE))
    expect(status(m, DEDUPE, false)).toMatchObject({ text: 'Capturing calls', glyph: null, busy: false })
  })
})

describe('candidate texts', () => {
  it('shows the strategy hint while writing, then the strategy', () => {
    const writing = cand(at(seqOf('candidate.write.started', DEDUPE)), DEDUPE, 'A')
    expect(writing.title.hint).toBe(true)
    expect(writing.face.sub).toBe('writing')
    expect(writing.foot).toMatchObject({ text: 'Writing…', repair: null, busy: true })
    const done = cand(fold(events), DEDUPE, 'A')
    expect(done.title.hint).toBe(false)
    expect(done.title.text).not.toBe('')
  })

  it('marks a repair with its try, and keeps the status after it lowercase', () => {
    const repairSeq = events.find(
      (e) => e.type === 'candidate.write.started' && e.function_id === DEDUPE && e.candidate_id === 'A' && e.attempt === 1,
    )?.seq
    const a = cand(at(repairSeq ?? 0), DEDUPE, 'A')
    expect(a.face.sub).toBe('repairing')
    expect(a.foot.repair).toBe('Repair · try 2')
    expect(a.foot.text).toBe('writing…')
  })

  it('says it is checking in words, since no spinner draws', () => {
    const m = at(seqOf('candidate.check.started', DEDUPE))
    const checking = deriveTree(m).functions[0]?.candidates.find((c) => c.phase === 'checking')
    if (checking === undefined) throw new Error('no checking candidate')
    expect(candFace(checking)).toEqual({ sub: 'checking', glyph: null, delta: null })
  })

  it('flags a running bench while live', () => {
    const m = at(seqOf('candidate.bench.started', DEDUPE))
    expect(cand(m, DEDUPE, 'C').foot).toMatchObject({ text: 'Measuring · 16 trials', progress: true })
    expect(cand(m, DEDUPE, 'C', false).foot.progress).toBe(false)
  })

  it('says how each measured candidate did after the decision', () => {
    const m = fold(events)
    expect(cand(m, DEDUPE, 'B').foot).toMatchObject({ text: 'Winner · merged', glyph: 'ok' })
    expect(cand(m, DEDUPE, 'B').face).toEqual({ sub: null, glyph: 'mark-win', delta: '−78.6%' })
    expect(cand(m, DEDUPE, 'A').foot.text).toBe('Smaller saving than B')
    expect(cand(m, DEDUPE, 'A').face.glyph).toBe('mark-lo')
    expect(cand(m, DEDUPE, 'C').foot.text).toBe('Not significant')
    expect(cand(m, DEDUPE, 'C').face.glyph).toBe('mark-ns')
    expect(cand(m, AVG, 'A').foot).toMatchObject({ text: 'Merged, then reverted', tone: 'caution', glyph: 'rv' })
    expect(cand(m, JOIN, 'A').foot.text).toBe('Below the gate')
  })

  it('says why each rejected candidate failed', () => {
    const m = fold(events)
    expect(cand(m, TOPK, 'A').foot).toMatchObject({ text: 'Syntax error', tone: 'fail', glyph: 'x' })
    expect(cand(m, TOPK, 'B').foot.text).toMatch(/^(\d+\/\d+ tests failed|Tests failed)$/)
    expect(cand(m, TOPK, 'C').foot.text).toBe('Differs on captured calls')
    expect(cand(m, TOPK, 'A').aria).toMatch(/^Candidate A, .*, rejected: syntax error\. Show details$/)
  })

  it('says measured before the decision', () => {
    const m = at(seqOf('candidate.bench.completed', DEDUPE, 1))
    expect(cand(m, DEDUPE, 'B').foot.text).toBe('Measured · clears the gate')
    expect(cand(m, DEDUPE, 'C').foot.text).toBe('Measured · not significant')
  })
})

describe('result texts', () => {
  const m = fold(events)
  const result = (fid: string) => {
    const { node, f } = fnOf(m, fid)
    if (node.result === null) throw new Error('no result')
    return { face: resultFace(node.result, f, false), tip: resultTip(node.result, f, m) }
  }

  it('describes every kind of result in the synthetic run', () => {
    expect(result(DEDUPE).face).toMatchObject({ text: 'Merged', glyph: 'ok', delta: '−78.6%' })
    expect(result(DEDUPE).tip).toMatch(/^B merged · 18 tests pass · \d+\/\d+ captured calls identical$/)
    expect(result(AVG).face).toMatchObject({ text: 'Reverted', tone: 'caution', glyph: 'rv' })
    expect(result(AVG).tip).toMatch(/^A merged, then reverted/)
    expect(result(TOPK).face).toMatchObject({ text: 'All rejected', glyph: 'dash' })
    expect(result(TOPK).tip).toBe('Every candidate failed a check · the original stays')
    expect(result(JOIN).face.text).toBe('No change')
    expect(result(JOIN).tip).toBe('Best A −4.2%, below the 5% gate · the original stays')
  })

  it('names the winner before the merge', () => {
    const before = at(seqOf('function.decision', DEDUPE))
    const { node, f } = fnOf(before, DEDUPE)
    if (node.result === null) throw new Error('no result')
    expect(resultFace(node.result, f, true)).toMatchObject({ text: 'Winner B', glyph: 'mark-win' })
    expect(resultTip(node.result, f, before)).toBe('B wins · −78.6% CO2e per call')
  })
})

describe('setup and tail texts', () => {
  it('reports the setup stages', () => {
    const m = fold(events)
    const t = deriveTree(m)
    expect(preText(t.pre, m).text).toMatch(/^6 of \d+ functions selected$/)
    const early = at(2)
    expect(preText(deriveTree(early).pre, early)).toMatchObject({ busy: true, glyph: 'spin' })
  })

  it('keeps the setup texts lowercase, since they follow the repository name in the root pill', () => {
    for (const e of events) {
      const m = at(e.seq)
      const text = preText(deriveTree(m).pre, m).text
      expect(text.charAt(0)).toBe(text.charAt(0).toLowerCase())
      if (m.selection !== null) break
    }
  })

  it('counts queued functions, then the end of the run', () => {
    const mid = at(seqOf('function.started', DEDUPE))
    const tail = deriveTree(mid).tail
    if (tail === null) throw new Error('no tail')
    expect(tailText(tail, mid)).toEqual({ text: '+5 queued', tone: 'muted', dot: 'q', dotted: true })
    const end = fold(events)
    const last = deriveTree(end).tail
    if (last === null) throw new Error('no tail')
    expect(tailText(last, end).text).toMatch(/^Run complete · \d+:\d\d$/)
  })

  it('names the stage a run failed in', () => {
    const base = events.filter((e) => e.seq <= seqOf('function.started', DEDUPE))
    const n = base.length
    const m = fold([
      ...base,
      makeEvent(n + 1, 'run.state_changed', { from_state: 'optimizing', to_state: 'failed', reason: null }),
      makeEvent(n + 2, 'run.failed', {
        stage: 'optimizing',
        error: { kind: 'internal', message: 'boom', detail: null },
      }),
    ])
    const tail = deriveTree(m).tail
    if (tail === null) throw new Error('no tail')
    expect(tailText(tail, m)).toMatchObject({ text: 'Run failed · optimizing', tone: 'fail' })
  })
})

describe('copy rules', () => {
  // every face and tooltip the tree can show, at every seq of the synthetic run
  it('never uses the banned glyphs in a face or a tooltip', () => {
    // an arrow, at least, about, a subscript two and an em dash, escaped so the ban grep stays clean
    const banned = /[\u2192\u2265\u2248\u2082\u2014]/
    const seen: string[] = []
    for (const e of events) {
      const m = at(e.seq)
      const t = deriveTree(m)
      const live = m.terminal === null
      seen.push(preText(t.pre, m).text)
      for (const node of t.functions) {
        const f = m.functions[node.functionId]
        if (f === undefined) continue
        seen.push(node.qualname, fnTip(node, f), fnStatus(node, f, live).text, fnPillTail(node, f)?.text ?? '')
        for (const c of node.candidates) {
          const model = f.candidates[c.letter]
          if (model === undefined) continue
          const face = candFace(c)
          seen.push(face.sub ?? '', face.delta ?? '', candTip(c, model), candFoot(c, model, f, live).text)
        }
        if (node.result !== null) {
          const face = resultFace(node.result, f, live)
          seen.push(face.text, face.delta ?? '', resultTip(node.result, f, m))
        }
      }
      if (t.tail !== null) seen.push(tailText(t.tail, m).text, tailTip(t.tail, m) ?? '')
    }
    expect(seen.filter((s) => banned.test(s))).toEqual([])
  })
})
