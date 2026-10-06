// The synthetic run behind /replay: the functions of catalog.ts played through the steps of
// the backend's demo pipeline (netzero/pipeline/fake.py) on a virtual clock. The result is
// checked by the backend's own event parser and grammar (see scripts/synth.ts).
import type {
  Assumptions,
  BenchStats,
  CandidateId,
  CandidateRejectedData,
  FunctionCompletedData,
  LlmStage,
  LogData,
  LogLevel,
  LogSource,
  PowerInfo,
  PytestResult,
  RankingEntry,
  ReplayRef,
  RunEvent,
  RunSettings,
  RunState,
  RunTotals,
  TestFile,
} from '../gen/events'
import { RunBuilder, runConcurrently, type Scope, type Task } from './builders'
import { SELECTED, UNSELECTED, type CandidatePlan, type Draft, type SyntheticFunction } from './catalog'
import { draft, functionId, functionInfo, pytestLines, pytestResult, rewriteDiff, testFile, triageItem } from './python'
import { Rand } from './random'
import { compare, fmtG, fmtP, headline, holm, isSignificant, measure, mean, pct, round, std, whyNot } from './stats'

export const SYNTHETIC_RUN_ID = '20260101-000000-demo'
const T0 = Date.UTC(2026, 0, 1)

const SETTINGS: RunSettings = {
  max_functions: 20,
  preselect: 8,
  n_trials: 16,
  trial_target_s: 0.75,
  alpha: 0.05,
  min_effect_pct: 5,
  candidates: ['A', 'B', 'C'],
  country_iso: null,
}
const CANDIDATES = SETTINGS.candidates
const TEST_REPAIRS = 2
const CANDIDATE_REPAIRS = 1
const REPO_TESTS = 14
const MODEL = 'claude-haiku-4-5'

const ASSUMPTIONS: Assumptions = {
  eur_per_kwh: 0.2,
  eu_ets_eur_per_t: 70,
  vcm_eur_per_t: 15,
  default_calls_per_year: 1_000_000,
  notes: [
    'electricity: indicative EU non-household price',
    'EU ETS: indicative allowance price',
    'VCM: indicative voluntary carbon market price',
  ],
}

const POWER: PowerInfo = {
  power_source: 'tdp_estimate',
  badge: 'estimated',
  method: 'codecarbon_model',
  cpu_model: 'Apple M3 Pro',
  tdp_w: 30,
  cpu_count: 12,
  p_core_w: 2.5,
  p_ram_w: 0.4,
  grid: { country_iso: 'WORLD', kg_per_kwh: 0.475, source: 'codecarbon world average' },
  notes: ["estimated from CodeCarbon's TDP model × measured CPU time"],
}

const STRATEGY_HINTS: Record<CandidateId, string> = {
  A: 'algorithmic: better complexity',
  B: 'builtins/stdlib: idiomatic fast paths',
  C: 'conservative: micro-optimizations only',
}

/** Prompt-cache sizes of each stage's system prompt. */
const SYSTEM_TOKENS: Record<LlmStage, number> = {
  triage: 1850,
  tests: 2420,
  tests_repair: 2420,
  rewrite: 2160,
  rewrite_repair: 2160,
}

/**
 * Nominal step durations in ms, each jittered by ±15%. Longer than the backend's demo
 * timings, closer to a real run; the replay caps idle gaps anyway.
 */
const MS = {
  clone: [1400, 1200],
  env: [1600, 2600],
  discovery: 1500,
  triage: 3500,
  /** the user reviewing the triage before confirming */
  selection: 6000,
  testsWrite: 6000,
  testsRun: [1200, 900],
  capture: 800,
  baseline: 6000,
  write: { A: 8000, B: 6500, C: 7000 } satisfies Record<CandidateId, number>,
  staticCheck: 300,
  check: 1500,
  benchChunk: 2000,
  merge: 2500,
  artifacts: 1000,
} as const

const ALL = [...SELECTED, ...UNSELECTED]
const PACKAGES = [...new Set(ALL.map((f) => f.module.split('.')[0] ?? ''))].sort()
/** one file per module plus an `__init__.py` per package */
const N_SOURCE_FILES = new Set(ALL.map((f) => f.module)).size + PACKAGES.length

const byCodePoint = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0)

type Outcome = Pick<FunctionCompletedData, 'outcome'> &
  Partial<Omit<FunctionCompletedData, 'outcome' | 'duration_ms'>>

interface CandidateResult {
  cid: CandidateId
  stats: BenchStats | null
  rejected: CandidateRejectedData | null
  diff: string
}

/** The single bench lane: the baseline first, then one candidate at a time, first come first served. */
interface BenchLane {
  ready: boolean
  busy: boolean
  callsPerTrial: number
  /** candidates waiting for the lane, in arrival order */
  queue: CandidateId[]
}

type BenchPlan = Extract<CandidatePlan, { delta: number }>

class SyntheticRunPlayer {
  readonly b = new RunBuilder(SYNTHETIC_RUN_ID, T0)
  private state: RunState = 'created'
  private readonly warmStages = new Set<LlmStage>()
  private runCost = 0
  private suiteTests = REPO_TESTS
  private readonly merged: { fid: string; diff: string }[] = []
  private readonly completed: FunctionCompletedData[] = []

  play(): RunTotals {
    const b = this.b
    b.emit('run.created', {
      schema_version: 1,
      netzero_version: '0.2.0',
      mode: 'demo',
      source: { kind: 'demo', url: null, ref: null },
      models: { triage: MODEL, tests: MODEL, tests_repair: MODEL, rewrite: MODEL, rewrite_repair: MODEL },
      settings: SETTINGS,
      assumptions: ASSUMPTIONS,
    })
    b.emit('run.power.detected', { power: POWER })
    this.transition('cloning')
    this.clone()
    this.transition('installing')
    this.install()
    this.transition('discovering')
    this.discover()
    this.transition('triaging')
    this.triage()
    this.select()
    SELECTED.forEach((f, index) => this.function(f, index))
    this.transition('finalizing')
    this.artifacts()
    this.transition('completed')
    const summary = this.totals()
    b.emit('run.completed', { summary })
    return summary
  }

  // -- helpers ---------------------------------------------------------------------

  private transition(to: RunState): void {
    this.b.emit('run.state_changed', { from_state: this.state, to_state: to, reason: null })
    this.state = to
  }

  private log(
    source: LogSource,
    lines: string[],
    scope: Scope = {},
    level: LogLevel = 'info',
    stream: LogData['stream'] = null,
  ): void {
    this.b.emit('log', { level, source, stream, lines, truncated: false }, scope)
  }

  /** One model call: the first call of a stage writes the prompt cache, later ones read it. */
  private usage(stage: LlmStage, label: string, latencyMs: number, scope: Scope = {}): void {
    const r = new Rand(scope.function_id ?? 'run', 'usage', label)
    const system = SYSTEM_TOKENS[stage]
    const cacheWrite = this.warmStages.has(stage) ? 0 : system
    const cacheRead = system - cacheWrite
    this.warmStages.add(stage)
    const [input, output] =
      stage === 'triage'
        ? [r.randint(5200, 6400), r.randint(1400, 1900)]
        : stage.startsWith('tests')
          ? [r.randint(1100, 1900), r.randint(450, 900)]
          : [r.randint(900, 1600), r.randint(280, 720)]
    // Haiku 4.5: $1 / $5 per million input / output tokens; cache reads 0.1×, writes 1.25×
    const cost = round((input + 0.1 * cacheRead + 1.25 * cacheWrite) / 1e6 + (output * 5) / 1e6, 8)
    this.runCost += cost
    this.b.emit(
      'llm.usage',
      {
        stage,
        model: MODEL,
        cassette: 'off',
        input_tokens: input,
        output_tokens: output,
        cache_creation_input_tokens: cacheWrite,
        cache_read_input_tokens: cacheRead,
        cost_usd: cost,
        run_cost_usd: round(this.runCost, 8),
        latency_ms: Math.trunc(latencyMs),
        stop_reason: 'end_turn',
      },
      scope,
    )
  }

  // -- setup -----------------------------------------------------------------------

  private clone(): void {
    const b = this.b
    const r = new Rand('clone')
    const sha = new Rand('head').hex(40)
    const step = b.start('run.clone.started', { url: null, ref: null })
    this.log('clone', ["Copying the bundled demo repository into 'repo'"])
    b.advance(r.jitter(MS.clone[0]))
    this.log('clone', [
      'remote: Enumerating objects: 64, done.',
      'Receiving objects: 100% (64/64), 21.37 KiB | 1.9 MiB/s, done.',
      'Resolving deltas: 100% (17/17), done.',
    ])
    b.advance(r.jitter(MS.clone[1]))
    this.log('clone', [`HEAD is now at ${sha.slice(0, 7)} datakit: add rank helpers`])
    step.ok({ commit_sha: sha, n_files: N_SOURCE_FILES + 7, n_py_files: N_SOURCE_FILES + 2, size_bytes: 48_213 })
  }

  private install(): void {
    const b = this.b
    const r = new Rand('env')
    const packages = [
      { name: 'iniconfig', version: '2.0.0' },
      { name: 'packaging', version: '24.2' },
      { name: 'pluggy', version: '1.5.0' },
      { name: 'pytest', version: '8.3.4' },
      { name: 'demo-utils', version: '0.1.0' },
    ]
    const step = b.start('run.env.started', { python_request: '3.12', dependency_sources: ['pyproject.toml'] })
    this.log('env', ['Using CPython 3.12.7', 'Creating virtual environment at: .venv'])
    b.advance(r.jitter(MS.env[0]))
    this.log('env', [
      `Resolved ${packages.length} packages in 214ms`,
      `Installed ${packages.length} packages in 31ms`,
      ...packages.map((p) => ` + ${p.name}==${p.version}`),
    ])
    b.advance(r.jitter(MS.env[1]))
    this.log('env', [`import probe: ${PACKAGES.join(', ')} ok`])
    step.ok({ python_version: '3.12.7', packages, import_probe: { ok_modules: PACKAGES, failed: {} } })
  }

  private discover(): void {
    const b = this.b
    const ranked = [...ALL].sort(
      (x, y) => y.triage.heuristic - x.triage.heuristic || byCodePoint(functionId(x), functionId(y)),
    )
    const step = b.start('run.discovery.started', {})
    b.advance(new Rand('discovery').jitter(MS.discovery))
    this.log('orchestrator', [
      `scanned ${N_SOURCE_FILES} files: ${ALL.length} functions, 2 test files`,
      `${ranked.length} functions ranked by static heuristics`,
    ])
    step.ok({
      n_files: N_SOURCE_FILES,
      n_functions: ALL.length,
      n_test_files: 2,
      import_roots: ['.'],
      heuristic_ranked: ranked.map((f) => triageItem(f, false)),
    })
  }

  private triage(): void {
    const b = this.b
    const rated = [...ALL]
      .sort((x, y) => y.triage.score - x.triage.score || byCodePoint(functionId(x), functionId(y)))
      .slice(0, SETTINGS.max_functions)
    const preselected = ALL.filter((f) => f.triage.skipReason === undefined).map(functionId)
    const skipped = rated.filter((f) => f.triage.skipReason !== undefined).length
    const step = b.start('run.triage.started', { n_candidates: rated.length, llm: true })
    this.log('llm', [`triage: rating ${rated.length} functions in 1 request`])
    b.advance(new Rand('triage').jitter(MS.triage))
    this.log('llm', [`triage: ${preselected.length} preselected, ${skipped} not optimizable`])
    step.ok({
      items: rated.map((f) => triageItem(f, true, preselected.includes(functionId(f)))),
      preselected,
      llm_used: true,
    })
    this.usage('triage', 'triage', MS.triage)
  }

  /** The user keeps the preselection minus one function. */
  private select(): void {
    this.transition('awaiting_selection')
    this.b.advance(new Rand('selection').jitter(MS.selection))
    this.b.emit('run.selection.confirmed', { function_ids: SELECTED.map(functionId), auto: false })
    this.transition('optimizing')
  }

  // -- one function ----------------------------------------------------------------

  private function(f: SyntheticFunction, index: number): void {
    const b = this.b
    const fid = functionId(f)
    const scope = { function_id: fid }
    const startedTs = b.ts
    b.emit('function.started', { index, total: SELECTED.length, info: functionInfo(f) }, scope)
    this.log('orchestrator', [`[${index + 1}/${SELECTED.length}] ${fid}`], scope)
    const data: FunctionCompletedData = {
      winner: null,
      delta_pct: null,
      delta_ci_pct: null,
      g_saved_per_1m_calls: null,
      kwh_saved_per_1m_calls: null,
      diff: null,
      reason: '',
      ...this.optimize(f),
      duration_ms: 0,
    }
    data.duration_ms = b.ts - startedTs
    if (data.outcome !== f.expected) {
      throw new Error(`${fid}: scripted as ${f.expected} but played out as ${data.outcome} (${data.reason})`)
    }
    b.emit('function.completed', data, scope)
    this.completed.push(data)
  }

  private optimize(f: SyntheticFunction): Outcome {
    const tests = this.writeTests(f)
    if (tests === null) {
      return {
        outcome: 'skipped_untestable',
        reason: `generated tests still fail on the original after ${TEST_REPAIRS} repairs`,
      }
    }
    if (!this.capture(f)) return { outcome: 'skipped_capture', reason: `capture: ${f.capture.problem ?? ''}` }

    const plans = f.candidates
    if (plans === undefined) throw new Error(`${functionId(f)} reaches the candidates but has none`)
    const lane: BenchLane = { ready: false, busy: false, callsPerTrial: 0, queue: [] }
    const results = new Map<CandidateId, CandidateResult>()
    // the writes start before the baseline, which holds the lane until it is measured
    runConcurrently(this.b, [
      ...CANDIDATES.map((cid) => this.candidate(f, cid, plans[cid], tests, lane, results)),
      this.baseline(f, lane),
    ])
    const ordered = CANDIDATES.map((cid) => {
      const res = results.get(cid)
      if (res === undefined) throw new Error(`${functionId(f)}: candidate ${cid} never finished`)
      return res
    })

    const { winner, ranking } = this.decide(f, ordered)
    return winner === null ? noWinner(ordered, ranking) : this.merge(f, winner, tests)
  }

  /** Write tests, repairing them until they pass on the original twice; null if they never do. */
  private writeTests(f: SyntheticFunction): TestFile | null {
    const b = this.b
    const fid = functionId(f)
    let failures: string[] = []
    for (let attempt = 0; attempt <= TEST_REPAIRS; attempt++) {
      const scope = { function_id: fid, attempt }
      const tf = testFile(f, attempt)
      const step = b.start(
        'function.tests.write.started',
        { kind: attempt === 0 ? 'write' : 'repair', failures_in: failures },
        scope,
      )
      b.advance(new Rand(fid, 'tests', attempt).jitter(MS.testsWrite))
      step.ok({ test_file: tf })
      this.usage(attempt === 0 ? 'tests' : 'tests_repair', `tests:${attempt}`, MS.testsWrite, scope)
      const d = draft(f, attempt)
      if (this.runTests(fid, tf, attempt, 0, d)) {
        this.runTests(fid, tf, attempt, 1, d)
        return tf
      }
      failures = [`${tf.path}::${d.failing ?? ''}`]
    }
    return null
  }

  private runTests(fid: string, tf: TestFile, attempt: number, repeat: number, d: Draft): boolean {
    const b = this.b
    const r = new Rand(fid, 'tests.run', attempt, repeat)
    const scope = { function_id: fid, attempt }
    const step = b.start('function.tests.run.started', { target: 'original', repeat }, scope)
    b.advance(r.jitter(MS.testsRun[repeat === 0 ? 0 : 1]))
    const result = pytestResult(tf, r, d.failing, d.message)
    const passed = result.exit_code === 0
    this.log('pytest', pytestLines(tf, result), scope, passed ? 'info' : 'warn', 'stdout')
    if (passed) step.ok({ result, flaky: false })
    else step.fail(null, { result, flaky: false })
    return passed
  }

  /** Record the calls the tests make; a function whose calls don't repeat can't be checked. */
  private capture(f: SyntheticFunction): boolean {
    const b = this.b
    const fid = functionId(f)
    const r = new Rand(fid, 'capture')
    const scope = { function_id: fid }
    const { nCalls, problem } = f.capture
    const nKept = Math.min(nCalls, 512)
    const step = b.start('function.capture.started', {}, scope)
    b.advance(r.jitter(MS.capture))
    const fields = (deterministic: boolean) => ({
      n_calls: nCalls,
      n_kept: nKept,
      n_unpicklable: 0,
      mutates_args: f.capture.mutatesArgs ?? false,
      raises: false,
      deterministic,
      total_bytes: nKept * r.randint(180, 2400),
      previews: [...f.capture.previews],
    })
    if (problem !== undefined) {
      this.log('sandbox', [`capture: ${problem}`], scope, 'warn')
      step.fail(null, fields(false))
      return false
    }
    this.log('sandbox', [`captured ${nCalls} calls (${nKept} kept) while running the tests`], scope)
    step.ok(fields(true))
    return true
  }

  // -- candidates (concurrent tasks) -------------------------------------------------

  private *baseline(f: SyntheticFunction, lane: BenchLane): Task {
    const b = this.b
    const fid = functionId(f)
    const r = new Rand(fid, 'baseline')
    const scope = { function_id: fid }
    const n = SETTINGS.n_trials
    const calls = Math.max(1, Math.round(SETTINGS.trial_target_s / f.estCallS))
    const step = b.start('function.baseline.started', {}, scope)
    const est = f.estCallS * r.uniform(0.93, 1.07)
    this.log('bench', [`calibration: 5 samples, ~${fmtG(est * 1e3, 3)} ms/call -> ${calls} calls/trial`], scope)
    yield r.jitter(MS.baseline)
    const original = measure(r.next, { cpuS: f.estCallS, sigma: 0.04, n, calls, power: POWER }).stats
    const cv = (std(original.trials_g) / mean(original.trials_g)) * 100
    this.log('bench', [`baseline: ${n} trials × ${calls} calls, cv ${cv.toFixed(1)}%`], scope)
    step.ok({
      calibration: { calls_per_trial: calls, est_call_s: est, n_samples: 5 },
      original,
      cv_pct: round(cv, 2),
      power: POWER,
    })
    lane.callsPerTrial = calls
    lane.ready = true
  }

  /** Write, check (repairing once), then queue for the bench lane. */
  private *candidate(
    f: SyntheticFunction,
    cid: CandidateId,
    first: CandidatePlan,
    tf: TestFile,
    lane: BenchLane,
    results: Map<CandidateId, CandidateResult>,
  ): Task {
    const b = this.b
    const fid = functionId(f)
    let plan = first
    let attempt = 0
    let diff = ''
    for (;;) {
      diff = yield* this.writeCandidate(f, cid, plan, attempt)
      yield* this.checkCandidate(f, cid, plan, attempt, tf)
      if (!('reject' in plan)) break
      if (plan.repair !== undefined && attempt < CANDIDATE_REPAIRS) {
        plan = plan.repair
        attempt += 1
        continue
      }
      const rejected = { reason: plan.reject.reason, detail: rejectDetail(plan) }
      const scope = { function_id: fid, candidate_id: cid, attempt }
      b.emit('candidate.rejected', rejected, scope)
      this.log('orchestrator', [`${cid} rejected (${rejected.reason}): ${rejected.detail}`], scope, 'warn')
      results.set(cid, { cid, stats: null, rejected, diff })
      return
    }

    const scope = { function_id: fid, candidate_id: cid, attempt }
    yield () => lane.ready
    // the position counts candidates still waiting, not the one on the lane
    b.emit('candidate.bench.queued', { position: lane.queue.length }, scope)
    lane.queue.push(cid)
    yield () => !lane.busy && lane.queue[0] === cid
    lane.queue.shift()
    lane.busy = true
    const stats = yield* this.bench(f, cid, plan, attempt, lane.callsPerTrial)
    lane.busy = false
    results.set(cid, { cid, stats, rejected: null, diff })
  }

  private *writeCandidate(f: SyntheticFunction, cid: CandidateId, plan: CandidatePlan, attempt: number): Task<string> {
    const fid = functionId(f)
    const r = new Rand(fid, 'write', cid, attempt)
    const scope = { function_id: fid, candidate_id: cid, attempt }
    const stage = attempt === 0 ? 'rewrite' : 'rewrite_repair'
    const latency = r.jitter(MS.write[cid] * (attempt === 0 ? 1 : 0.8))
    const step = this.b.start(
      'candidate.write.started',
      { kind: attempt === 0 ? 'write' : 'repair', strategy_hint: STRATEGY_HINTS[cid] },
      scope,
    )
    yield latency
    const diff = rewriteDiff(f, plan.rewrite)
    step.ok({
      candidate: {
        code: plan.rewrite.code,
        new_imports: [...(plan.rewrite.newImports ?? [])],
        strategy: plan.rewrite.strategy,
        rationale: plan.rewrite.rationale,
        diff,
        diagnosis: attempt === 0 ? '' : (plan.diagnosis ?? ''),
      },
    })
    this.usage(stage, `${stage}:${cid}:${attempt}`, latency, scope)
    return diff
  }

  /** Static checks, then the generated tests, then the captured calls (differential). */
  private *checkCandidate(
    f: SyntheticFunction,
    cid: CandidateId,
    plan: CandidatePlan,
    attempt: number,
    tf: TestFile,
  ): Task {
    const fid = functionId(f)
    const r = new Rand(fid, 'check', cid, attempt)
    const scope = { function_id: fid, candidate_id: cid, attempt }
    const nSamples = Math.min(f.capture.nCalls, 64)
    const reject = 'reject' in plan ? plan.reject : null
    const step = this.b.start('candidate.check.started', {}, scope)

    if (reject?.reason === 'syntax') {
      yield r.jitter(MS.staticCheck)
      this.log('sandbox', reject.problems.map((p) => `static: ${p}`), scope, 'warn')
      step.fail(null, { static: { ok: false, problems: [...reject.problems] }, tests: null, differential: null })
      return
    }
    const staticOk = { ok: true, problems: [] }
    yield r.jitter(MS.check)

    if (reject?.reason === 'tests_failed') {
      const tests = pytestResult(tf, r, reject.failing, reject.message)
      this.log('pytest', pytestLines(tf, tests), scope, 'warn', 'stdout')
      step.fail(null, { static: staticOk, tests, differential: null })
      return
    }
    const tests: PytestResult = pytestResult(tf, r)
    this.log('pytest', pytestLines(tf, tests), scope, 'info', 'stdout')

    if (reject?.reason === 'differential_mismatch') {
      const { path, expected, actual } = reject
      this.log('sandbox', [`differential: sample 0: ${path} expected ${expected}, got ${actual}`], scope, 'warn')
      step.fail(null, {
        static: staticOk,
        tests,
        differential: {
          ok: false,
          n_samples: nSamples,
          mismatches: [{ sample_idx: 0, kind: 'mutation', path, expected, actual }],
          slowdown_ratio: null,
        },
      })
      return
    }
    const delta = 'delta' in plan ? plan.delta : 0
    this.log('sandbox', [`differential: ${nSamples}/${nSamples} samples match`], scope)
    step.ok({
      static: staticOk,
      tests,
      differential: { ok: true, n_samples: nSamples, mismatches: [], slowdown_ratio: round(1 + delta / 100, 3) },
    })
  }

  /** Interleaved trials of the original and the candidate, logged in four chunks. */
  private *bench(
    f: SyntheticFunction,
    cid: CandidateId,
    plan: CandidatePlan,
    attempt: number,
    calls: number,
  ): Task<BenchStats> {
    if (!('delta' in plan)) throw new Error(`${functionId(f)}: ${cid} reached the bench without a delta`)
    const fid = functionId(f)
    const r = new Rand(fid, 'bench', cid, attempt)
    const scope = { function_id: fid, candidate_id: cid, attempt }
    const n = SETTINGS.n_trials
    const stats = benchStats(r, f, plan, calls)
    const step = this.b.start('candidate.bench.started', { calls_per_trial: calls, n_trials: n }, scope)
    const orig = stats.original.trials_g
    const cand = stats.candidate.trials_g
    const unit = orig[0] ?? 1
    for (let k = 1; k <= 4; k++) {
      yield r.jitter(MS.benchChunk)
      const done = Math.floor((n * k) / 4)
      const o = mean(orig.slice(0, done)) / unit
      const c = mean(cand.slice(0, done)) / unit
      this.log(
        'bench',
        [
          `${cid}: trials ${done}/${n} (interleaved) · original ${fmtG(f.estCallS * o * 1e3, 3)} ms/call · ` +
            `candidate ${fmtG(f.estCallS * c * 1e3, 3)} ms/call`,
        ],
        scope,
      )
    }
    const { lo, hi } = stats.delta_ci_pct
    this.log(
      'bench',
      [`${cid}: Δ ${pct(stats.delta_pct)}% CO₂/call (95% CI ${pct(lo)}…${pct(hi)}), ${fmtP(stats.p_value)}`],
      scope,
    )
    step.ok({ stats })
    return stats
  }

  // -- decision + merge ------------------------------------------------------------

  /** Holm-correct the benched candidates and pick the largest significant cut. */
  private decide(
    f: SyntheticFunction,
    results: readonly CandidateResult[],
  ): { winner: (CandidateResult & { stats: BenchStats }) | null; ranking: RankingEntry[] } {
    const benched = results.flatMap((r) => (r.stats === null ? [] : [{ ...r, stats: r.stats }]))
    const pHolm = holm(new Map(benched.map((r) => [r.cid, r.stats.p_value])))
    const eligible: RankingEntry[] = benched.map(({ cid, stats }) => {
      const p = pHolm.get(cid) ?? 1
      const { delta_pct: delta, delta_ci_pct: ci } = stats
      return {
        candidate_id: cid,
        status: 'eligible',
        delta_pct: delta,
        delta_ci_pct: ci,
        p_value: stats.p_value,
        p_holm: p,
        significant: isSignificant(p, ci, delta, SETTINGS),
        reason: whyNot(p, ci, delta, SETTINGS),
      }
    })
    const byDelta = (x: RankingEntry, y: RankingEntry) =>
      (x.delta_pct ?? 0) - (y.delta_pct ?? 0) || byCodePoint(x.candidate_id, y.candidate_id)
    eligible.sort(byDelta)
    const best = eligible.find((e) => e.significant)
    for (const e of eligible) {
      if (e === best) e.reason = 'largest significant reduction'
      else if (best !== undefined && e.significant) e.reason = `significant; ${best.candidate_id} saves more`
    }
    const ranking: RankingEntry[] = [
      ...eligible,
      ...results.flatMap(({ cid, rejected }) =>
        rejected === null
          ? []
          : [
              {
                candidate_id: cid,
                status: 'rejected' as const,
                delta_pct: null,
                delta_ci_pct: null,
                p_value: null,
                p_holm: null,
                significant: false,
                reason: `${rejected.reason}: ${rejected.detail}`,
              },
            ],
      ),
    ]
    this.b.emit(
      'function.decision',
      {
        outcome: best !== undefined ? 'winner' : benched.length === 0 ? 'all_rejected' : 'no_significant_win',
        winner: best?.candidate_id ?? null,
        ranking,
        rule: { alpha: SETTINGS.alpha, min_effect_pct: SETTINGS.min_effect_pct, correction: 'holm' },
      },
      { function_id: functionId(f) },
    )
    const won = benched.find((r) => r.cid === best?.candidate_id)
    return {
      winner: won === undefined ? null : { ...won, stats: { ...won.stats, p_holm: pHolm.get(won.cid) ?? null } },
      ranking,
    }
  }

  /** Merge the winner and run the full suite; revert if the captured calls disagree. */
  private merge(f: SyntheticFunction, winner: CandidateResult & { stats: BenchStats }, tf: TestFile): Outcome {
    const b = this.b
    const fid = functionId(f)
    const r = new Rand(fid, 'merge')
    const scope = { function_id: fid }
    const { cid, stats, diff } = winner
    const nSamples = Math.min(f.capture.nCalls, 512)
    const suiteN = this.suiteTests + tf.test_names.length
    const duration = round(r.uniform(0.6, 1.6), 2)
    const suite: PytestResult = {
      exit_code: 0,
      passed: suiteN,
      failed: 0,
      errors: 0,
      skipped: 0,
      duration_s: duration,
      failures: [],
      output_tail: `${suiteN} passed in ${duration.toFixed(2)}s`,
    }
    const step = b.start('function.merge.started', {}, scope)
    this.log('orchestrator', [`merging ${cid} into the trunk; running the full suite`], scope)
    b.advance(r.jitter(MS.merge))
    this.log('pytest', [suite.output_tail], scope, 'info', 'stdout')

    const mismatches = f.mergeMismatches ?? []
    if (mismatches.length > 0) {
      const why =
        `${mismatches.length} of ${nSamples} captured calls differ on the merged trunk ` +
        '(running-sum float drift)'
      this.log('orchestrator', [`reverting ${cid}: ${why}`], scope, 'warn')
      step.fail(
        { kind: 'validation', message: why, detail: null },
        {
          reverted: true,
          suite,
          differential: { ok: false, n_samples: nSamples, mismatches: [...mismatches], slowdown_ratio: null },
          commit_sha: null,
          diff: null,
        },
      )
      return {
        outcome: 'reverted',
        winner: cid,
        delta_pct: stats.delta_pct,
        delta_ci_pct: stats.delta_ci_pct,
        reason: `${cid} reverted after merge: ${why}`,
      }
    }

    const sha = r.hex(40)
    this.suiteTests = suiteN
    this.merged.push({ fid, diff })
    this.log('orchestrator', [`merged ${cid} as ${sha.slice(0, 7)}`], scope)
    step.ok({
      reverted: false,
      suite,
      differential: { ok: true, n_samples: nSamples, mismatches: [], slowdown_ratio: null },
      commit_sha: sha,
      diff,
    })
    return {
      outcome: 'accepted',
      winner: cid,
      delta_pct: stats.delta_pct,
      delta_ci_pct: stats.delta_ci_pct,
      g_saved_per_1m_calls: stats.g_saved_per_1m_calls,
      kwh_saved_per_1m_calls: stats.kwh_saved_per_1m_calls,
      diff,
      // p_holm || p_value: Python's `or`, as the backend writes it
      reason: `${cid}: ${headline(stats.delta_pct, stats.delta_ci_pct)}, ${fmtP(stats.p_holm || stats.p_value, 'p_holm')}`,
    }
  }

  // -- wrap-up -----------------------------------------------------------------------

  /** The patch and report zip; sizes and hashes are synthetic, the URLs don't resolve. */
  private artifacts(): void {
    const b = this.b
    const r = new Rand('artifacts')
    const base = `/api/runs/${SYNTHETIC_RUN_ID}/artifacts`
    const step = b.start('run.artifacts.started', {})
    b.advance(r.jitter(MS.artifacts))
    const patchBytes = new TextEncoder().encode(this.merged.map((m) => m.diff).join('')).length
    const zipBytes = 2400 + Math.round(patchBytes * 0.4) + r.randint(0, 600)
    const n = this.merged.length
    this.log('orchestrator', [
      n > 0 ? `patch: ${n} files changed` : 'patch: nothing merged',
      `report: ${SYNTHETIC_RUN_ID}.zip (${zipBytes} bytes)`,
    ])
    step.ok({
      patch: n > 0 ? { url: `${base}/patch`, bytes: patchBytes, sha256: r.hex(64), files_changed: n } : null,
      zip: { url: `${base}/zip`, bytes: zipBytes, sha256: r.hex(64) },
      function_diffs: this.merged.map((m) => ({
        function_id: m.fid,
        url: `${base}/functions/${encodeURIComponent(m.fid)}/diff`,
      })),
    })
  }

  /** The backend's projection totals (netzero/pipeline/projection.py), summed in the same order. */
  private totals(): RunTotals {
    const counts: RunTotals['counts_by_outcome'] = {}
    for (const c of this.completed) counts[c.outcome] = (counts[c.outcome] ?? 0) + 1
    const accepted = this.completed.filter((c) => c.outcome === 'accepted' && c.delta_pct !== null)
    const sum = (xs: readonly number[]) => xs.reduce((a, x) => a + x, 0)
    return {
      functions_total: SELECTED.length,
      functions_done: this.completed.length,
      counts_by_outcome: counts,
      mean_reduction_pct:
        accepted.length > 0 ? sum(accepted.map((c) => c.delta_pct ?? 0)) / accepted.length : null,
      g_saved_per_1m_calls: sum(accepted.map((c) => c.g_saved_per_1m_calls ?? 0)),
      kwh_saved_per_1m_calls: sum(
        this.completed.flatMap((c) =>
          c.outcome === 'accepted' && c.kwh_saved_per_1m_calls !== null ? [c.kwh_saved_per_1m_calls] : [],
        ),
      ),
      llm_cost_usd: round(this.runCost, 8),
      duration_ms: this.b.ts - T0,
    }
  }
}

function benchStats(r: Rand, f: SyntheticFunction, plan: BenchPlan, calls: number): BenchStats {
  const n = SETTINGS.n_trials
  const sigma = plan.sigma ?? 0.04
  const orig = measure(r.next, { cpuS: f.estCallS, sigma, n, calls, power: POWER })
  const cand = measure(r.next, { cpuS: f.estCallS * (1 + plan.delta / 100), sigma, n, calls, power: POWER })
  const { deltaPct, ci, p } = compare(orig, cand)
  const o = orig.stats
  const c = cand.stats
  return {
    original: o,
    candidate: c,
    delta_pct: deltaPct,
    delta_ci_pct: ci,
    p_value: p,
    p_holm: null,
    significant: isSignificant(p, ci, deltaPct, SETTINGS),
    n_trials: n,
    calls_per_trial: calls,
    cpu_time_delta_pct: round((c.cpu_s_per_call.mean / o.cpu_s_per_call.mean - 1) * 100, 2),
    sanity_ok: true,
    power: POWER,
    g_saved_per_1m_calls: (o.g_per_call.mean - c.g_per_call.mean) * 1e6,
    kwh_saved_per_1m_calls: (o.kwh_per_call.mean - c.kwh_per_call.mean) * 1e6,
  }
}

function rejectDetail(plan: Extract<CandidatePlan, { reject: unknown }>): string {
  const reject = plan.reject
  switch (reject.reason) {
    case 'tests_failed':
      return `${reject.failing} failed: ${reject.message}`
    case 'differential_mismatch':
      return `${reject.path} after the call: expected ${reject.expected}, got ${reject.actual} (sample 0)`
    case 'syntax':
      return reject.problems.join('; ')
  }
}

function noWinner(results: readonly CandidateResult[], ranking: readonly RankingEntry[]): Outcome {
  const best = ranking.find((e) => e.status === 'eligible')
  if (best === undefined) {
    const parts = results.map((r) => `${r.cid} ${r.rejected?.reason ?? ''}`).join(', ')
    return { outcome: 'all_rejected', reason: `all ${results.length} candidates rejected: ${parts}` }
  }
  const ci = best.delta_ci_pct ?? { lo: 0, hi: 0 }
  return {
    outcome: 'no_significant_win',
    reason:
      `best: ${best.candidate_id} ${pct(best.delta_pct ?? 0)}% (95% CI ${pct(ci.lo)}…${pct(ci.hi)}), ` +
      `${fmtP(best.p_holm || 1, 'p_holm')}: ${best.reason}`,
  }
}

export interface SyntheticRun {
  events: RunEvent[]
  summary: RunTotals
  ref: ReplayRef
}

/** Plays the synthetic run; deterministic, so every call returns the same events. */
export function buildSyntheticRun(src = '/replays/synthetic.events.jsonl'): SyntheticRun {
  const run = new SyntheticRunPlayer()
  const summary = run.play()
  return {
    events: run.b.events,
    summary,
    ref: {
      id: 'synthetic',
      title: 'Synthetic demo run',
      description:
        'Six functions of the bundled demo repository, one per outcome: accepted, reverted, ' +
        'all rejected, no significant win, untestable and not capturable.',
      src,
      functions: SELECTED.length,
      duration_ms: summary.duration_ms,
    },
  }
}
