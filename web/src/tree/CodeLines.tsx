import type { CSSProperties } from 'react'
import { lineIndent, type Line } from './python'

/** Highlighted tokens of one line; a hanging indent keeps wrapped text under its code. */
export function CodeText({ tokens }: { tokens: Line }) {
  const style = { '--hi': `${lineIndent(tokens)}ch` } as CSSProperties
  return (
    <span className="tx" style={style}>
      {tokens.length === 0 ? ' ' : tokens.map((t, i) => (t.cls === null ? t.text : <span key={i} className={`tok-${t.cls}`}>{t.text}</span>))}
    </span>
  )
}

/** Source lines numbered from `first`. The parent scrolls; the rows set their own width. */
export function CodeLines({ lines, first }: { lines: readonly Line[]; first: number }) {
  return (
    <div className="w-max min-w-full py-1.5">
      {lines.map((tokens, i) => (
        <div key={i} className="code-row">
          <span className="ln">{first + i}</span>
          <CodeText tokens={tokens} />
        </div>
      ))}
    </div>
  )
}
