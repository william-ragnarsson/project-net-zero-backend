import { useMemo } from 'react'
import type { RunModel } from '../state/types'
import { EmptyState } from '../ui/EmptyState'
import { selectLogLines, type LogLine } from './log'

/** Lines kept on screen; the events file has the rest. */
const MAX_LINES = 2000

const lineTone = (l: LogLine): string => (l.level === 'error' ? 'text-fail' : l.stream === 'stderr' ? 'text-ink-2' : '')

/** The run's log lines in one scrolling block, newest at the bottom. */
export function LogTab({ model }: { model: RunModel }) {
  const { lines, truncated } = useMemo(() => selectLogLines(model, MAX_LINES), [model])
  if (lines.length === 0) return <EmptyState title="No log lines yet" />
  return (
    <pre className="max-h-[70vh] overflow-auto rounded-lg border border-control bg-surface p-3 font-mono text-code text-ink">
      {truncated && <div className="text-ink-3">Older lines are not shown. Download events for the full log</div>}
      {lines.map((l, i) => (
        <div key={i} className={lineTone(l)}>
          <span className="text-ink-3">{l.source.padEnd(12)}</span> {l.text}
        </div>
      ))}
    </pre>
  )
}
