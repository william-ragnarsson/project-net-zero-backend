// The node glyphs (● ✓ ◆ ✎, × for a failed step, ⊘ for a cap) drawn as SVG, so they stay
// crisp at any zoom and do not depend on a font having them.
import type { ReactNode } from 'react'
import type { Glyph as GlyphName } from './layout'

const stroke = {
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.6,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
} as const

const SHAPES: Record<GlyphName | 'cap', ReactNode> = {
  dot: <circle cx="6" cy="6" r="2.6" fill="currentColor" />,
  check: <path d="M3.2 6.3 5.2 8.3 8.9 4" {...stroke} />,
  cross: <path d="M4 4 8 8M8 4 4 8" {...stroke} />,
  diamond: <path d="M6 2.4 9.6 6 6 9.6 2.4 6Z" fill="currentColor" />,
  pen: (
    <path d="M3 9 3.4 7.2 7.6 3a.85.85 0 0 1 1.2 0l.2.2a.85.85 0 0 1 0 1.2L4.8 8.6Z" {...stroke} strokeWidth={1.3} />
  ),
  cap: (
    <>
      <circle cx="6" cy="6" r="4.4" {...stroke} strokeWidth={1.4} />
      <path d="M3 9 9 3" {...stroke} strokeWidth={1.4} />
    </>
  ),
}

export function Glyph({ name, size = 12 }: { name: GlyphName | 'cap'; size?: number }) {
  return (
    <svg viewBox="0 0 12 12" width={size} height={size} aria-hidden="true">
      {SHAPES[name]}
    </svg>
  )
}
