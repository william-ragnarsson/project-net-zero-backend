import type { LogEntry, RunModel } from '../state/types'

export interface LogLine {
  seq: number
  source: LogEntry['source']
  level: LogEntry['level']
  stream: LogEntry['stream']
  text: string
}

/**
 * Every log scope merged back into one stream by seq, as plain lines, keeping the newest
 * `max`. `truncated` says whether older lines are missing, from a buffer or from the cap.
 */
export function selectLogLines(m: RunModel, max: number): { lines: LogLine[]; truncated: boolean } {
  const buffers = Object.values(m.logs)
  const entries = buffers.flatMap((b) => b.entries).sort((a, b) => a.seq - b.seq)
  const lines = entries.flatMap((e) =>
    e.lines.map((text) => ({ seq: e.seq, source: e.source, level: e.level, stream: e.stream, text })),
  )
  const cut = lines.length > max
  return {
    lines: cut ? lines.slice(lines.length - max) : lines,
    truncated: cut || buffers.some((b) => b.dropped > 0),
  }
}
