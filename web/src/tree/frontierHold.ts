// Which function layer is expanded. The live function's layer is open; when it finishes, it
// stays open a moment (the hold), folds (the collapse), and only then does the next one open.
// Display state only: the model never sees it, and `now` is the page clock, never event ts.

/** How long a finished layer stays open, by replay speed (live runs use 1). */
export const holdMs = (speed: number): number => (speed <= 1 ? 1200 : speed <= 2 ? 1000 : speed <= 4 ? 850 : 700)

export const COLLAPSE_MS = 360
export const COLLAPSE_MS_REDUCED = 120

/** What the hold looks at in each render. */
export interface Obs {
  /** the session's epoch: a seek, step or reconnect jumps and must not animate */
  epoch: number
  speed: number
  lastSeq: number
  /** the function the run is working on */
  frontierFid: string | null
  doneCount: number
  doneFids: ReadonlySet<string>
}

export type Hold =
  | { kind: 'idle' }
  | { kind: 'hold'; fid: string; until: number }
  | { kind: 'collapse'; fid: string; until: number }

export const IDLE: Hold = { kind: 'idle' }

/**
 * The hold after the model moved from `prev` to `next`.
 *
 * A function that starts and finishes inside one batch, while the previous frontier was
 * another function or none, gets no hold: only the frontier seen in the last render can hold.
 */
export function observe(h: Hold, prev: Obs | null, next: Obs, now: number): Hold {
  if (prev === null || prev.epoch !== next.epoch || prev.speed !== next.speed) return IDLE
  const newlyDone = next.doneCount - prev.doneCount
  // the batch skipped over whole functions (a fast replay, a backlog): show where it landed
  if (newlyDone > 1) return IDLE
  if (newlyDone === 1 && prev.frontierFid !== null && next.doneFids.has(prev.frontierFid)) {
    return { kind: 'hold', fid: prev.frontierFid, until: now + holdMs(next.speed) }
  }
  return h
}

/** The hold once the clock reaches `now`. */
export function tick(h: Hold, now: number, reduced: boolean): Hold {
  if (h.kind === 'hold' && now >= h.until) {
    return { kind: 'collapse', fid: h.fid, until: now + (reduced ? COLLAPSE_MS_REDUCED : COLLAPSE_MS) }
  }
  if (h.kind === 'collapse' && now >= h.until) return IDLE
  return h
}

/** The function whose layer is open. */
export function expandedFid(h: Hold, next: Obs): string | null {
  if (h.kind === 'hold') return h.fid
  if (h.kind === 'collapse') return null
  return next.frontierFid
}

/** When `tick` next changes the hold, or null when idle. */
export const deadline = (h: Hold): number | null => (h.kind === 'idle' ? null : h.until)
