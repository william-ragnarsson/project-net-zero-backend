// Function titles over their blocks, lane tags beside the fork, and notes on the trunk.
import type { FunctionOutcome } from '../gen/events'
import { formatPct } from '../lib/format'
import type { LayoutLabel } from './layout'
import { FAIL, LABEL_TONE, NEON, NODE_R } from './theme'

type FunctionLabel = Extract<LayoutLabel, { kind: 'function' }>

const OUTCOME: Record<FunctionOutcome, { text: string; color: string }> = {
  accepted: { text: 'accepted', color: NEON },
  reverted: { text: 'reverted', color: FAIL },
  all_rejected: { text: 'all rejected', color: FAIL },
  no_significant_win: { text: 'no significant win', color: '#8a8a8a' },
  skipped_untestable: { text: 'skipped', color: '#6b6b6b' },
  skipped_capture: { text: 'skipped', color: '#6b6b6b' },
  failed: { text: 'failed', color: FAIL },
  cancelled: { text: 'cancelled', color: '#6b6b6b' },
}

/** Gap between a title and the next block's, so a long name truncates instead of running on. */
const TITLE_GAP = 28

export function LabelLayer({ labels }: { labels: readonly LayoutLabel[] }) {
  const titles = labels.filter((l): l is FunctionLabel => l.kind === 'function').sort((a, b) => a.x - b.x)
  return (
    <>
      {titles.map((label, i) => {
        const next = titles[i + 1]
        return <FunctionTitle key={label.id} label={label} maxWidth={next && next.x - label.x - TITLE_GAP} />
      })}
      {labels.map((label) => {
        if (label.kind === 'lane') {
          return (
            <span
              key={label.id}
              className="pointer-events-none absolute -translate-x-full -translate-y-1/2 pr-2 font-mono text-[10px] leading-none"
              style={{ left: label.x, top: label.y, color: LABEL_TONE[label.tone] }}
            >
              {label.text}
            </span>
          )
        }
        if (label.kind === 'note') {
          return (
            <span
              key={label.id}
              className="pointer-events-none absolute -translate-x-1/2 whitespace-nowrap font-mono text-[10px] leading-none"
              style={{ left: label.x, top: label.y - 16, color: LABEL_TONE[label.tone] }}
            >
              {label.text}
            </span>
          )
        }
        return null
      })}
    </>
  )
}

function FunctionTitle({ label, maxWidth }: { label: FunctionLabel; maxWidth: number | undefined }) {
  const outcome = label.outcome === null ? null : OUTCOME[label.outcome]
  const index = String(label.index + 1).padStart(2, '0')
  return (
    <div
      className="pointer-events-none absolute -translate-y-full"
      style={{ left: label.x - NODE_R, top: label.y - 40, maxWidth }}
    >
      <p className="truncate font-mono text-[10px] leading-4 text-[#5f5f5f]">
        {index}/{String(label.total).padStart(2, '0')}
        <span className="text-[#3f3f3f]"> · </span>
        {label.module}
      </p>
      <p className="flex items-baseline gap-2.5 whitespace-nowrap">
        <span className="truncate text-[15px] leading-6 tracking-[-0.01em] text-[#ececec]">{label.qualname}</span>
        {outcome !== null && (
          <span className="shrink-0 font-mono text-[10.5px]" style={{ color: outcome.color }}>
            {outcome.text}
            {label.deltaPct !== null && ` ${formatPct(label.deltaPct)}`}
          </span>
        )}
      </p>
    </div>
  )
}
