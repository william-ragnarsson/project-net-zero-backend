import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { eventsUrl, isApiError } from '../api/client'
import { useArtifactHref, useCancel } from '../api/queries'
import { SHOWCASE } from '../showcase/config'
import type { RunModel } from '../state/types'
import { Button, buttonClass } from '../ui/Button'
import { Menu, MenuItem } from '../ui/Menu'
import type { RunViewKind } from './view'

export interface RunNotice {
  tone: 'neutral' | 'fail'
  text: string
}

/** How long "Confirm cancel" waits for the second click. */
const CONFIRM_MS = 4000

/** Two clicks, then it stays on "Cancelling" until the terminal event changes the view. */
function CancelButton({
  runId,
  model,
  onNotice,
}: {
  runId: string
  model: RunModel
  onNotice: (n: RunNotice | null) => void
}) {
  const [stage, setStage] = useState<'idle' | 'confirm' | 'cancelling'>('idle')
  const cancel = useCancel(runId)

  useEffect(() => {
    if (stage !== 'confirm') return
    const t = window.setTimeout(() => setStage('idle'), CONFIRM_MS)
    return () => window.clearTimeout(t)
  }, [stage])

  const click = () => {
    if (stage === 'idle') {
      onNotice(null)
      setStage('confirm')
      return
    }
    if (stage !== 'confirm') return
    setStage('cancelling')
    cancel.mutate(undefined, {
      onError: (e) => {
        setStage('idle')
        if (isApiError(e, 'bad_state')) {
          // a run another process drives; once the model is terminal the answer is moot
          if (model.terminal === null) onNotice({ tone: 'neutral', text: e.detail })
          return
        }
        onNotice({ tone: 'fail', text: isApiError(e) ? e.detail : 'Could not cancel the run' })
      },
    })
  }

  return (
    <Button onClick={click} disabled={stage === 'cancelling'}>
      {stage === 'idle' ? 'Cancel run' : stage === 'confirm' ? 'Confirm cancel' : 'Cancelling'}
    </Button>
  )
}

/** "Copied" shows in the item itself for a moment before the menu closes. */
function CopyIdItem({ runId }: { runId: string }) {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    void navigator.clipboard?.writeText(runId).then(
      () => setCopied(true),
      () => {},
    )
  }
  return (
    <MenuItem onSelect={copy} closeDelayMs={1500}>
      {copied ? 'Copied' : 'Copy run id'}
    </MenuItem>
  )
}

function runAgainHref(m: RunModel): string | null {
  const source = m.meta?.source
  if (source?.kind !== 'github' || source.url === null) return null
  const q = new URLSearchParams({ repo: source.url })
  if (source.ref !== null && source.ref !== '') q.set('ref', source.ref)
  return `/new?${q.toString()}`
}

/** The page header's actions for each view of a run. */
export function RunActions({
  view,
  runId,
  model,
  onNotice,
}: {
  view: RunViewKind
  runId: string
  model: RunModel
  onNotice: (n: RunNotice | null) => void
}) {
  const href = useArtifactHref()
  if (view === 'finding' || view === 'choose' || view === 'optimizing') {
    return <CancelButton runId={runId} model={model} onNotice={onNotice} />
  }
  const events = (
    <MenuItem href={eventsUrl(runId)} download={`${runId}.events.jsonl`}>
      Download events
    </MenuItem>
  )
  if (view === 'done') {
    const artifacts = model.steps.artifacts?.result ?? null
    const patch = href(artifacts?.patch?.url)
    const zip = href(artifacts?.zip?.url)
    return (
      <>
        {patch !== undefined && (
          <a href={patch} download className={buttonClass('primary')}>
            Download patch
          </a>
        )}
        <Menu label="More actions">
          {zip !== undefined && (
            <MenuItem href={zip} download="">
              Download zip
            </MenuItem>
          )}
          {events}
          <CopyIdItem runId={runId} />
        </Menu>
      </>
    )
  }
  if (view === 'ended') {
    // the showcase can't start runs
    const again = SHOWCASE ? null : runAgainHref(model)
    return (
      <>
        {again !== null && (
          <Link to={again} className={buttonClass('secondary')}>
            Run again
          </Link>
        )}
        <Menu label="More actions">
          {events}
          <CopyIdItem runId={runId} />
        </Menu>
      </>
    )
  }
  return null
}
