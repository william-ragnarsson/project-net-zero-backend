// /replay?src=&speed=&at=&node= plays a recorded events.jsonl through the same store a live run
// uses, drawn as the timeline; `node` is the timeline node the viewer picked.
import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { useStore } from 'zustand'
import { PageHeader } from '../components/PageHeader'
import { ReplayControls } from '../components/ReplayControls'
import { ReplayList } from '../components/ReplayList'
import { formatInt } from '../lib/format'
import { useReplayFile, useReplayIndex } from '../sources/queries'
import { createReplay, parseAt, parseSpeed, readableQuery, sameOriginSrc, type ReplayAt } from '../sources/replay'
import type { Speed } from '../sources/replayDriver'
import type { ReplayFile } from '../sources/replayFile'
import { Timeline } from '../timeline/Timeline'

export function ReplayPage() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const raw = params.get('src')

  if (raw === null) {
    return (
      <>
        <PageHeader label="Replays" title="Recorded runs">
          Each one plays back event by event, as it streamed when it ran.
        </PageHeader>
        <section className="px-6 py-12 md:px-12">
          <ReplayList />
        </section>
      </>
    )
  }

  const src = sameOriginSrc(raw, window.location.origin)
  if (src === null) {
    return (
      <PageHeader label="Replay" title="This replay is not served here.">
        Replays load from this site only; <code className="font-mono text-sm text-gray-300">{raw}</code> points
        elsewhere.
      </PageHeader>
    )
  }

  // the speed and the picked node land in the URL, so a shared link opens the same way
  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params)
    next.set(key, value)
    void navigate({ search: readableQuery(next) }, { replace: true })
  }

  return (
    <ReplayLoader
      key={src}
      src={src}
      speed={parseSpeed(params.get('speed'))}
      at={parseAt(params.get('at'))}
      node={params.get('node')}
      onSpeedChange={(speed) => setParam('speed', String(speed))}
      onSelectNode={(id) => setParam('node', id)}
    />
  )
}

interface ReplayProps {
  src: string
  speed: Speed
  at: ReplayAt | null
  node: string | null
  onSpeedChange: (speed: Speed) => void
  onSelectNode: (id: string) => void
}

function ReplayLoader(props: ReplayProps) {
  const file = useReplayFile(props.src)
  if (file.isPending) return <PageHeader label="Replay" title="Loading the replay" />
  if (file.isError) {
    return (
      <PageHeader label="Replay" title="Could not load this replay.">
        <code className="font-mono text-sm text-fail">{file.error.message}</code>
      </PageHeader>
    )
  }
  if (file.data.events.length === 0) {
    return (
      <PageHeader label="Replay" title="This replay has no events.">
        <code className="font-mono text-sm text-gray-300">{props.src}</code>
      </PageHeader>
    )
  }
  return <ReplayView {...props} file={file.data} />
}

/** Mounted once per loaded file: the speed and position from the URL only set where it opens. */
function ReplayView({ src, speed, at, node, onSpeedChange, onSelectNode, file }: ReplayProps & { file: ReplayFile }) {
  const [replay] = useState(() => createReplay(file.events, { speed, at }))
  useEffect(() => {
    if (replay.autoplay) replay.driver.play()
    return replay.stop
  }, [replay])

  const model = useStore(replay.session, (s) => s.state)
  const ref = useReplayIndex().data?.find((r) => r.src === src)

  return (
    <div className="flex h-[calc(100dvh-4rem-1px)] min-h-[34rem] flex-col">
      <header className="flex flex-wrap items-end justify-between gap-x-8 gap-y-1 border-b border-dark-border px-6 pb-4 pt-6 md:px-12">
        <div className="min-w-0">
          <p className="label">Replay</p>
          <h1 className="mt-2 truncate text-2xl font-light tracking-[-0.02em] text-white">{ref?.title ?? src}</h1>
        </div>
        <p className="pb-1 font-mono text-xs text-muted">
          {model.runId ?? src} · {formatInt(file.events.length)} events
          {file.skipped > 0 && <span className="text-caution"> · {formatInt(file.skipped)} lines skipped</span>}
        </p>
      </header>
      <ReplayControls driver={replay.driver} onSpeedChange={onSpeedChange} />
      <div className="min-h-0 flex-1">
        <Timeline model={model} selectedId={node} onSelect={onSelectNode} />
      </div>
    </div>
  )
}
