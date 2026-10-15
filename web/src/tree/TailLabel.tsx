import type { RunModel } from '../state/types'
import type { TailNode } from './derive'
import { Tip } from './Pill'
import { tailText, tailTip } from './text'

/** The line under the last layer: what comes next, or how the run ended. */
export function TailLabel({ tail, model }: { tail: TailNode; model: RunModel }) {
  const t = tailText(tail, model)
  const tip = tailTip(tail, model)
  const label = (
    <span
      // a label with a tooltip takes focus, so the tooltip is reachable without a pointer
      tabIndex={tip === null ? undefined : 0}
      className={`block whitespace-nowrap text-xs ${t.tone === 'fail' ? 'text-fail' : 'text-ink-2'}`}
    >
      {t.text}
    </span>
  )
  return <Tip text={tip}>{label}</Tip>
}
