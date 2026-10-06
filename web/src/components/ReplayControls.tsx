import { useEffect, useRef, useSyncExternalStore, type KeyboardEvent, type PointerEvent } from 'react'
import { formatClock, formatInt } from '../lib/format'
import { SPEEDS, type ReplayDriver, type ReplayStatus, type Speed } from '../sources/replayDriver'

const useReplayStatus = (driver: ReplayDriver): ReplayStatus => useSyncExternalStore(driver.subscribe, driver.getStatus)

interface Props {
  driver: ReplayDriver
  onSpeedChange?: (speed: Speed) => void
}

export function ReplayControls({ driver, onSpeedChange }: Props) {
  const status = useReplayStatus(driver)
  const ended = status.cursor >= status.total && !status.playing

  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-3 border-b border-dark-border px-6 py-4 md:px-12">
      <button
        type="button"
        onClick={() => (status.playing ? driver.pause() : driver.play())}
        aria-label={status.playing ? 'Pause' : ended ? 'Replay from the start' : 'Play'}
        className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-neon text-dark transition-colors hover:bg-neon-dim"
      >
        {status.playing ? <PauseIcon /> : ended ? <RestartIcon /> : <PlayIcon />}
      </button>

      <Scrubber driver={driver} status={status} />

      <div className="flex items-center gap-4">
        <div role="group" aria-label="Playback speed" className="flex gap-px border border-dark-border bg-dark-border">
          {SPEEDS.map((speed) => (
            <button
              key={speed}
              type="button"
              aria-pressed={status.speed === speed}
              onClick={() => {
                driver.setSpeed(speed)
                onSpeedChange?.(speed)
              }}
              className={`h-8 px-2.5 font-mono text-xs transition-colors ${
                status.speed === speed ? 'bg-neon/10 text-neon' : 'bg-dark text-gray-400 hover:text-white'
              }`}
            >
              {speed}×
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={() => driver.seekEnd()}
          disabled={status.cursor >= status.total}
          className="text-sm text-gray-400 transition-colors hover:text-white disabled:text-gray-700"
        >
          End
        </button>
      </div>
    </div>
  )
}

/**
 * A slider over the replay's timeline. The fill and clock move every frame while playing;
 * the status only changes when events arrive, which can be over a second apart.
 */
function Scrubber({ driver, status }: { driver: ReplayDriver; status: ReplayStatus }) {
  const fill = useRef<HTMLDivElement>(null)
  const clock = useRef<HTMLSpanElement>(null)
  const pendingSeek = useRef<{ frame: number; fraction: number } | null>(null)

  useEffect(() => {
    let frame = 0
    const paint = () => {
      const ms = driver.position()
      if (fill.current) fill.current.style.transform = `scaleX(${status.durationMs > 0 ? ms / status.durationMs : 0})`
      if (clock.current) clock.current.textContent = formatClock(ms)
      if (status.playing) frame = requestAnimationFrame(paint)
    }
    paint()
    return () => cancelAnimationFrame(frame)
  }, [driver, status])

  useEffect(() => () => cancelAnimationFrame(pendingSeek.current?.frame ?? 0), [])

  // a seek re-reduces the run from its first event, so a drag seeks at most once a frame
  const seekToPointer = (e: PointerEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const fraction = rect.width > 0 ? (e.clientX - rect.left) / rect.width : 0
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

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.shiftKey ? 10 : 1
    const action: Record<string, () => void> = {
      ArrowLeft: () => driver.step(-step),
      ArrowRight: () => driver.step(step),
      Home: () => driver.seekStart(),
      End: () => driver.seekEnd(),
    }
    const run = action[e.key]
    if (run === undefined) return
    e.preventDefault()
    run()
  }

  return (
    <div className="flex min-w-[14rem] flex-1 items-center gap-4">
      <div
        role="slider"
        tabIndex={0}
        aria-label="Replay position"
        aria-valuemin={0}
        aria-valuemax={status.total}
        aria-valuenow={status.cursor}
        aria-valuetext={`event ${status.cursor} of ${status.total}`}
        onKeyDown={onKeyDown}
        onPointerDown={(e) => {
          e.currentTarget.setPointerCapture(e.pointerId)
          seekToPointer(e)
        }}
        onPointerMove={(e) => {
          if (e.currentTarget.hasPointerCapture(e.pointerId)) seekToPointer(e)
        }}
        className="group flex h-6 flex-1 cursor-pointer touch-none items-center outline-none"
      >
        <div className="relative h-0.5 w-full bg-dark-border group-focus-visible:bg-gray-600">
          <div ref={fill} className="absolute inset-0 origin-left bg-neon" style={{ transform: 'scaleX(0)' }} />
        </div>
      </div>
      <p className="shrink-0 font-mono text-xs tabular-nums text-gray-400">
        <span ref={clock} className="text-white">
          {formatClock(status.positionMs)}
        </span>{' '}
        / {formatClock(status.durationMs)}
        <span className="ml-3 text-muted">
          seq {formatInt(status.seq)}
        </span>
      </p>
    </div>
  )
}

const icon = 'h-4 w-4'

function PlayIcon() {
  return (
    <svg viewBox="0 0 16 16" className={icon} aria-hidden="true">
      <path d="M4.5 2.8v10.4L13 8z" fill="currentColor" />
    </svg>
  )
}

function PauseIcon() {
  return (
    <svg viewBox="0 0 16 16" className={icon} aria-hidden="true">
      <path d="M4 3h3v10H4zM9 3h3v10H9z" fill="currentColor" />
    </svg>
  )
}

function RestartIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" className={icon} aria-hidden="true">
      <path
        d="M3 8a5 5 0 1 0 1.5-3.57M3 2.5v2.75h2.75"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}
