import { Fragment, type ReactNode } from 'react'

/** CO₂ with a real subscript: the brand fonts have no ₂ glyph. */
export function Co2() {
  return (
    <>
      CO<sub className="relative top-[0.1em] align-baseline text-[0.72em]">2</sub>
    </>
  )
}

/** Text with each `CO₂` or `CO2` drawn as <Co2/>. */
export function withCo2(text: string): ReactNode {
  const parts = text.split(/CO₂|CO2/)
  if (parts.length === 1) return text
  return parts.map((part, i) => (
    <Fragment key={i}>
      {i > 0 && <Co2 />}
      {part}
    </Fragment>
  ))
}

// Server and replay reason strings use math glyphs the fonts lack and the copy rules ban.
const PLAIN: [RegExp, string][] = [
  [/ ≥ /g, ' is at least '],
  [/α=/g, 'alpha '],
  [/Δ /g, 'change of '],
  [/ → /g, ' to '],
  [/→/g, ' to '],
  [/≈/g, 'about '],
  [/CO₂/g, 'CO2'],
]

/** A reason string in plain words, for `title` and `aria-label`: `p_holm=0.081 is at least alpha 0.05`. */
export const plainText = (text: string): string =>
  PLAIN.reduce((s, [from, to]) => s.replace(from, to), text).replace(/`([^`]+)`/g, '$1')

/** How every server or replay reason string renders: plain words, CO₂ subscripts, and `code` spans in mono. */
export function plainReason(text: string): ReactNode {
  const plain = PLAIN.reduce((s, [from, to]) => s.replace(from, to), text)
  const parts = plain.split(/`([^`]+)`/)
  if (parts.length === 1) return withCo2(plain)
  // split with a capture group puts the code spans at odd indexes
  return parts.map((part, i) =>
    i % 2 === 1 ? (
      <code key={i} className="font-mono text-[0.9em] text-ink">
        {part}
      </code>
    ) : (
      <Fragment key={i}>{withCo2(part)}</Fragment>
    ),
  )
}
