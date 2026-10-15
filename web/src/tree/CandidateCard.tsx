import { useMemo } from 'react'
import { withCo2 } from '../components/Co2'
import { formatCI, formatMass, formatP, formatPct, massUnit, sig3 } from '../lib/format'
import type { RunModel } from '../state/types'
import { CardFooter, CardHeader, CardShell, CodeBody, Facts, type CardProps } from './Card'
import { CiChart } from './CiChart'
import { benchOf, gateOf, rankOf, type CandNode } from './derive'
import { compactDiff, fullDiffRows } from './diff'
import { DiffLines } from './DiffLines'
import { Glyph } from './Glyph'
import { baselineG, candAria, candFoot, candTitle, checkFail, isWriting, plural, pytestTotal, rejText, writtenAttempt } from './text'

const WIN_VERDICTS = new Set(['winner', 'merging', 'merged'])

export function CandidateCard({ node, model, ...p }: CardProps & { node: CandNode; model: RunModel }) {
  const f = model.functions[node.functionId]
  const c = f?.candidates[node.letter]
  const diff = c === undefined ? null : (writtenAttempt(c)?.write?.result?.candidate?.diff ?? null)
  const rows = useMemo(() => (p.pinned ? fullDiffRows(diff) : compactDiff(diff)), [diff, p.pinned])
  if (f === undefined || c === undefined) return null
  const title = candTitle(node, c)
  const foot = candFoot(node, c, f, p.live)
  const writing = isWriting(node)
  const win = node.verdict !== null && WIN_VERDICTS.has(node.verdict)
  return (
    <CardShell framed={p.framed} pinned={p.pinned}>
      <CardHeader id={p.id} pinned={p.pinned} aria={candAria(node, c, true)} win={win} onSelect={p.onSelect}>
        <span className="font-medium">{node.letter}</span>
        <span className={`truncate ${title.hint ? 'text-ink-3' : ''}`}>{title.text}</span>
        {node.deltaPct !== null && <span className="ml-auto shrink-0 tabular-nums">{formatPct(node.deltaPct)}</span>}
      </CardHeader>
      {writing ? (
        <p className="px-3 py-2.5 text-xs text-ink-3">Writing…</p>
      ) : (
        rows.length > 0 && (
          <CodeBody>
            <DiffLines rows={rows} />
          </CodeBody>
        )
      )}
      {p.pinned && <CandidateDetail node={node} model={model} />}
      <CardFooter>
        {foot.repair !== null && <span className="shrink-0 text-ink-3">{foot.repair}</span>}
        <Glyph glyph={foot.glyph} tone={foot.tone} />
        <span className={`truncate ${foot.tone === 'fail' ? 'text-fail' : foot.tone === 'caution' ? 'text-caution' : ''}`}>
          {foot.text}
        </span>
      </CardFooter>
    </CardShell>
  )
}

function CandidateDetail({ node, model }: { node: CandNode; model: RunModel }) {
  const f = model.functions[node.functionId]
  const c = f?.candidates[node.letter]
  if (f === undefined || c === undefined) return null
  const code = writtenAttempt(c)?.write?.result?.candidate ?? null
  const b = benchOf(c)
  const rank = rankOf(f, node.letter)
  const gate = gateOf(model, f)
  const unit = massUnit(baselineG(f))
  const ci = rank?.delta_ci_pct ?? b?.delta_ci_pct ?? null
  const p = rank?.p_holm ?? b?.p_holm ?? null
  const check = writtenAttempt(c)?.check?.result ?? null
  const repairs = c.attempts.filter((a) => a.write?.started.kind === 'repair').length
  const checks =
    check === null
      ? null
      : check.ok
        ? [
            check.tests !== null && `${plural(pytestTotal(check.tests), 'test')} pass`,
            check.differential !== null &&
              `${check.differential.n_samples - check.differential.mismatches.length}/${check.differential.n_samples} captured calls identical`,
          ]
            .filter(Boolean)
            .join(' · ') || 'pass'
        : checkFail(check)
  return (
    <div className="border-t border-line">
      {code !== null && code.rationale !== '' && <p className="px-3 pt-2.5 text-xs text-ink-2">{code.rationale}</p>}
      {b !== null && (
        <div className="px-3 pt-2">
          <CiChart
            rows={[{ key: node.letter, label: node.letter, delta: b.delta_pct, ci, clears: (rank?.significant ?? b.significant) && b.delta_pct <= -gate }]}
            gate={gate}
          />
        </div>
      )}
      <Facts
        rows={[
          b !== null && ['Change', `${formatPct(b.delta_pct)} · interval ${formatCI(ci)}`],
          b !== null && ['p (Holm)', formatP(p)],
          b !== null && [
            'Per call',
            <>
              {formatMass(b.original.g_per_call.mean, unit)} to {formatMass(b.candidate.g_per_call.mean, unit)}
            </>,
          ],
          b !== null &&
            b.g_saved_per_1m_calls !== null && [withCo2('CO2e saved'), `${sig3(b.g_saved_per_1m_calls)} g per 1M calls`],
          b !== null && ['Trials', String(b.n_trials)],
          checks !== null && ['Checks', checks],
          repairs > 0 && ['Repairs', String(repairs)],
          c.rejection !== null && ['Rejected', c.rejection.detail ? `${rejText(c)} · ${c.rejection.detail}` : rejText(c)],
        ]}
      />
    </div>
  )
}
