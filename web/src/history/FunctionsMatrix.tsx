// Every function a project's runs looked at, against every run: one square per (function, run),
// coloured by what happened to it. A row opens the per-run results and the diff that was kept.
// It sits in a card, under the card's header.
import { Fragment, useState } from 'react'
import { Co2 } from '../components/Co2'
import type { FunctionHistory, FunctionOutcome, FunctionPoint, HistoryRun } from '../gen/events'
import { formatDate, formatGrams, formatPct, NONE } from '../lib/format'
import { useFunctionDiff } from '../sources/history'
import type { BadgeTone } from '../ui/Badge'
import { Badge } from '../ui/Badge'
import { Icon } from '../ui/Icon'
import { OUTCOME, shortSha } from './labels'

const SWATCH = {
  kept: 'bg-accent',
  none: 'border border-control-strong bg-surface',
  rejected: 'border border-fail bg-fail-tint',
  skipped: 'border border-dashed border-control-strong bg-surface',
} as const

function swatchOf(outcome: FunctionOutcome | null): string {
  if (outcome === 'accepted') return SWATCH.kept
  if (outcome === 'no_significant_win') return SWATCH.none
  const tone: BadgeTone | undefined = outcome === null ? undefined : OUTCOME[outcome].tone
  return tone === 'fail' ? SWATCH.rejected : SWATCH.skipped
}

function pointTitle(point: FunctionPoint): string {
  const outcome = point.outcome === null ? 'Unfinished' : OUTCOME[point.outcome].text
  const delta = point.outcome === 'accepted' ? `: ${formatPct(point.delta_pct)} (rewrite ${point.winner})` : ''
  return `${formatDate(point.created_ts)}, ${outcome.toLowerCase()}${delta}`
}

function Cell({ point }: { point: FunctionPoint | undefined }) {
  if (point === undefined) {
    return <span title="Not in this run" className="mx-auto block size-1 rounded-full bg-control" />
  }
  return <span title={pointTitle(point)} className={`mx-auto block size-4 rounded-[4px] ${swatchOf(point.outcome)}`} />
}

export function MatrixLegend() {
  const swatch = (cls: string, text: string) => (
    <span className="flex items-center gap-2">
      <span aria-hidden="true" className={`block size-3 rounded-[3px] ${cls}`} />
      {text}
    </span>
  )
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-2 text-xs text-ink-3">
      {swatch(SWATCH.kept, 'Rewrite kept')}
      {swatch(SWATCH.none, 'No significant win')}
      {swatch(SWATCH.rejected, 'Rejected or failed')}
      {swatch(SWATCH.skipped, 'Skipped')}
    </div>
  )
}

/** The measured change with its 95% interval, on a track from −100% (left) to 0 (right). */
function DeltaBar({ point }: { point: FunctionPoint }) {
  if (point.delta_pct === null) return <span className="text-sm text-ink-3">{NONE}</span>
  const reach = (pct: number) => Math.min(100, Math.max(0, -pct))
  const kept = point.outcome === 'accepted'
  return (
    <span className="flex items-center gap-3">
      <span className="relative block h-2 w-28 shrink-0 overflow-hidden rounded-full bg-hover">
        <span
          className={`absolute inset-y-0 right-0 ${kept ? 'bg-accent' : 'bg-control-strong'}`}
          style={{ width: `${reach(point.delta_pct)}%` }}
        />
        {point.ci_lo !== null && point.ci_hi !== null && (
          <span
            className="absolute top-1/2 h-px -translate-y-1/2 bg-ink"
            style={{ right: `${reach(point.ci_hi)}%`, width: `${Math.max(0.5, reach(point.ci_lo) - reach(point.ci_hi))}%` }}
          />
        )}
      </span>
      <span className={`text-sm tabular-nums ${kept ? 'font-medium text-accent' : 'text-ink-2'}`}>
        {formatPct(point.delta_pct)}
      </span>
    </span>
  )
}

function lineClass(line: string): string {
  if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('diff ')) return 'text-ink-3'
  if (line.startsWith('+')) return 'bg-accent-tint text-accent'
  if (line.startsWith('-')) return 'bg-fail-tint text-fail'
  if (line.startsWith('@@')) return 'text-ink-3'
  return 'text-ink-2'
}

function DiffView({ runId, functionId }: { runId: string; functionId: string }) {
  const diff = useFunctionDiff(runId, functionId, true)
  if (diff.isPending) return <p className="py-3 text-sm text-ink-3">Loading the diff</p>
  if (diff.isError) return <p className="py-3 text-sm text-fail">{diff.error.message}</p>
  if (diff.data.diff === null) return <p className="py-3 text-sm text-ink-3">This run stored no diff.</p>
  return (
    <pre className="mt-2 max-h-96 overflow-auto rounded-lg border border-line bg-surface py-3 font-mono text-code">
      {diff.data.diff.split('\n').map((line, i) => (
        <span key={i} className={`block px-4 ${lineClass(line)}`}>
          {line === '' ? ' ' : line}
        </span>
      ))}
    </pre>
  )
}

function PointRow({ point, functionId }: { point: FunctionPoint; functionId: string }) {
  const [showDiff, setShowDiff] = useState(false)
  const outcome = point.outcome === null ? null : OUTCOME[point.outcome]
  const hasDiff = point.outcome === 'accepted' || point.outcome === 'reverted'
  return (
    <li className="border-b border-line py-3 last:border-b-0">
      <div className="grid grid-cols-[4.5rem_minmax(0,1fr)] items-center gap-x-4 gap-y-2 md:grid-cols-[4.5rem_4.5rem_10rem_12rem_minmax(0,1fr)_auto]">
        <span className="text-sm tabular-nums text-ink-2">{formatDate(point.created_ts)}</span>
        <span className="hidden font-mono text-xs text-ink-3 md:block">{shortSha(point.base_sha) ?? NONE}</span>
        <span className="flex items-center gap-1.5">
          <Badge tone={outcome?.tone ?? 'neutral'}>{outcome?.text ?? 'Unfinished'}</Badge>
          {point.winner !== null && <span className="text-xs text-ink-3">{point.winner}</span>}
        </span>
        <DeltaBar point={point} />
        <span className="col-span-2 truncate text-sm text-ink-3 md:col-span-1" title={point.reason}>
          {point.g_saved_per_1m_calls !== null && point.outcome === 'accepted' ? (
            <>
              Saves {formatGrams(point.g_saved_per_1m_calls)} <Co2 /> per 1M calls
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
            className="justify-self-start text-sm text-ink-2 underline-offset-2 transition-colors hover:text-ink hover:underline md:justify-self-end"
          >
            {showDiff ? 'Hide diff' : 'Diff'}
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
  if (functions.length === 0) {
    return <p className="border-t border-line px-5 py-4 text-sm text-ink-3">No function results stored yet.</p>
  }

  return (
    <div className="overflow-x-auto border-t border-line">
      <table className="w-full border-collapse text-base">
        <thead>
          <tr className="text-left">
            <th className="h-9 border-b border-line pl-5 pr-6 text-sm font-normal text-ink-3">Function</th>
            <th className="h-9 whitespace-nowrap border-b border-line text-sm font-normal text-ink-3" colSpan={chrono.length}>
              Runs, oldest first
            </th>
            <th className="h-9 whitespace-nowrap border-b border-line pl-6 pr-5 text-right text-sm font-normal text-ink-3">
              Best kept
            </th>
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
                  className={`cursor-pointer border-b border-line transition-colors duration-150 hover:bg-hover ${
                    expanded ? 'bg-hover' : ''
                  }`}
                >
                  <td className="py-3 pl-5 pr-6">
                    <button type="button" aria-expanded={expanded} className="flex min-w-48 items-start gap-2 text-left">
                      <Icon
                        name="chevron-right"
                        className={`mt-0.5 shrink-0 text-ink-3 transition-transform duration-150 ${expanded ? 'rotate-90' : ''}`}
                      />
                      <span>
                        <span className="font-mono text-code text-ink">{fn.qualname}</span>
                        <span className="mt-0.5 block text-xs text-ink-3">{fn.file ?? fn.module}</span>
                      </span>
                    </button>
                  </td>
                  {chrono.map((run) => (
                    <td key={run.run_id} className="w-[22px] px-[3px] py-3">
                      <Cell point={byRun.get(run.run_id)} />
                    </td>
                  ))}
                  <td className="whitespace-nowrap py-3 pl-6 pr-5 text-right text-sm tabular-nums">
                    {fn.best_delta_pct === null ? (
                      <span className="text-ink-3">{NONE}</span>
                    ) : (
                      <span className="font-medium text-accent">{formatPct(fn.best_delta_pct)}</span>
                    )}
                  </td>
                </tr>
                {expanded && (
                  <tr className="border-b border-line bg-page">
                    <td colSpan={chrono.length + 2} className="px-5 py-1">
                      <ul>
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
