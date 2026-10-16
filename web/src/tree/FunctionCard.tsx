import { useMemo } from 'react'
import { formatMass, massUnit } from '../lib/format'
import type { RunModel } from '../state/types'
import { CardFooter, CardHeader, CardShell, CodeBody, Facts, type CardProps } from './Card'
import { CodeLines } from './CodeLines'
import type { FnNode } from './derive'
import { Glyph } from './Glyph'
import { highlightPython } from './python'
import { baselineG, fnAria, fnStatus, plural, pytestTotal, skipText } from './text'

/** Lines of source the live card shows; the pinned card shows all of it. */
const LIVE_LINES = 10

export function FunctionCard({ node, model, ...p }: CardProps & { node: FnNode; model: RunModel }) {
  const f = model.functions[node.functionId]
  const info = f?.started?.info
  const lines = useMemo(() => highlightPython(info?.source ?? ''), [info?.source])
  if (f === undefined) return null
  const s = fnStatus(node, f, p.live)
  const shown = p.pinned ? lines : lines.slice(0, LIVE_LINES)
  return (
    <CardShell framed={p.framed} pinned={p.pinned}>
      <CardHeader id={p.id} pinned={p.pinned} aria={fnAria(node, f, true)} onSelect={p.onSelect}>
        <span className="truncate">{node.qualname}</span>
        {info !== undefined && (
          <span className="ml-auto shrink-0 text-xs text-ink-3">
            {info.file.split('/').at(-1)}:{info.line}
          </span>
        )}
      </CardHeader>
      {shown.length > 0 && (
        <CodeBody>
          <CodeLines lines={shown} first={info?.line ?? 1} />
        </CodeBody>
      )}
      {p.pinned && <FunctionFacts node={node} model={model} />}
      <CardFooter>
        <Glyph glyph={s.glyph} tone={s.tone} />
        <span className={`truncate ${s.tone === 'fail' ? 'text-fail' : ''}`}>{s.text}</span>
        {s.mass !== null && <span className="tabular-nums text-ink">{s.mass}</span>}
      </CardFooter>
    </CardShell>
  )
}

function FunctionFacts({ node, model }: { node: FnNode; model: RunModel }) {
  const f = model.functions[node.functionId]
  if (f === undefined) return null
  const triage = model.triage.find((t) => t.function_id === node.functionId)
  const run = f.tests.at(-1)?.runs.at(-1)?.result?.result ?? null
  const cap = f.capture?.result ?? null
  const base = f.baseline?.result?.original ?? null
  const unit = massUnit(baselineG(f))
  return (
    <div className="border-t border-line">
      <Facts
        rows={[
          triage !== undefined && triage.reasons.length > 0 && ['Why', triage.reasons[0]],
          f.started !== null && ['File', `${f.started.info.file}:${f.started.info.line}`],
          run !== null && ['Tests', run.failed + run.errors === 0 ? `${plural(pytestTotal(run), 'test')} pass` : `${run.passed}/${pytestTotal(run)} pass`],
          cap !== null && ['Captured', cap.ok ? `${plural(cap.n_kept, 'call')}${cap.deterministic ? '' : ', not deterministic'}` : 'capture failed'],
          base !== null && ['Baseline', `${formatMass(base.g_per_call.mean, unit)} per call · ${plural(base.n_trials, 'trial')}`],
          node.status === 'skipped' && ['Skipped', skipText(f)],
          node.status === 'failed' && ['Failed', f.outcome?.reason ?? ''],
        ]}
      />
    </div>
  )
}
