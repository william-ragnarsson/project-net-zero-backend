// Edges and repair loops, in one SVG whose coordinates are the world's.
import { useMemo } from 'react'
import { worldRect } from './camera'
import { edgePath, loopPath } from './geometry'
import type { TimelineLayout } from './layout'
import { CAUTION, DASH, EDGE, EDGE_ORDER, NODE_R } from './theme'

export function EdgeLayer({ layout }: { layout: TimelineLayout }) {
  const world = worldRect(layout.width)
  const edges = useMemo(
    () => [...layout.edges].sort((a, b) => EDGE_ORDER.indexOf(a.tone) - EDGE_ORDER.indexOf(b.tone)),
    [layout.edges],
  )
  return (
    <svg
      className="pointer-events-none absolute overflow-visible"
      style={{ left: world.x, top: world.y }}
      width={world.width}
      height={world.height}
      viewBox={`${world.x} ${world.y} ${world.width} ${world.height}`}
      aria-hidden="true"
    >
      <defs>
        <marker id="tl-loop-head" viewBox="0 0 6 6" refX="3" refY="3" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M1 1 5 3 1 5" fill="none" stroke={CAUTION} strokeWidth="1.2" strokeLinecap="round" />
        </marker>
      </defs>
      {edges.map((e) => {
        const style = EDGE[e.tone]
        return (
          <path
            key={e.id}
            data-edge={e.id}
            d={edgePath(e)}
            fill="none"
            stroke={style.stroke}
            strokeWidth={style.width}
            strokeOpacity={style.opacity}
            strokeDasharray={e.dashed ? DASH : undefined}
            strokeLinecap="round"
          />
        )
      })}
      {layout.loops.map((loop) => (
        <g key={loop.id}>
          <path
            d={loopPath(loop, NODE_R)}
            fill="none"
            stroke={CAUTION}
            strokeOpacity={0.85}
            strokeWidth={1.25}
            markerEnd="url(#tl-loop-head)"
          />
          {loop.count > 1 && (
            <text
              x={loop.x + NODE_R + 6}
              y={loop.y - NODE_R - 10}
              fill={CAUTION}
              fontFamily="JetBrains Mono, monospace"
              fontSize="9.5"
            >
              ×{loop.count}
            </text>
          )}
        </g>
      ))}
    </svg>
  )
}
