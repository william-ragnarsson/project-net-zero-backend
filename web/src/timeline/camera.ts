// The camera's arithmetic. A camera maps a world point p to the screen at p * scale + (x, y),
// with the screen measured from the viewport's top left corner.
import { COL, LANE, type Point } from './layout'

export interface Camera {
  x: number
  y: number
  scale: number
}

export interface Size {
  width: number
  height: number
}

export interface Rect extends Point, Size {}

export const ZOOM_MIN = 0.4
export const ZOOM_MAX = 1.75

/** The world's vertical extent: function titles above the trunk down to the lowest lane's chips. */
export const BAND = { top: -96, bottom: 3 * LANE + 56 } as const

/** Follow mode keeps the tip this far across the viewport, leaving room ahead for what comes next. */
export const FOLLOW_AT = 0.62
/** Screen space left of the drawing's first node, while the drawing is narrower than the screen. */
const LEAD = 64
/** How much of the drawing a pan always leaves on screen, in screen pixels. */
const KEEP = 120

const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, n))

export const clampScale = (scale: number) => clamp(scale, ZOOM_MIN, ZOOM_MAX)

/** The world's bounds for a drawing `width` wide. */
export const worldRect = (width: number): Rect => ({
  x: -COL / 2,
  y: BAND.top,
  width: width + COL,
  height: BAND.bottom - BAND.top,
})

/** The largest scale up to 1 that fits the band's height in the viewport. */
export const fitScale = (view: Size) => clampScale(Math.min(1, view.height / (BAND.bottom - BAND.top + 64)))

/** Zooms by `factor`, keeping the world point under `at` (screen) where it is. */
export function zoomAt(cam: Camera, factor: number, at: Point): Camera {
  const scale = clampScale(cam.scale * factor)
  const k = scale / cam.scale
  return { scale, x: at.x - (at.x - cam.x) * k, y: at.y - (at.y - cam.y) * k }
}

/** Keeps some of the world on screen whatever the pan. */
export function clampCamera(cam: Camera, view: Size, world: Rect): Camera {
  const s = cam.scale
  return {
    scale: s,
    x: clamp(cam.x, KEEP - (world.x + world.width) * s, view.width - KEEP - world.x * s),
    y: clamp(cam.y, KEEP - (world.y + world.height) * s, view.height - KEEP - world.y * s),
  }
}

/** Where follow mode wants the camera: the tip `FOLLOW_AT` across, the band centered. */
export function followCamera(tipX: number, scale: number, view: Size): Camera {
  return {
    scale,
    x: Math.min(LEAD, FOLLOW_AT * view.width - tipX * scale),
    y: view.height / 2 - ((BAND.top + BAND.bottom) / 2) * scale,
  }
}

/** The camera that puts world point `p` at the viewport's center. */
export const centerOn = (p: Point, scale: number, view: Size): Camera => ({
  scale,
  x: view.width / 2 - p.x * scale,
  y: view.height / 2 - p.y * scale,
})

/** The part of the world on screen. */
export const visibleRect = (cam: Camera, view: Size): Rect => ({
  x: -cam.x / cam.scale,
  y: -cam.y / cam.scale,
  width: view.width / cam.scale,
  height: view.height / cam.scale,
})

/** Maps between the world and a minimap of size `map`, which stretches `world` to fill it. */
export const toMap = (p: Point, world: Rect, map: Size): Point => ({
  x: ((p.x - world.x) / world.width) * map.width,
  y: ((p.y - world.y) / world.height) * map.height,
})

export const fromMap = (p: Point, world: Rect, map: Size): Point => ({
  x: world.x + (p.x / map.width) * world.width,
  y: world.y + (p.y / map.height) * world.height,
})
