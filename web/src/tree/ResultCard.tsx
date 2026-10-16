import { withCo2 } from '../components/Co2'
import { formatP, sig3 } from '../lib/format'
import type { RunModel } from '../state/types'
import { CardHeader, CardShell, Facts, type CardProps } from './Card'
import { CiChart, type CiRow } from './CiChart'
import { alphaOf, gateOf, type ResultNode } from './derive'
import { Glyph } from './Glyph'
import { plural, resultAria, resultFace, sentence } from './text'

export function ResultCard({ node, model, ...p }: CardProps & { node: ResultNode; model: RunModel }) {
  const f = model.functions[node.functionId]
  if (f === undefined) return null
  const r = resultFace(node, f, p.live)
  const merged = node.result === 'merged'
  const gate = gateOf(model, f)
  const rows: CiRow[] = (f.decision?.ranking ?? []).flatMap((e) =>
    e.delta_pct === null
      ? []
      : [{ key: e.candidate_id, label: e.candidate_id, delta: e.delta_pct, ci: e.delta_ci_pct, clears: e.significant && e.delta_pct <= -gate }],
  )
  const merge = f.merge?.result ?? null
  const rule = f.decision?.rule
  const sha = merge?.commit_sha ?? null
  const saved = f.outcome?.g_saved_per_1m_calls ?? null
  const mismatches = node.result === 'reverted' ? (merge?.differential?.mismatches ?? []) : []
  return (
    <CardShell framed={p.framed} pinned={p.pinned}>
      <CardHeader id={p.id} pinned={p.pinned} aria={resultAria(node, true)} win={merged} onSelect={p.onSelect}>
        <Glyph glyph={r.glyph} tone={merged ? 'win' : r.tone} />
        <span className={r.tone === 'fail' ? 'text-fail' : r.tone === 'caution' ? 'text-caution' : ''}>{r.text}</span>
        {r.delta !== null && <span className="ml-auto tabular-nums">{r.delta}</span>}
      </CardHeader>
      {rows.length > 0 && (
        <div className="px-3 pt-2">
          <CiChart rows={rows} gate={gate} />
        </div>
      )}
      {rule !== undefined && (
        <p className="px-3 pt-2 text-xs text-ink-2">
          {withCo2(
            `A winner needs p below ${alphaOf(model, f)} after ${sentence(rule.correction)} correction and at least ${gate}% less CO2e per call`,
          )}
        </p>
      )}
      <Facts
        rows={[
          rows.length > 0 && ['p (Holm)', rows.map((row) => `${row.label} ${formatP(f.decision?.ranking.find((e) => e.candidate_id === row.key)?.p_holm ?? null)}`).join(' · ')],
          merge?.suite != null && ['Suite', `${plural(merge.suite.passed, 'test')} pass${merge.suite.failed + merge.suite.errors > 0 ? `, ${merge.suite.failed + merge.suite.errors} fail` : ''}`],
          merge?.differential != null && [
            'Captured calls',
            `${merge.differential.n_samples - merge.differential.mismatches.length}/${merge.differential.n_samples} identical`,
          ],
          sha !== null && ['Commit', <span className="font-mono">{sha.slice(0, 7)}</span>],
          merged && saved !== null && [withCo2('CO2e saved'), `${sig3(saved)} g per 1M calls`],
          node.result === 'failed' && ['Error', merge?.error?.message ?? f.outcome?.reason ?? ''],
        ]}
      />
      {mismatches.length > 0 && (
        <div data-codebox className="max-h-[150px] overflow-auto border-t border-line px-3 py-2 font-mono text-code text-ink-2">
          {mismatches.slice(0, 20).map((mm, i) => (
            <p key={i} className="whitespace-pre">
              #{mm.sample_idx} {mm.path || mm.kind}: expected {String(mm.expected)}, got {String(mm.actual)}
            </p>
          ))}
        </div>
      )}
    </CardShell>
  )
}
