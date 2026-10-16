import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { plainReason, plainText, withCo2 } from './Co2'

const BANNED = ['≥', 'α', 'Δ', '→', '≈', '₂']
const html = (node: ReturnType<typeof plainReason>) => renderToStaticMarkup(<>{node}</>)

describe('plain reason strings', () => {
  it('spells out each glyph', () => {
    expect(plainText('p_holm=0.081 ≥ α=0.05')).toBe('p_holm=0.081 is at least alpha 0.05')
    expect(plainText('Δ −12.0%')).toBe('change of −12.0%')
    expect(plainText('A → B')).toBe('A to B')
    expect(plainText('A→B')).toBe('A to B')
    expect(plainText('≈3 µg')).toBe('about 3 µg')
    expect(plainText('CO₂ per call')).toBe('CO2 per call')
  })

  it('renders a why_not reason from bench/stats.py without the glyphs', () => {
    const out = html(plainReason('p_holm=0.081 ≥ α=0.05'))
    expect(out).toBe('p_holm=0.081 is at least alpha 0.05')
    for (const glyph of BANNED) expect(out).not.toContain(glyph)
  })

  it('renders CO₂ reasons from pipeline/candidates.py and bench/stats.py with a subscript', () => {
    for (const reason of [
      'CO₂ Δ −12.0% disagrees with CPU-time Δ +3.0%',
      'measurement_inconsistent: CO₂ and CPU time moved in opposite directions (−12.0% vs CPU +3.0%)',
    ]) {
      const out = html(plainReason(reason))
      for (const glyph of BANNED) expect(out).not.toContain(glyph)
      expect(out).toContain('CO<sub')
      expect(out).toContain('>2</sub>')
    }
    expect(html(plainReason('CO₂ Δ −12.0% disagrees with CPU-time Δ +3.0%'))).toMatch(
      /^CO<sub[^>]*>2<\/sub> change of −12\.0% disagrees with CPU-time change of \+3\.0%$/,
    )
  })

  it('draws `code` spans from triage reasons in mono, without the backticks', () => {
    const reason = 'O(n²): `item not in result` scans a list on every iteration'
    const out = html(plainReason(reason))
    expect(out).not.toContain('`')
    expect(out).toMatch(/<code class="font-mono[^"]*">item not in result<\/code> scans/)
    expect(plainText(reason)).toBe('O(n²): item not in result scans a list on every iteration')
    expect(html(plainReason('`CO2` stays code, CO2 does not'))).toMatch(/<code[^>]*>CO2<\/code> stays code, CO<sub/)
  })

  it('leaves text without CO2 as a plain string', () => {
    expect(withCo2('below the 5% minimum effect')).toBe('below the 5% minimum effect')
    expect(html(withCo2('CO2e saved'))).toMatch(/^CO<sub[^>]*>2<\/sub>e saved$/)
  })
})
