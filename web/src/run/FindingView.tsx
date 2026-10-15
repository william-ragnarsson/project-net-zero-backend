import type { RunState } from '../gen/events'
import type { RunModel, RunSteps, StepStatus } from '../state/types'
import { Icon } from '../ui/Icon'

type RowKey = keyof Omit<RunSteps, 'artifacts'>

interface Row {
  key: RowKey
  label: string
  status: StepStatus | null
  detail: string | null
  /** closed by a cancel or a server stop rather than by a failure */
  stopped: boolean
}

/** The setup step each failing state belongs to, for a run that failed between two steps. */
const ROW_OF_STAGE: Partial<Record<RunState, RowKey>> = {
  created: 'powerCalibration',
  cloning: 'clone',
  installing: 'env',
  discovering: 'discovery',
  triaging: 'triage',
}

const when = <T,>(v: T | null | undefined, text: (v: T) => string): string | null =>
  v === null || v === undefined ? null : text(v)

function rows(m: RunModel): Row[] {
  const { powerCalibration, clone, env, discovery, triage } = m.steps
  const stopped = (error: { kind: string } | null | undefined) =>
    error?.kind === 'cancelled' || error?.kind === 'interrupted'
  const out: Row[] = []
  // only a run that measures the machine has this step
  if (powerCalibration !== null) {
    out.push({
      key: 'powerCalibration',
      label: 'Measure this machine’s power',
      status: powerCalibration.status,
      detail: when(powerCalibration.result?.p_core_w, (w) => `${w.toFixed(1)} W per core`),
      stopped: stopped(powerCalibration.result?.error),
    })
  }
  out.push(
    {
      key: 'clone',
      label: 'Clone the repository',
      status: clone?.status ?? null,
      detail: when(clone?.result?.n_py_files, (n) => `${n} Python files`),
      stopped: stopped(clone?.result?.error),
    },
    {
      key: 'env',
      label: 'Set up the environment',
      status: env?.status ?? null,
      detail: when(env?.result?.python_version, (v) => `Python ${v}`),
      stopped: stopped(env?.result?.error),
    },
    {
      key: 'discovery',
      label: 'Scan the code',
      status: discovery?.status ?? null,
      detail: when(discovery?.result?.n_functions, (n) => `${n} functions`),
      stopped: stopped(discovery?.result?.error),
    },
    {
      // before triage completes, the model's ranking is still discovery's heuristic one
      key: 'triage',
      label: 'Rank functions',
      status: triage?.status ?? null,
      detail: when(triage?.result?.items, (items) => `${items.length} ranked`),
      stopped: stopped(triage?.result?.error),
    },
  )
  return out
}

type Mark = 'ok' | 'running' | 'failed' | 'stopped' | null

/**
 * The setup checklist. `frozen` draws it for a run that ended here: the step it failed in
 * carries the fail mark (the backend can fail just after a step closed ok), nothing after it
 * is ticked, and a step left open has no mark.
 */
export function FindingView({ model, frozen = false }: { model: RunModel; frozen?: boolean }) {
  const list = rows(model)
  const t = model.terminal
  const failedKey = frozen && t?.type === 'run.failed' ? ROW_OF_STAGE[t.stage] : undefined
  const failedAt = failedKey === undefined ? -1 : list.findIndex((r) => r.key === failedKey)

  const markOf = (r: Row, i: number): Mark => {
    if (failedAt >= 0 && i > failedAt) return null
    if (i === failedAt) return 'failed'
    if (r.status === 'failed') return r.stopped ? 'stopped' : 'failed'
    if (r.status === 'running') return frozen ? null : 'running'
    return r.status === 'ok' ? 'ok' : null
  }

  return (
    <ul className="max-w-[560px]">
      {list.map((r, i) => {
        const mark = markOf(r, i)
        const reached = r.status !== null && !(failedAt >= 0 && i > failedAt)
        const tone = mark === 'running' || mark === 'failed' ? 'text-ink' : reached ? 'text-ink-2' : 'text-ink-3'
        return (
          <li key={r.key} className="flex h-9 items-center gap-3">
            <span className="flex w-4 shrink-0 justify-center">
              {mark === 'ok' && <Icon name="check" className="text-ink-2" />}
              {mark === 'running' && <span className="size-2 rounded-full border-[1.5px] border-ink-3" />}
              {mark === 'failed' && <Icon name="close" className="text-fail" />}
              {mark === 'stopped' && <Icon name="close" className="text-ink-3" />}
            </span>
            <span className={`min-w-0 truncate ${tone}`}>{r.label}</span>
            {reached && r.detail !== null && <span className="ml-auto shrink-0 text-sm text-ink-3">{r.detail}</span>}
          </li>
        )
      })}
    </ul>
  )
}
