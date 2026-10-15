import { CodeText } from './CodeLines'
import type { DiffRow } from './diff'

/** Diff rows as a card draws them: added and removed lines tinted, skipped lines as one gap row. */
export function DiffLines({ rows }: { rows: readonly DiffRow[] }) {
  return (
    <div className="w-max min-w-full py-1.5">
      {rows.map((r, i) =>
        r.kind === 'gap' ? (
          <div key={i} className="code-row gap">
            <span className="ln" />
            <span className="tx">…</span>
          </div>
        ) : (
          <div key={i} className={`code-row ${r.kind}`}>
            <span className="ln">{r.line ?? ''}</span>
            <CodeText tokens={r.tokens} />
          </div>
        ),
      )}
    </div>
  )
}
