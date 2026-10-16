import { useNavigate, useSearchParams } from 'react-router'
import { useRunEvents } from '../api/queries'
import { ReplayPlayer } from '../replay/ReplayPlayer'
import { readableQuery } from '../sources/replay'
import type { RunSessionStore } from '../state/store'
import type { RunModel } from '../state/types'
import { RunView } from '../tree/RunView'
import { Button } from '../ui/Button'
import { Delayed } from '../ui/Delayed'
import { EmptyState } from '../ui/EmptyState'
import { InlineNotice } from '../ui/InlineNotice'
import { TabsContent, TabsList, TabsRoot, TabsTrigger } from '../ui/Tabs'
import { ChangesTab } from './ChangesTab'
import { LogTab } from './LogTab'

type RunTab = 'tree' | 'changes' | 'log'

const parseTab = (v: string | null): RunTab => (v === 'changes' || v === 'log' ? v : 'tree')

/** Sets or removes query params in place, keeping the others. */
function useParamSetter() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  return (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params)
    for (const [key, value] of Object.entries(changes)) {
      if (value === null) next.delete(key)
      else next.set(key, value)
    }
    void navigate({ search: next.size === 0 ? '' : readableQuery(next) }, { replace: true })
  }
}

/** A finished run played again from its own log, through the replay page's player. */
function RunReplay({ runId }: { runId: string }) {
  const events = useRunEvents(runId, true)
  if (events.isPending) {
    return (
      <Delayed>
        <p className="text-base text-ink-3">Loading</p>
      </Delayed>
    )
  }
  if (events.isError) {
    return (
      <InlineNotice
        tone="fail"
        action={
          <Button size="sm" onClick={() => void events.refetch()}>
            Retry
          </Button>
        }
      >
        Can’t load this run’s events
      </InlineNotice>
    )
  }
  if (events.data.length === 0) return <EmptyState title="This run has no events" />
  return <ReplayPlayer events={events.data} />
}

/** Tree, Changes and Log under a running or finished run. The tab lives in `?tab=`. */
export function RunTabs({
  runId,
  model,
  epoch,
  session,
  ended,
}: {
  runId: string
  model: RunModel
  epoch: number
  session: RunSessionStore
  ended: boolean
}) {
  const [params] = useSearchParams()
  const setParams = useParamSetter()
  const tab = parseTab(params.get('tab'))
  const replaying = ended && params.get('replay') === '1'

  return (
    <TabsRoot value={tab} onValueChange={(v) => setParams({ tab: v === 'tree' ? null : v })}>
      <TabsList aria-label="Run details">
        <TabsTrigger value="tree">Tree</TabsTrigger>
        <TabsTrigger value="changes">Changes</TabsTrigger>
        <TabsTrigger value="log">Log</TabsTrigger>
      </TabsList>
      <TabsContent value="tree">
        {replaying ? (
          <>
            <div className="mb-3 flex justify-end">
              <Button variant="quiet" size="sm" onClick={() => setParams({ replay: null, speed: null, at: null })}>
                Back to the result
              </Button>
            </div>
            <RunReplay runId={runId} />
          </>
        ) : (
          <>
            {ended && (
              <div className="mb-3 flex justify-end">
                <Button variant="quiet" size="sm" onClick={() => setParams({ replay: '1' })}>
                  Replay this run
                </Button>
              </div>
            )}
            <RunView
              model={model}
              epoch={epoch}
              speed={1}
              playing={!ended}
              selectedId={params.get('node')}
              onSelect={(id) => setParams({ node: id })}
              session={session}
            />
          </>
        )}
      </TabsContent>
      <TabsContent value="changes">
        <ChangesTab model={model} />
      </TabsContent>
      <TabsContent value="log">
        <LogTab model={model} />
      </TabsContent>
    </TabsRoot>
  )
}
