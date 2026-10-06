// What the backend derives from a function's Python source (netzero/pipeline/fake.py's
// FakeFunction): its module file, discovery metadata, generated test files, pytest output
// and diffs. Text-level stand-ins for the backend's `ast` calls, enough for catalog.ts.
import { formatPatch, structuredPatch } from 'diff'
import type { FunctionInfo, PytestResult, TestFile, TriageItem } from '../gen/events'
import type { Draft, PyFunction, Rewrite, SyntheticFunction, Triage } from './catalog'
import type { Rand } from './random'
import { round } from './stats'

export const functionId = (f: PyFunction): string => `${f.module}:${f.qualname}`

const file = (f: PyFunction): string => `${f.module.replaceAll('.', '/')}.py`

const name = (f: PyFunction): string => f.qualname.split('.').at(-1) ?? f.qualname

const importLine = (f: PyFunction): string => `from ${f.module} import ${name(f)}`

const lines = (text: string): string[] => text.replace(/\n$/, '').split('\n')

/** Python's `a < b` on strings: code points, not locale order. */
const byCodePoint = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0)

/** `import x` lines first, then `from x import y`, each by module. */
function byImportKey(a: string, b: string): number {
  const key = (line: string) => [line.startsWith('from ') ? 1 : 0, line.split(/\s+/)[1] ?? ''] as const
  const [fa, ma] = key(a)
  const [fb, mb] = key(b)
  return fa - fb || byCodePoint(ma, mb)
}

/** The module file with `src` in place of the function. */
function render(f: PyFunction, src: string, extraImports: readonly string[] = []): string {
  const imports = [...new Set([...(f.imports ?? []), ...extraImports])].sort(byImportKey)
  let head = `"""${f.doc}"""\n\n`
  if (imports.length > 0) head += `${imports.join('\n')}\n\n`
  return `${head}\n${src}`
}

/** `name(a, b)` from the def line; string defaults are blanked so their commas don't split. */
function callHint(f: PyFunction): string {
  const def = /^def (\w+)\((.*)\):/.exec(f.source)
  if (def === null) throw new Error(`${functionId(f)}: source does not start with a def`)
  const params = (def[2] ?? '')
    .replace(/"[^"]*"|'[^']*'/g, '""')
    .split(',')
    .map((p) => /^\s*(\w+)/.exec(p)?.[1])
    .filter((p) => p !== undefined)
  return `${def[1]}(${params.join(', ')})`
}

export function functionInfo(f: PyFunction): FunctionInfo {
  const line = lines(render(f, '')).length + 1
  const src = lines(f.source)
  return {
    function_id: functionId(f),
    module: f.module,
    qualname: f.qualname,
    kind: 'function',
    file: file(f),
    line,
    end_line: line + src.length - 1,
    loc: src.filter((l) => l.trim() !== '' && !l.trim().startsWith('#')).length,
    import_line: importLine(f),
    call_hint: callHint(f),
    source: f.source,
  }
}

/** Discovery ranks by heuristic alone; triage adds the model's ratings. */
export function triageItem(f: PyFunction & { triage: Triage }, llm: boolean, preselected = false): TriageItem {
  const { source: _source, ...info } = functionInfo(f)
  const t = f.triage
  return {
    ...info,
    heuristic_score: t.heuristic,
    llm_potential: llm ? t.potential : null,
    llm_testability: llm ? t.testability : null,
    score: llm ? t.score : t.heuristic,
    reasons: [...(llm ? t.reasons : t.heuristicReasons)],
    skip_reason: t.skipReason ?? null,
    preselected,
  }
}

export function draft(f: SyntheticFunction, attempt: number): Draft {
  const d = f.drafts[Math.min(attempt, f.drafts.length - 1)]
  if (d === undefined) throw new Error(`${functionId(f)} has no test drafts`)
  return d
}

export function testFile(f: SyntheticFunction, attempt: number): TestFile {
  const d = draft(f, attempt)
  const code = `import pytest\n\n${importLine(f)}\n\n\n${d.body.replace(/^\n+|\n+$/g, '')}\n`
  const names = [...code.matchAll(/^def (test\w*)\(/gm)].map((m) => m[1] ?? '')
  const workload = /^@pytest\.mark\.nz_workload\ndef (\w+)\(/m.exec(code)?.[1] ?? ''
  return {
    path: `tests/netzero/test_${f.module.replaceAll('.', '_')}.py`,
    code,
    test_names: names,
    workload_test: workload,
    notes: `${names.length} tests; \`${workload}\` is the benchmark workload`,
    diagnosis: attempt > 0 ? (d.diagnosis ?? '') : '',
  }
}

/** A pytest-style traceback tail pointing at the first assert of `test`. */
function tbTail(tf: TestFile, test: string, message: string): string {
  const colon = message.indexOf(':')
  const head = message.slice(0, Math.max(colon, 0))
  const exc = colon >= 0 && /^[A-Za-z_]\w*Error$/.test(head) ? head : 'AssertionError'
  const code = lines(tf.code)
  const def = code.findIndex((l) => l.startsWith(`def ${test}(`))
  if (def < 0) return `E       ${message}`
  const end = code.findIndex((l, i) => i > def && /^\S/.test(l))
  const body = code.slice(def + 1, end < 0 ? undefined : end)
  const offset = body.findIndex((l) => /^\s+assert\b/.test(l))
  const at = offset < 0 ? def : def + 1 + offset
  return `    def ${test}():\n>${(code[at] ?? '').slice(1)}\nE       ${message}\n\n${tf.path}:${at + 1}: ${exc}`
}

export function pytestResult(tf: TestFile, rand: Rand, failing?: string, message = ''): PytestResult {
  const n = tf.test_names.length
  const duration = round(rand.uniform(0.15, 0.9), 2)
  if (failing === undefined) {
    return {
      exit_code: 0,
      passed: n,
      failed: 0,
      errors: 0,
      skipped: 0,
      duration_s: duration,
      failures: [],
      output_tail: `${tf.path} ${'.'.repeat(n)}  [100%]\n\n${n} passed in ${duration.toFixed(2)}s`,
    }
  }
  const nodeid = `${tf.path}::${failing}`
  const dots = tf.test_names.map((t) => (t === failing ? 'F' : '.')).join('')
  return {
    exit_code: 1,
    passed: n - 1,
    failed: 1,
    errors: 0,
    skipped: 0,
    duration_s: duration,
    failures: [{ nodeid, message, tb_tail: tbTail(tf, failing, message) }],
    output_tail:
      `${tf.path} ${dots}  [100%]\n\nFAILED ${nodeid} - ${message}\n` +
      `1 failed, ${n - 1} passed in ${duration.toFixed(2)}s`,
  }
}

/** The verbose pytest lines a run logs. */
export function pytestLines(tf: TestFile, result: PytestResult): string[] {
  const failed = new Set(result.failures.map((f) => f.nodeid.split('::').at(-1)))
  return [
    `collected ${tf.test_names.length} items`,
    '',
    ...tf.test_names.map((t) => `${tf.path}::${t} ${failed.has(t) ? 'FAILED' : 'PASSED'}`),
    '',
    ...result.output_tail.split('\n').slice(-1),
  ]
}

/** A `git diff`-style patch of the module, from the original function to `rewrite`. */
export function rewriteDiff(f: PyFunction, rewrite: Rewrite): string {
  const path = file(f)
  const patch = structuredPatch(
    `a/${path}`,
    `b/${path}`,
    render(f, f.source),
    render(f, rewrite.code, rewrite.newImports),
    undefined,
    undefined,
    { context: 3 },
  )
  return formatPatch({ ...patch, isGit: true })
}
