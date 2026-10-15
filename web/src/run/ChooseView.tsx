import { useEffect, useState } from 'react'
import { isApiError } from '../api/client'
import { useSelect } from '../api/queries'
import { plainReason } from '../components/Co2'
import type { RunModel } from '../state/types'
import { Button } from '../ui/Button'
import { Checkbox } from '../ui/Checkbox'
import { Disclosure } from '../ui/Disclosure'
import { InlineNotice } from '../ui/InlineNotice'

/** SelectRequest.function_ids has max_length=20 (netzero/api/schemas.py), whatever the run's setting. */
export const MAX_SELECT = 20

function initialPick(m: RunModel): Set<string> {
  if (m.preselected.length > 0) return new Set(m.preselected)
  return new Set(m.triage.filter((t) => t.preselected).map((t) => t.function_id))
}

/**
 * The ranked functions to pick from. The view moves on only when run.selection.confirmed
 * arrives on the stream, never on the POST's answer, so a run the CLI selects for lands the
 * same way.
 */
export function ChooseView({
  runId,
  model: m,
  onShown,
  onSubmitted,
}: {
  runId: string
  model: RunModel
  onShown: () => void
  onSubmitted: () => void
}) {
  // set once: later events never undo the user's ticks
  const [picked, setPicked] = useState(() => initialPick(m))
  const [submitted, setSubmitted] = useState(false)
  const select = useSelect(runId)

  useEffect(onShown, [onShown])

  const eligible = m.triage.filter((t) => t.skip_reason === null)
  const ineligible = m.triage.filter((t) => t.skip_reason !== null)
  const n = eligible.filter((t) => picked.has(t.function_id)).length
  const max = Math.min(m.meta?.settings.max_functions ?? MAX_SELECT, MAX_SELECT)

  const toggle = (id: string, on: boolean) =>
    setPicked((prev) => {
      const next = new Set(prev)
      if (on) next.add(id)
      else next.delete(id)
      return next
    })

  const submit = () => {
    const ids = eligible.filter((t) => picked.has(t.function_id)).map((t) => t.function_id)
    select.mutate(ids, {
      onSuccess: () => {
        setSubmitted(true)
        onSubmitted()
      },
    })
  }

  const error = select.error
  const moved = isApiError(error, 'bad_state')

  return (
    <section>
      <h2 className="text-md font-medium text-ink">Choose functions</h2>
      <p className="mt-1 text-base text-ink-2">Recommended functions are already selected.</p>
      <ul className="mt-5 divide-y divide-line">
        {eligible.map((t) => (
          <li key={t.function_id}>
            <label
              className="flex cursor-pointer items-start gap-3 py-3"
              title={`${t.file}:${t.line} · score ${t.score.toFixed(2)}`}
            >
              <Checkbox
                className="mt-0.5"
                checked={picked.has(t.function_id)}
                onChange={(e) => toggle(t.function_id, e.currentTarget.checked)}
                disabled={submitted}
              />
              <div className="min-w-0">
                <div className="truncate text-ink">
                  {t.qualname}
                  <span className="ml-2 text-sm text-ink-3">{t.module}</span>
                </div>
                {t.reasons[0] !== undefined && (
                  <div className="line-clamp-2 text-sm text-ink-2">{plainReason(t.reasons[0])}</div>
                )}
              </div>
            </label>
          </li>
        ))}
      </ul>
      {ineligible.length > 0 && (
        <Disclosure className="mt-4" summary={`Not eligible (${ineligible.length})`}>
          <ul className="space-y-2">
            {ineligible.map((t) => (
              <li key={t.function_id} className="min-w-0">
                <div className="truncate text-ink-3">{t.qualname}</div>
                <div className="text-sm text-ink-3">{plainReason(t.skip_reason ?? '')}</div>
              </li>
            ))}
          </ul>
        </Disclosure>
      )}
      {error !== null && (
        <div className="mt-6">
          {moved ? (
            <InlineNotice>This run already moved on</InlineNotice>
          ) : (
            <InlineNotice tone="fail">{isApiError(error) ? error.detail : 'Could not start the run'}</InlineNotice>
          )}
        </div>
      )}
      <div className="sticky bottom-0 -mx-4 mt-6 flex items-center justify-between gap-3 border-t border-line bg-page px-4 py-3 sm:-mx-8 sm:px-8 lg:-mx-10 lg:px-10">
        <span className="text-sm text-ink-2">
          {n === 0 ? 'Pick at least one function' : n > max ? `Pick up to ${max} functions` : `${n} selected`}
        </span>
        <Button
          variant="primary"
          disabled={n === 0 || n > max || select.isPending || submitted}
          onClick={submit}
        >
          {submitted ? 'Starting' : `Start optimizing (${n})`}
        </Button>
      </div>
    </section>
  )
}
