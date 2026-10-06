// The whole run in 240×64, with the viewport as a rectangle; a click or drag moves there.
import { useEffect, useRef, type PointerEvent } from 'react'
import { fromMap, toMap, visibleRect } from './camera'
import { edgePath } from './geometry'
import type { NodeStatus, TimelineLayout } from './layout'
import { CAUTION, FAIL, NEON } from './theme'
import type { CameraControls } from './useCamera'

const MAP = { width: 240, height: 64 }

const DOT: Record<NodeStatus | 'lit', string> = {
  pending: '#333333',
  active: NEON,
  ok: '#7a7a7a',
  lit: NEON,
  fail: FAIL,
  caution: CAUTION,
  muted: '#363636',
}

export function Minimap({ layout, camera }: { layout: TimelineLayout; camera: CameraControls }) {
  const frame = useRef<HTMLDivElement>(null)
  const { x, y, scale, view, world, centerAt } = camera

  useEffect(() => {
    const paint = () => {
      const el = frame.current
      if (el === null || view === null) return
      const seen = visibleRect({ x: x.get(), y: y.get(), scale: scale.get() }, view)
      const a = toMap(seen, world, MAP)
      const b = toMap({ x: seen.x + seen.width, y: seen.y + seen.height }, world, MAP)
      const left = Math.max(0, a.x)
      const top = Math.max(0, a.y)
      el.style.left = `${left}px`
      el.style.top = `${top}px`
      el.style.width = `${Math.max(4, Math.min(MAP.width, b.x) - left)}px`
      el.style.height = `${Math.max(4, Math.min(MAP.height, b.y) - top)}px`
    }
    paint()
    const stops = [x.on('change', paint), y.on('change', paint), scale.on('change', paint)]
    return () => stops.forEach((stop) => stop())
  }, [x, y, scale, view, world.x, world.y, world.width, world.height])

  const navigate = (e: PointerEvent<HTMLDivElement>) => {
    const box = e.currentTarget.getBoundingClientRect()
    centerAt(fromMap({ x: e.clientX - box.left, y: e.clientY - box.top }, world, MAP))
  }

  const kx = MAP.width / world.width
  const ky = MAP.height / world.height

  return (
    <div
      aria-hidden="true"
      className="absolute bottom-4 right-4 cursor-crosshair touch-none overflow-hidden rounded-lg border border-[#1f1f1f] bg-[#0c0c0c]/90 backdrop-blur"
      style={MAP}
      onPointerDown={(e) => {
        e.stopPropagation()
        e.currentTarget.setPointerCapture(e.pointerId)
        navigate(e)
      }}
      onPointerMove={(e) => {
        if (e.currentTarget.hasPointerCapture(e.pointerId)) navigate(e)
      }}
    >
      <svg width={MAP.width} height={MAP.height} className="absolute inset-0">
        <g transform={`scale(${kx} ${ky}) translate(${-world.x} ${-world.y})`}>
          {layout.edges.map((e) => (
            <path
              key={e.id}
              d={edgePath(e)}
              fill="none"
              stroke={e.tone === 'lit' ? NEON : e.tone === 'fail' ? FAIL : '#2c2c2c'}
              strokeOpacity={e.tone === 'lit' ? 0.7 : 1}
              strokeWidth={1}
              vectorEffect="non-scaling-stroke"
            />
          ))}
        </g>
        {layout.nodes.map((n) => {
          const p = toMap(n, world, MAP)
          return (
            <circle key={n.id} cx={p.x} cy={p.y} r={1.4} fill={DOT[n.lit && n.status === 'ok' ? 'lit' : n.status]} />
          )
        })}
        {layout.caps.map((c) => {
          const p = toMap(c, world, MAP)
          return <circle key={c.id} cx={p.x} cy={p.y} r={1.4} fill={c.status === 'fail' ? FAIL : DOT.muted} />
        })}
      </svg>
      <div ref={frame} className="pointer-events-none absolute rounded-[3px] border border-neon/50 bg-neon/[0.05]" />
    </div>
  )
}
