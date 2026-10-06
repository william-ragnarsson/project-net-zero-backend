// Nodes and caps as buttons over the edge layer: each opens its artifact (F4) via `?node=`.
import { chipText, Chips } from './Chips'
import { Glyph } from './Glyph'
import type { LayoutCap, LayoutNode, Point } from './layout'
import { FAIL, NODE, NODE_R } from './theme'

interface Props {
  nodes: readonly LayoutNode[]
  caps: readonly LayoutCap[]
  selectedId: string | null
  onSelect: (id: string) => void
  /** A node took keyboard focus: the camera brings it on screen. A click does not move it. */
  onFocus: (p: Point) => void
}

export function NodeLayer({ nodes, caps, selectedId, onSelect, onFocus }: Props) {
  return (
    <>
      {nodes.map((node) => (
        <NodeButton key={node.id} node={node} selected={node.id === selectedId} onSelect={onSelect} onFocus={onFocus} />
      ))}
      {caps.map((cap) => (
        <CapButton key={cap.id} node={cap} selected={cap.id === selectedId} onSelect={onSelect} onFocus={onFocus} />
      ))}
    </>
  )
}

const button =
  'group absolute flex w-max -translate-x-1/2 cursor-pointer flex-col items-center outline-none transition-[filter] hover:brightness-150'

const ring = 'pointer-events-none absolute -inset-[5px] rounded-full border'

interface ItemProps<T> {
  selected: boolean
  onSelect: (id: string) => void
  onFocus: (p: Point) => void
  node: T
}

function NodeButton({ node, selected, onSelect, onFocus }: ItemProps<LayoutNode>) {
  const style = NODE[node.lit && node.status === 'ok' ? 'lit' : node.status]
  const strong = node.lit || node.status === 'active'
  return (
    <button
      type="button"
      data-node={node.id}
      aria-label={[node.label, node.status, ...node.chips.map(chipText)].join(', ')}
      aria-current={selected || undefined}
      onClick={() => onSelect(node.id)}
      onFocus={(e) => {
        if (e.currentTarget.matches(':focus-visible')) onFocus(node)
      }}
      className={button}
      style={{ left: node.x, top: node.y - NODE_R }}
    >
      <span
        className="relative flex items-center justify-center rounded-full"
        style={{
          width: 2 * NODE_R,
          height: 2 * NODE_R,
          background: style.fill,
          color: style.glyph,
          border: `${strong ? 1.5 : 1}px ${style.dashed ? 'dashed' : 'solid'} ${style.border}`,
        }}
      >
        {node.status === 'active' && <span className={`${ring} tl-pulse border-neon/50`} />}
        {selected && <span className={`${ring} border-white/80`} />}
        <span className={`${ring} border-neon opacity-0 group-focus-visible:opacity-100`} />
        <Glyph name={node.glyph} />
      </span>
      <span className="mt-1.5 font-mono text-[10.5px] leading-none" style={{ color: style.label }}>
        {node.label}
      </span>
      <Chips chips={node.chips} />
    </button>
  )
}

const CAP_R = 9

function CapButton({ node: cap, selected, onSelect, onFocus }: ItemProps<LayoutCap>) {
  const color = cap.status === 'fail' ? FAIL : '#5f5f5f'
  return (
    <button
      type="button"
      data-node={cap.id}
      title={cap.detail || undefined}
      aria-label={`${cap.label}${cap.detail ? `: ${cap.detail}` : ''}`}
      aria-current={selected || undefined}
      onClick={() => onSelect(cap.id)}
      onFocus={(e) => {
        if (e.currentTarget.matches(':focus-visible')) onFocus(cap)
      }}
      className={button}
      style={{ left: cap.x, top: cap.y - CAP_R }}
    >
      <span
        className="relative flex items-center justify-center rounded-full bg-dark"
        style={{ width: 2 * CAP_R, height: 2 * CAP_R, color }}
      >
        {selected && <span className={`${ring} border-white/80`} />}
        <span className={`${ring} border-neon opacity-0 group-focus-visible:opacity-100`} />
        <Glyph name="cap" size={18} />
      </span>
      <span className="mt-1.5 font-mono text-[10.5px] leading-none" style={{ color }}>
        {cap.label}
      </span>
    </button>
  )
}
