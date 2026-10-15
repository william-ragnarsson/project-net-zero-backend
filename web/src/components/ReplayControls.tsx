import { useEffect, useRef, useSyncExternalStore, type Ref } from 'react'
import { formatClock } from '../lib/format'
import { SPEEDS, type ReplayDriver, type ReplayStatus, type Speed } from '../sources/replayDriver'
import { Button } from '../ui/Button'
import { Icon } from '../ui/Icon'
import { Segmented } from '../ui/Segmented'

const useReplayStatus = (driver: ReplayDriver): ReplayStatus => useSyncExternalStore(driver.subscribe, driver.getStatus)

const SPEED_OPTIONS = SPEEDS.map((s) => ({ value: String(s), label: `${s}×` }))
/** steps of the position slider */
const STEPS = 1000

interface Props {
  driver: ReplayDriver
  onSpeedChange?: (speed: Speed) => void
  ref?: Ref<HTMLDivElement>
}

/** The sticky bar above a replay: play, restart, speed, position and the clock. */
export function ReplayControls({ driver, onSpeedChange, ref }: Props) {
  const status = useReplayStatus(driver)
  const slider = useRef<HTMLInputElement>(null)
  const clock = useRef<HTMLSpanElement>(null)
  const dragging = useRef(false)
  const pendingSeek = useRef<{ frame: number; fraction: number } | null>(null)

  // the slider and the clock move every frame while playing; the status only changes when
  // events arrive, which can be over a second apart
  useEffect(() => {
    let frame = 0
    const paint = () => {
      const ms = driver.position()
      if (slider.current !== null && !dragging.current) {
        slider.current.value = String(status.durationMs > 0 ? Math.round((ms / status.durationMs) * STEPS) : 0)
      }
      if (clock.current !== null) clock.current.textContent = formatClock(ms)
      if (status.playing) frame = requestAnimationFrame(paint)
    }
    paint()
    return () => cancelAnimationFrame(frame)
  }, [driver, status])

  useEffect(() => () => cancelAnimationFrame(pendingSeek.current?.frame ?? 0), [])

  // a seek re-reduces the run from its first event, so a drag seeks at most once a frame
  const seek = (fraction: number) => {
    if (pendingSeek.current !== null) {
      pendingSeek.current.fraction = fraction
      return
    }
    const frame = requestAnimationFrame(() => {
      const target = pendingSeek.current?.fraction ?? fraction
      pendingSeek.current = null
      driver.seekFraction(target)
    })
    pendingSeek.current = { frame, fraction }
  }

  const setSpeed = (value: string) => {
    const speed = SPEEDS.find((s) => String(s) === value)
    if (speed === undefined) return
    driver.setSpeed(speed)
    onSpeedChange?.(speed)
  }

  const release = () => {
    dragging.current = false
  }

  return (
    <div
      ref={ref}
      className="sticky top-12 z-10 -mx-4 mb-6 flex flex-wrap items-center gap-3 border-b border-line bg-page px-4 py-2 sm:-mx-8 sm:px-8 lg:top-0 lg:-mx-10 lg:px-10">
      <Button
        variant="secondary"
        icon
        aria-label={status.playing ? 'Pause' : 'Play'}
        onClick={() => (status.playing ? driver.pause() : driver.play())}
      >
        <Icon name={status.playing ? 'pause' : 'play'} />
      </Button>
      <Button variant="quiet" icon aria-label="Restart" onClick={() => driver.seekStart()}>
        <Icon name="restart" />
      </Button>
      <Segmented label="Speed" size="sm" options={SPEED_OPTIONS} value={String(status.speed)} onChange={setSpeed} />
      <input
        ref={slider}
        type="range"
        aria-label="Position"
        aria-valuetext={`${formatClock(status.positionMs)} of ${formatClock(status.durationMs)}`}
        min={0}
        max={STEPS}
        step={1}
        defaultValue={0}
        onPointerDown={() => {
          dragging.current = true
        }}
        onPointerUp={release}
        onPointerCancel={release}
        onBlur={release}
        onChange={(e) => seek(Number(e.currentTarget.value) / STEPS)}
        className="h-1 min-w-[160px] flex-1 cursor-pointer accent-ink-2 max-sm:basis-full"
      />
      <span className="text-sm tabular-nums text-ink-2">
        <span ref={clock}>{formatClock(status.positionMs)}</span> / {formatClock(status.durationMs)}
      </span>
    </div>
  )
}
