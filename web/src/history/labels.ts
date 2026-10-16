// How stored values read on the history pages, in the words and tones the rest of the app uses.
import type { FunctionOutcome, RejectReason, RunState } from '../gen/events'
import { stateLabel } from '../lib/runs'
import type { BadgeTone } from '../ui/Badge'

const STATE_TONE: Readonly<Partial<Record<RunState, BadgeTone>>> = {
  awaiting_selection: 'caution',
  completed: 'success',
  failed: 'fail',
  cancelled: 'neutral',
  interrupted: 'neutral',
}

/** A stored run's state as a badge. Every state not listed is still going. */
export const runState = (state: RunState): { text: string; tone: BadgeTone } => ({
  text: stateLabel(state),
  tone: STATE_TONE[state] ?? 'live',
})

const ENDED: ReadonlySet<RunState> = new Set(['completed', 'failed', 'cancelled', 'interrupted'])

/** Still going, or waiting on someone: the history may change while you look. */
export const isOpen = (state: RunState): boolean => !ENDED.has(state)

/** A small dot or bar in a tone's colour. */
export const TONE_FILL: Readonly<Record<BadgeTone, string>> = {
  success: 'bg-accent',
  live: 'bg-accent-soft',
  caution: 'bg-caution-soft',
  fail: 'bg-fail-soft',
  neutral: 'bg-control',
}

export const OUTCOME: Readonly<Record<FunctionOutcome, { text: string; tone: BadgeTone }>> = {
  accepted: { text: 'Accepted', tone: 'success' },
  no_significant_win: { text: 'No significant win', tone: 'neutral' },
  all_rejected: { text: 'All rejected', tone: 'fail' },
  reverted: { text: 'Reverted', tone: 'fail' },
  skipped_untestable: { text: 'Skipped', tone: 'caution' },
  skipped_capture: { text: 'Skipped', tone: 'caution' },
  failed: { text: 'Failed', tone: 'fail' },
  cancelled: { text: 'Cancelled', tone: 'neutral' },
}

export const REJECT_REASON: Readonly<Record<RejectReason, string>> = {
  syntax: 'Syntax error',
  static_rule: 'Broke a static rule',
  tests_failed: 'Tests failed',
  differential_mismatch: 'Different output',
  timeout: 'Timed out',
  llm_error: 'Model error',
  identical: 'Same as the original',
  bench_failed: 'Bench failed',
  measurement_inconsistent: 'Inconsistent measurement',
}

/** `module:qualname` as stored in `function_id`. */
export function splitFunctionId(id: string): { module: string | null; name: string } {
  const at = id.indexOf(':')
  return at < 0 ? { module: null, name: id } : { module: id.slice(0, at), name: id.slice(at + 1) }
}

export const shortSha = (sha: string | null): string | null => (sha === null ? null : sha.slice(0, 7))
