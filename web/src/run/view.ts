// Which part of the run page to show. It reads the model alone (plus the connection status),
// so a reload, a replay of the log and the live stream all land on the same view.
import type { LiveStatus } from '../sources/live'
import type { RunModel } from '../state/types'

export type RunViewKind = 'not_found' | 'error' | 'loading' | 'finding' | 'choose' | 'optimizing' | 'done' | 'ended'

/**
 * How long a run sits in awaiting_selection before the choice shows. An auto-selected run
 * emits run.selection.confirmed straight after, and RunCreatedData does not say whether
 * auto_select was set, so without the wait the choice would flash for a frame.
 */
export const CHOOSE_GRACE_MS = 400

export function pickView(m: RunModel, live: LiveStatus, awaitingSinceMs: number | null, now: number): RunViewKind {
  if (live.kind === 'not_found') return 'not_found'
  if (live.kind === 'error' && m.lastSeq === 0) return 'error'
  if (m.lastSeq === 0) return 'loading'
  if (m.terminal !== null) return m.terminal.type === 'run.completed' ? 'done' : 'ended'
  if (m.selection !== null) return 'optimizing'
  if (m.state === 'awaiting_selection' && awaitingSinceMs !== null && now - awaitingSinceMs >= CHOOSE_GRACE_MS) {
    return 'choose'
  }
  return 'finding'
}

/**
 * Since when the choice may show, for a model in awaiting_selection: at once (-Infinity) when
 * the state came from the hydrated log, so a reload while choosing shows the choice straight
 * away, otherwise from `now`.
 */
export function awaitingSince(m: RunModel, hydratedThroughSeq: number, now: number): number | null {
  if (m.state !== 'awaiting_selection') return null
  const entered = m.history.findLast((h) => h.to === 'awaiting_selection')
  return entered !== undefined && entered.bornSeq <= hydratedThroughSeq ? -Infinity : now
}
