// How stored values read on the history pages. Outcome colors come from the timeline's theme.
import type { RejectReason, RunState } from '../gen/events'
import { CAUTION, FAIL, NEON } from '../timeline/theme'

const AT_REST = '#d4d4d4'
const DIM = '#6b6b6b'

export const RUN_STATE: Readonly<Record<RunState, { text: string; color: string }>> = {
  created: { text: 'starting', color: NEON },
  cloning: { text: 'cloning', color: NEON },
  installing: { text: 'installing', color: NEON },
  discovering: { text: 'discovering', color: NEON },
  triaging: { text: 'triaging', color: NEON },
  awaiting_selection: { text: 'waiting for a selection', color: CAUTION },
  optimizing: { text: 'optimizing', color: NEON },
  finalizing: { text: 'finalizing', color: NEON },
  completed: { text: 'completed', color: AT_REST },
  failed: { text: 'failed', color: FAIL },
  cancelled: { text: 'cancelled', color: DIM },
  interrupted: { text: 'interrupted', color: CAUTION },
}

const ENDED: ReadonlySet<RunState> = new Set(['completed', 'failed', 'cancelled', 'interrupted'])

/** Still going, or waiting on someone: the history may change while you look. */
export const isOpen = (state: RunState): boolean => !ENDED.has(state)

export const REJECT_REASON: Readonly<Record<RejectReason, string>> = {
  syntax: 'syntax error',
  static_rule: 'broke a static rule',
  tests_failed: 'tests failed',
  differential_mismatch: 'different output',
  timeout: 'timed out',
  llm_error: 'model error',
  identical: 'same as the original',
  bench_failed: 'bench failed',
  measurement_inconsistent: 'inconsistent measurement',
}

/** `module:qualname` as stored in `function_id`. */
export function splitFunctionId(id: string): { module: string | null; name: string } {
  const at = id.indexOf(':')
  return at < 0 ? { module: null, name: id } : { module: id.slice(0, at), name: id.slice(at + 1) }
}

export const shortSha = (sha: string | null): string | null => (sha === null ? null : sha.slice(0, 7))
