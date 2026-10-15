import { Icon } from '../ui/Icon'
import type { Tone } from './derive'
import type { Glyph as GlyphName } from './text'

/**
 * A status mark. Only a pass and a failure draw: a dot or ring beside every status is the
 * chip look the app avoids, and the words already say what the other marks said.
 */
export function Glyph({ glyph, tone }: { glyph: GlyphName | null; tone: Tone }) {
  if (glyph === 'ok') return <Icon name="check" size={12} className={`shrink-0 ${tone === 'win' ? 'text-accent' : 'text-ink-2'}`} />
  if (glyph === 'x') return <Icon name="close" size={12} className="shrink-0 text-fail" />
  return null
}
