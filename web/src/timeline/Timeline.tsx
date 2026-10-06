// The run as a timeline: a world of nodes and edges, moved by a camera that follows the
// tip until the viewer takes over.
import { motion, useTransform } from 'framer-motion'
import { useMemo, type PointerEvent } from 'react'
import type { RunModel } from '../state/types'
import { EdgeLayer } from './EdgeLayer'
import { LabelLayer } from './LabelLayer'
import { layout } from './layout'
import { Minimap } from './Minimap'
import { NodeLayer } from './NodeLayer'
import { useCamera, type CameraControls } from './useCamera'

interface Props {
  model: RunModel
  selectedId: string | null
  onSelect: (id: string) => void
}

export function Timeline({ model, selectedId, onSelect }: Props) {
  const drawing = useMemo(() => layout(model), [model])
  const camera = useCamera(drawing.width, drawing.tip?.x ?? 0)

  return (
    <div
      ref={camera.viewport}
      role="region"
      aria-label="Run timeline. Drag or use the arrow keys to pan, plus and minus to zoom."
      tabIndex={0}
      className="relative h-full touch-none select-none overflow-hidden bg-dark outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-neon/40 [&:active]:cursor-grabbing"
      style={{ cursor: 'grab' }}
      {...camera.handlers}
    >
      <motion.div
        className="absolute left-0 top-0"
        style={{ x: camera.x, y: camera.y, scale: camera.scale, transformOrigin: '0 0' }}
      >
        <EdgeLayer layout={drawing} />
        <LabelLayer labels={drawing.labels} />
        <NodeLayer
          nodes={drawing.nodes}
          caps={drawing.caps}
          selectedId={selectedId}
          onSelect={onSelect}
          onFocus={camera.reveal}
        />
      </motion.div>

      {drawing.nodes.length === 0 && (
        <p className="pointer-events-none absolute inset-0 flex items-center justify-center font-mono text-xs text-muted">
          waiting for the run to start
        </p>
      )}

      <Overlay camera={camera} />
      <Minimap layout={drawing} camera={camera} />
    </div>
  )
}

const control =
  'flex h-8 items-center justify-center bg-[#0c0c0c]/90 font-mono text-xs text-gray-400 backdrop-blur transition-colors hover:text-white'

function Overlay({ camera }: { camera: CameraControls }) {
  // the controls take their own presses: a press here is not the start of a pan
  const own = { onPointerDown: (e: PointerEvent) => e.stopPropagation() }
  return (
    <>
      <p className="pointer-events-none absolute bottom-4 left-6 hidden font-mono text-[10px] text-[#4f4f4f] md:block">
        drag to pan · ctrl/⌘ + scroll to zoom
      </p>
      <div
        {...own}
        className="absolute bottom-[88px] right-4 flex overflow-hidden sm:bottom-4 sm:right-[272px] rounded-lg border border-[#1f1f1f]"
        role="group"
        aria-label="Zoom"
      >
        <button
          type="button"
          aria-label="Zoom out"
          className={`${control} w-8`}
          onClick={() => camera.zoomBy(1 / 1.25)}
        >
          −
        </button>
        <ZoomReadout camera={camera} />
        <button type="button" aria-label="Zoom in" className={`${control} w-8`} onClick={() => camera.zoomBy(1.25)}>
          +
        </button>
      </div>
      {!camera.following && (
        <button
          type="button"
          {...own}
          onClick={camera.follow}
          className="absolute right-4 top-4 flex h-8 items-center gap-2 rounded-full border border-neon/40 bg-[#0c0c0c]/90 px-3.5 font-mono text-xs text-neon backdrop-blur transition-colors hover:bg-neon/10"
        >
          <span className="h-1.5 w-1.5 rounded-full bg-neon" />
          jump to live
        </button>
      )}
    </>
  )
}

function ZoomReadout({ camera }: { camera: CameraControls }) {
  const percent = useTransform(camera.scale, (s) => `${Math.round(s * 100)}%`)
  return <motion.span className={`${control} w-12 border-x border-[#1f1f1f] text-[11px]`}>{percent}</motion.span>
}
