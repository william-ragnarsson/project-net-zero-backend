import { repoOf } from '../lib/runs'
import type { RunModel } from '../state/types'
import { CardHeader, CardShell, Facts, type CardProps } from './Card'
import type { PreNode } from './derive'
import { repoName } from './Pill'
import { plural, preText } from './text'

/** Rows of the ranked list before it scrolls inside the card. */
const TRIAGE_ROWS = 40

export function PreCard({ node, model, ...p }: CardProps & { node: PreNode; model: RunModel }) {
  const s = preText(node, model)
  const clone = model.steps.clone?.result ?? null
  const env = model.steps.env?.result ?? null
  const found = model.steps.discovery?.result ?? null
  const power = model.power?.power ?? null
  const selected = new Set(model.selection?.functionIds ?? model.preselected)
  const name = repoName(model)
  return (
    <CardShell framed={p.framed} pinned={p.pinned}>
      <CardHeader id={p.id} pinned={p.pinned} aria={`${name}, ${s.text}`} onSelect={p.onSelect}>
        <span className="truncate">{name}</span>
        <span className="ml-auto shrink-0 text-xs text-ink-3">{s.text}</span>
      </CardHeader>
      <Facts
        rows={[
          model.meta !== null && [
            'Repository',
            <>
              {repoOf(model.meta).full}
              {clone?.commit_sha != null && <span className="ml-2 font-mono text-ink-2">{clone.commit_sha.slice(0, 7)}</span>}
            </>,
          ],
          clone !== null &&
            clone.n_files !== null && ['Files', `${clone.n_files}${clone.n_py_files === null ? '' : `, ${clone.n_py_files} Python`}`],
          env !== null &&
            env.python_version !== null && ['Environment', `Python ${env.python_version} · ${plural(env.packages.length, 'package')}`],
          found !== null && ['Found', `${plural(found.n_functions, 'function')} · ${plural(found.n_test_files, 'test file')}`],
          power !== null && ['Machine', power.cpu_model],
        ]}
      />
      {model.triage.length > 0 && (
        <div className="border-t border-line">
          <ol aria-label="Ranked functions" className="max-h-[150px] overflow-auto py-1.5 text-xs" data-codebox>
            {model.triage.slice(0, TRIAGE_ROWS).map((t) => (
              <li key={t.function_id} className="flex min-w-0 items-baseline gap-3 px-3 py-0.5">
                <span className={`truncate ${selected.has(t.function_id) ? 'text-ink' : 'text-ink-3'}`}>{t.qualname}</span>
                <span className="ml-auto shrink-0 truncate text-ink-3">{t.skip_reason ?? t.reasons[0] ?? ''}</span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </CardShell>
  )
}
