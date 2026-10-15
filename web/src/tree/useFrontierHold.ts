// The frontier hold as a hook. The hold updates during render, so the layer that just finished
// never shows folded for a frame before it holds; one timeout wakes the view when it moves on.
import { useEffect, useReducer, useRef } from 'react'
import type { RunModel } from '../state/types'
import { fnId, type NodeId, type Tree } from './derive'
import { deadline, expandedFid, IDLE, observe, tick, type Hold, type Obs } from './frontierHold'

export function obsOf(tree: Tree, model: RunModel, epoch: number, speed: number): Obs {
  const doneFids = new Set<string>()
  for (const f of tree.functions) if (model.functions[f.functionId]?.outcome != null) doneFids.add(f.functionId)
  return {
    epoch,
    speed,
    lastSeq: tree.lastSeq,
    frontierFid: tree.frontierId === null ? null : (tree.functions.at(-1)?.functionId ?? null),
    doneCount: doneFids.size,
    doneFids,
  }
}

interface HoldState {
  session: unknown
  key: string | null
  hold: Hold
  prev: Obs | null
}

export interface FrontierHold {
  /** the function node whose layer is open, or null while a layer folds */
  expandedId: NodeId | null
  /** a finished layer is still open */
  holding: boolean
}

/**
 * `session` is the identity of the run being shown: a new session can start again at epoch 0
 * with seqs that collide with the old one.
 */
export function useFrontierHold(
  tree: Tree,
  model: RunModel,
  epoch: number,
  speed: number,
  reduced: boolean,
  session: unknown,
): FrontierHold {
  const ref = useRef<HoldState>({ session, key: null, hold: IDLE, prev: null })
  const [, wake] = useReducer((n: number) => n + 1, 0)
  const next = obsOf(tree, model, epoch, speed)
  const now = performance.now()
  const s = ref.current
  const key = `${epoch}:${tree.lastSeq}:${speed}`
  // keyed on (session, epoch, lastSeq, speed), so a second render of the same props
  // (StrictMode, a discarded concurrent pass) changes nothing
  if (s.session !== session || s.key !== key) {
    const prev = s.session === session ? s.prev : null
    s.hold = observe(tick(s.hold, now, reduced), prev, next, now)
    s.prev = next
    s.key = key
    s.session = session
  } else {
    s.hold = tick(s.hold, now, reduced)
  }
  const hold = s.hold

  useEffect(() => {
    const d = deadline(hold)
    if (d === null) return
    const id = window.setTimeout(wake, Math.max(0, d - performance.now()))
    return () => window.clearTimeout(id)
  }, [hold])

  const fid = expandedFid(hold, next)
  return { expandedId: fid === null ? null : fnId(fid), holding: hold.kind === 'hold' }
}
