// The timeline's camera: motion values for pan and zoom, follow mode, and the input that
// moves it. Panning detaches follow; zooming keeps it, since the tip stays in view.
import { animate, useMotionValue, useReducedMotion, type MotionValue } from 'framer-motion'
import {
  useEffect,
  useEffectEvent,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
  type PointerEvent,
  type RefObject,
  type UIEvent,
} from 'react'
import {
  centerOn,
  clampCamera,
  clampScale,
  fitScale,
  followCamera,
  worldRect,
  zoomAt,
  type Camera,
  type Rect,
  type Size,
} from './camera'
import type { Point } from './layout'

const SPRING = { type: 'spring', stiffness: 140, damping: 26, mass: 0.9 } as const
/** Pixels a pointer moves before a press becomes a drag instead of a click. */
const DRAG_SLOP = 4
const KEY_PAN = 72
const KEY_ZOOM = 1.25
/** Wheel distance that doubles the zoom. */
const WHEEL_ZOOM = 240
/** Screen margin inside which `reveal` counts a point as on screen. */
const REVEAL_MARGIN = 80

export interface CameraControls {
  viewport: RefObject<HTMLDivElement | null>
  x: MotionValue<number>
  y: MotionValue<number>
  scale: MotionValue<number>
  /** null until the viewport has been measured */
  view: Size | null
  world: Rect
  following: boolean
  follow: () => void
  zoomBy: (factor: number) => void
  centerAt: (p: Point) => void
  /** Glides to `p` unless it is already comfortably on screen. */
  reveal: (p: Point) => void
  handlers: {
    onPointerDown: (e: PointerEvent<HTMLDivElement>) => void
    onPointerMove: (e: PointerEvent<HTMLDivElement>) => void
    onPointerUp: (e: PointerEvent<HTMLDivElement>) => void
    onPointerCancel: (e: PointerEvent<HTMLDivElement>) => void
    onClickCapture: (e: MouseEvent<HTMLDivElement>) => void
    onScroll: (e: UIEvent<HTMLDivElement>) => void
    onKeyDown: (e: KeyboardEvent<HTMLDivElement>) => void
  }
}

interface Drag {
  pointerId: number
  start: Point
  from: Camera
  moved: boolean
}

export function useCamera(width: number, tipX: number): CameraControls {
  const viewport = useRef<HTMLDivElement>(null)
  const x = useMotionValue(0)
  const y = useMotionValue(0)
  const scale = useMotionValue(1)
  const [view, setView] = useState<Size | null>(null)
  const [following, setFollowing] = useState(true)
  const reduceMotion = useReducedMotion() ?? false
  const placed = useRef(false)
  const drag = useRef<Drag | null>(null)
  const dragged = useRef(false)
  const world = worldRect(width)

  useLayoutEffect(() => {
    const el = viewport.current
    if (el === null) return
    const measure = () => setView({ width: el.clientWidth, height: el.clientHeight })
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  const current = (): Camera => ({ x: x.get(), y: y.get(), scale: scale.get() })

  const jump = (cam: Camera) => {
    x.stop()
    y.stop()
    scale.stop()
    x.set(cam.x)
    y.set(cam.y)
    scale.set(cam.scale)
  }

  // one spring per value with the same settings: a zoom's anchor point stays put throughout
  const glide = (cam: Camera) => {
    if (reduceMotion) return jump(cam)
    void animate(x, cam.x, SPRING)
    void animate(y, cam.y, SPRING)
    void animate(scale, cam.scale, SPRING)
  }

  const placeFollowing = useEffectEvent((size: Size) => {
    if (!placed.current) {
      placed.current = true
      jump(followCamera(tipX, fitScale(size), size))
      return
    }
    glide(followCamera(tipX, scale.get(), size))
  })

  useEffect(() => {
    if (following && view !== null) placeFollowing(view)
  }, [following, view, tipX])

  const move = (cam: Camera) => {
    if (view === null) return
    setFollowing(false)
    jump(clampCamera(cam, view, world))
  }

  const panBy = (dx: number, dy: number) => {
    const cam = current()
    move({ ...cam, x: cam.x + dx, y: cam.y + dy })
  }

  const zoomAround = (factor: number, at: Point | null, animated: boolean) => {
    if (view === null) return
    const target = following
      ? followCamera(tipX, clampScale(scale.get() * factor), view)
      : clampCamera(zoomAt(current(), factor, at ?? { x: view.width / 2, y: view.height / 2 }), view, world)
    if (animated) glide(target)
    else jump(target)
  }

  const onWheel = useEffectEvent((e: WheelEvent) => {
    e.preventDefault()
    const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? (view?.height ?? 800) : 1
    const dx = e.deltaX * unit
    const dy = e.deltaY * unit
    if (e.ctrlKey || e.metaKey) {
      const box = viewport.current?.getBoundingClientRect()
      const at = box === undefined ? null : { x: e.clientX - box.left, y: e.clientY - box.top }
      zoomAround(2 ** (-dy / WHEEL_ZOOM), at, false)
      return
    }
    if (e.shiftKey && dx === 0) panBy(-dy, 0)
    else panBy(-dx, -dy)
  })

  useEffect(() => {
    const el = viewport.current
    if (el === null) return
    // React's wheel listener is passive, and a zoom must not also scroll or zoom the page
    const listener = (e: WheelEvent) => onWheel(e)
    el.addEventListener('wheel', listener, { passive: false })
    return () => el.removeEventListener('wheel', listener)
  }, [])

  const handlers: CameraControls['handlers'] = {
    onPointerDown: (e) => {
      if (e.button !== 0) return
      dragged.current = false
      drag.current = { pointerId: e.pointerId, start: { x: e.clientX, y: e.clientY }, from: current(), moved: false }
    },
    onPointerMove: (e) => {
      const d = drag.current
      if (d === null || d.pointerId !== e.pointerId) return
      const dx = e.clientX - d.start.x
      const dy = e.clientY - d.start.y
      if (!d.moved) {
        if (Math.hypot(dx, dy) < DRAG_SLOP) return
        // captured only now, so a press that does not move still clicks the node under it
        d.moved = true
        e.currentTarget.setPointerCapture(e.pointerId)
      }
      move({ ...d.from, x: d.from.x + dx, y: d.from.y + dy })
    },
    onPointerUp: (e) => {
      if (drag.current?.pointerId !== e.pointerId) return
      dragged.current = drag.current.moved
      drag.current = null
    },
    onPointerCancel: () => {
      drag.current = null
    },
    onClickCapture: (e) => {
      if (!dragged.current) return
      dragged.current = false
      e.stopPropagation()
    },
    // the browser scrolls even an overflow-hidden box to show a focused node; the camera does that
    onScroll: (e) => {
      e.currentTarget.scrollLeft = 0
      e.currentTarget.scrollTop = 0
    },
    onKeyDown: (e) => {
      const step = e.shiftKey ? KEY_PAN * 4 : KEY_PAN
      const actions: Record<string, () => void> = {
        ArrowLeft: () => panBy(step, 0),
        ArrowRight: () => panBy(-step, 0),
        ArrowUp: () => panBy(0, step),
        ArrowDown: () => panBy(0, -step),
        '+': () => zoomAround(KEY_ZOOM, null, true),
        '=': () => zoomAround(KEY_ZOOM, null, true),
        '-': () => zoomAround(1 / KEY_ZOOM, null, true),
        _: () => zoomAround(1 / KEY_ZOOM, null, true),
      }
      const action = actions[e.key]
      if (action === undefined) return
      e.preventDefault()
      action()
    },
  }

  return {
    viewport,
    x,
    y,
    scale,
    view,
    world,
    following,
    follow: () => setFollowing(true),
    zoomBy: (factor) => zoomAround(factor, null, true),
    centerAt: (p) => {
      if (view !== null) move(centerOn(p, scale.get(), view))
    },
    reveal: (p) => {
      if (view === null) return
      const s = scale.get()
      const sx = p.x * s + x.get()
      const sy = p.y * s + y.get()
      const m = REVEAL_MARGIN
      if (sx >= m && sx <= view.width - m && sy >= m && sy <= view.height - m) return
      setFollowing(false)
      glide(clampCamera(centerOn(p, s, view), view, world))
    },
    handlers,
  }
}
