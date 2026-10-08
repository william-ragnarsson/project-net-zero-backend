// Every function a project's runs looked at, against every run: one square per (function, run),
// colored by what happened to it. A row opens the per-run results and the diff that was kept.
import { Fragment, useState } from 'react'
import { Co2 } from '../components/Co2'
import type { FunctionHistory, FunctionPoint, HistoryRun } from '../gen/events'
import { formatDate, formatGrams, formatPct, NONE } from '../lib/format'
import { useFunctionDiff } from '../sources/history'
import { NEON, OUTCOME } from '../timeline/theme'
import { shortSha } from './labels'

const FAILED = new Set(['all_rejected', 'reverted', 'failed'])

function pointTitle(point: FunctionPoint): string {
  const outcome = point.outcome === null ? 'unfinished' : OUTCOME[point.outcome].text
  const delta = point.outcome === 'accepted' ? `: ${formatPct(point.delta_pct)} (rewrite ${point.winner})` : ''
  return `${formatDate(point.created_ts)}, ${outcome}${delta}`
}

function Cell({ point }: { point: FunctionPoint | undefined }) {
  if (point === undefined) {
    return <span title="not in this run" className="mx-auto block size-1 rounded-full bg-[#2a2a2a]" />
  }
  const outcome = point.outcome
  const style =
    outcome === 'accepted'
      ? { backgroundColor: NEON }
      : outcome !== null && FAILED.has(outcome)
        ? { border: `1px solid ${OUTCOME[outcome].color}`, backgroundColor: '#ff5f5722' }
        : outcome === 'no_significant_win'
          ? { border: '1px solid #5a5a5a' }
          : { border: '1px dashed #3a3a3a' }
  return <span title={pointTitle(point)} className="mx-auto block size-4 rounded-[3px]" style={style} />
}

export function MatrixLegend() {
  const swatch = (style: object, text: string) => (
    <span className="flex items-center gap-2">
      <span className="block size-3 rounded-[2px]" style={style} />
      {text}
    </span>
  )
  return (
    <div className="flex flex-wrap gap-x-5 gap-y-2 font-mono text-[11px] text-muted">
      {swatch({ backgroundColor: NEON }, 'rewrite kept')}
      {swatch({ border: '1px solid #5a5a5a' }, 'no significant win')}
      {swatch({ border: '1px solid #ff5f57', backgroundColor: '#ff5f5722' }, 'every rewrite rejected')}
      {swatch({ border: '1px dashed #3a3a3a' }, 'skipped')}
    </div>
  )
}

/** The measured change with its 95% interval, on a track from −100% (left) to 0 (right). */
function DeltaBar({ point }: { point: FunctionPoint }) {
  if (point.delta_pct === null) return <span className="font-mono text-xs text-muted">{NONE}</span>
  const reach = (pct: number) => Math.min(100, Math.max(0, -pct))
  const kept = point.outcome === 'accepted'
  return (
    <span className="flex items-center gap-3">
      <span className="relative block h-2 w-28 shrink-0 bg-[#161616]">
        <span
          className="absolute inset-y-0 right-0"
          style={{ width: `${reach(point.delta_pct)}%`, backgroundColor: kept ? NEON : '#4a4a4a' }}
        />
        {point.ci_lo !== null && point.ci_hi !== null && (
          <span
            className="absolute top-1/2 h-px -translate-y-1/2 bg-white"
            style={{ right: `${reach(point.ci_hi)}%`, width: `${Math.max(0.5, reach(point.ci_lo) - reach(point.ci_hi))}%` }}
          />
        )}
      </span>
      <span className={`font-mono text-xs tabular-nums ${kept ? 'text-neon' : 'text-gray-400'}`}>
        {formatPct(point.delta_pct)}
      </span>
    </span>
  )
}

function DiffView({ runId, functionId }: { runId: string; functionId: string }) {
  const diff = useFunctionDiff(runId, functionId, true)
  if (diff.isPending) return <p className="label py-3">Loading the diff</p>
  if (diff.isError) return <p className="py-3 text-xs text-fail">{diff.error.message}</p>
  if (diff.data.diff === null) return <p className="py-3 text-xs text-muted">This run stored no diff.</p>
  return (
    <pre className="mt-2 max-h-96 overflow-auto border border-dark-border bg-[#0d0d0d] p-4 font-mono text-xs leading-relaxed">
      {diff.data.diff.split('\n').map((line, i) => {
        const color = line.startsWith('+++') || line.startsWith('---') || line.startsWith('diff ')
          ? 'text-[#5a5a5a]'
          : line.startsWith('+')
            ? 'text-neon'
            : line.startsWith('-')
              ? 'text-fail'
              : line.startsWith('@@')
                ? 'text-muted'
                : 'text-gray-400'
        return (
          <span key={i} className={`block ${color}`}>
            {line === '' ? ' ' : line}
          </span>
        )
      })}
    </pre>
  )
}

function PointRow({ point, functionId }: { point: FunctionPoint; functionId: string }) {
  const [showDiff, setShowDiff] = useState(false)
  const outcome = point.outcome === null ? null : OUTCOME[point.outcome]
  const hasDiff = point.outcome === 'accepted' || point.outcome === 'reverted'
  return (
    <li className="border-b border-dark-border/70 py-3 last:border-b-0">
      <div className="grid grid-cols-[4.5rem_minmax(0,1fr)] items-center gap-x-4 gap-y-2 md:grid-cols-[4.5rem_4.5rem_9rem_12rem_minmax(0,1fr)_auto]">
        <span className="font-mono text-xs text-gray-300">{formatDate(point.created_ts)}</span>
        <span className="hidden font-mono text-xs text-muted md:block">{shortSha(point.base_sha) ?? NONE}</span>
        <span className="text-xs" style={{ color: outcome?.color ?? '#6b6b6b' }}>
          {outcome?.text ?? 'unfinished'}
          {point.winner !== null && <span className="text-muted"> · {point.winner}</span>}
        </span>
        <DeltaBar point={point} />
        <span className="col-span-2 truncate text-xs text-muted md:col-span-1" title={point.reason}>
          {point.g_saved_per_1m_calls !== null && point.outcome === 'accepted' ? (
            <>
              saves {formatGrams(point.g_saved_per_1m_calls)} <Co2 /> per 1M calls
            </>
          ) : (
            point.reason
          )}
        </span>
        {hasDiff ? (
          <button
            type="button"
            onClick={() => setShowDiff((v) => !v)}
            aria-expanded={showDiff}
            className="justify-self-start font-mono text-xs text-gray-400 transition-colors hover:text-neon md:justify-self-end"
          >
            {showDiff ? 'hide diff' : 'diff'}
          </button>
        ) : (
          <span className="hidden md:block" />
        )}
      </div>
      {showDiff && <DiffView runId={point.run_id} functionId={functionId} />}
    </li>
  )
}

export function FunctionsMatrix({ functions, runs }: { functions: readonly FunctionHistory[]; runs: readonly HistoryRun[] }) {
  const chrono = [...runs].reverse()
  const [open, setOpen] = useState<string | null>(null)
  if (functions.length === 0) return <p className="text-sm text-muted">No function results stored yet.</p>

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="text-left">
            <th className="label pb-3 pr-6 font-normal">Function</th>
            <th className="label whitespace-nowrap pb-3 font-normal" colSpan={chrono.length}>
              Runs, oldest first
            </th>
            <th className="label whitespace-nowrap pb-3 pl-6 text-right font-normal">Best kept</th>
          </tr>
        </thead>
        <tbody>
          {functions.map((fn) => {
            const byRun = new Map(fn.points.map((p) => [p.run_id, p]))
            const expanded = open === fn.function_id
            const toggle = () => setOpen(expanded ? null : fn.function_id)
            return (
              <Fragment key={fn.function_id}>
                <tr
                  onClick={toggle}
                  className={`cursor-pointer border-t border-dark-border transition-colors hover:bg-dark-card ${expanded ? 'bg-dark-card' : ''}`}
                >
                  <td className="py-3 pl-2 pr-6">
                    <button type="button" aria-expanded={expanded} className="block min-w-48 text-left outline-none">
                      <span className="font-mono text-[13px] text-white">{fn.qualname}</span>
                      <span className="mt-0.5 block text-xs text-muted">{fn.file ?? fn.module}</span>
                    </button>
                  </td>
                  {chrono.map((run) => (
                    <td key={run.run_id} className="w-[22px] px-[3px] py-3">
                      <Cell point={byRun.get(run.run_id)} />
                    </td>
                  ))}
                  <td className="whitespace-nowrap py-3 pl-6 pr-2 text-right font-mono text-xs tabular-nums">
                    {fn.best_delta_pct === null ? (
                      <span className="text-[#5a5a5a]">{NONE}</span>
                    ) : (
                      <span className="text-neon">{formatPct(fn.best_delta_pct)}</span>
                    )}
                  </td>
                </tr>
                {expanded && (
                  <tr className="bg-dark-card">
                    <td colSpan={chrono.length + 2} className="px-2 pb-4">
                      <ul className="border-t border-dark-border">
                        {[...fn.points].reverse().map((point) => (
                          <PointRow key={point.run_id} point={point} functionId={fn.function_id} />
                        ))}
                      </ul>
                    </td>
                  </tr>
                )}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
