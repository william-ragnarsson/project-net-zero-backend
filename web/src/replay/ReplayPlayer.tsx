// Plays a recorded event log through the same store a live run uses, drawn as the run tree.
// It owns the replay, the URL params (`speed`, `at`, `node`) and the replay's keys.
import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { useStore } from 'zustand'
import { ReplayControls } from '../components/ReplayControls'
import type { RunEvent } from '../gen/events'
import { createReplay, parseAt, parseSpeed, readableQuery } from '../sources/replay'
import { RunView } from '../tree/RunView'

/** Focus on one of these keeps Space and the arrows for itself. */
const OWN_KEYS = 'button, input, select, textarea, [role=slider], [role=radio], [role=checkbox], [data-face], [data-card-header]'
/** the controls bar's height on one line, until it is measured */
const BAR_H = 53

/** Mounted once per log: the speed and position in the URL only set where the replay opens. */
export function ReplayPlayer({ events }: { events: RunEvent[] }) {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const [replay] = useState(() =>
    createReplay(events, { speed: parseSpeed(params.get('speed')), at: parseAt(params.get('at')) }),
  )
  useEffect(() => {
    if (replay.autoplay) replay.driver.play()
    return replay.stop
  }, [replay])

  const model = useStore(replay.session, (s) => s.state)
  const epoch = useStore(replay.session, (s) => s.epoch)
  const status = useSyncExternalStore(replay.driver.subscribe, replay.driver.getStatus)

  // the bar wraps onto two lines on a phone, and the tree keeps what it follows clear of it
  const bar = useRef<HTMLDivElement>(null)
  const [barH, setBarH] = useState(BAR_H)
  useLayoutEffect(() => {
    const el = bar.current
    if (el === null) return
    const read = () => setBarH(Math.ceil(el.getBoundingClientRect().height) || BAR_H)
    read()
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(read)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // the speed and the pinned node land in the URL, so a shared link opens the same way
  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(params)
    if (value === null) next.delete(key)
    else next.set(key, value)
    void navigate({ search: next.size === 0 ? '' : readableQuery(next) }, { replace: true })
  }

  useEffect(() => {
    const { driver } = replay
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) return
      const t = e.target instanceof Element ? e.target : null
      if (t?.closest(OWN_KEYS) != null || (t instanceof HTMLElement && t.isContentEditable)) return
      if (e.key === ' ') {
        e.preventDefault()
        if (driver.getStatus().playing) driver.pause()
        else driver.play()
      } else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
        // a code body that scrolls across keeps the arrows
        if (t?.closest('[data-codebox]') != null) return
        e.preventDefault()
        driver.step(e.key === 'ArrowLeft' ? -1 : 1)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [replay])

  return (
    <>
      <ReplayControls
        ref={bar}
        driver={replay.driver}
        onSpeedChange={(s) => setParam('speed', s === 1 ? null : String(s))}
      />
      <RunView
        model={model}
        epoch={epoch}
        speed={status.speed}
        playing={status.playing}
        selectedId={params.get('node')}
        onSelect={(id) => setParam('node', id)}
        session={replay.session}
        topInset={barH}
      />
    </>
  )
}
