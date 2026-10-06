import { describe, expect, it } from 'vitest'
import {
  BAND,
  centerOn,
  clampCamera,
  followCamera,
  FOLLOW_AT,
  fromMap,
  toMap,
  visibleRect,
  worldRect,
  ZOOM_MAX,
  ZOOM_MIN,
  zoomAt,
} from './camera'

const view = { width: 1000, height: 600 }
const screenOf = (cam: { x: number; y: number; scale: number }, p: { x: number; y: number }) => ({
  x: p.x * cam.scale + cam.x,
  y: p.y * cam.scale + cam.y,
})

describe('camera', () => {
  it('zooms around the pointer, within the limits', () => {
    const cam = { x: -300, y: 120, scale: 1 }
    const at = { x: 420, y: 250 }
    const world = { x: (at.x - cam.x) / cam.scale, y: (at.y - cam.y) / cam.scale }
    const zoomed = zoomAt(cam, 1.3, at)
    expect(screenOf(zoomed, world).x).toBeCloseTo(at.x)
    expect(screenOf(zoomed, world).y).toBeCloseTo(at.y)
    expect(zoomAt(cam, 100, at).scale).toBe(ZOOM_MAX)
    expect(zoomAt(cam, 0.01, at).scale).toBe(ZOOM_MIN)
  })

  it('follows the tip at the same place on screen, and starts at the left while the run is short', () => {
    const far = followCamera(4000, 0.8, view)
    expect(screenOf(far, { x: 4000, y: 0 }).x).toBeCloseTo(FOLLOW_AT * view.width)
    expect(screenOf(far, { x: 0, y: (BAND.top + BAND.bottom) / 2 }).y).toBeCloseTo(view.height / 2)
    const near = followCamera(100, 1, view)
    expect(near.x).toBeLessThan(FOLLOW_AT * view.width - 100)
    expect(near.x).toBeGreaterThan(0)
  })

  it('never pans the drawing fully off screen', () => {
    const world = worldRect(3000)
    const lost = clampCamera({ x: -10_000, y: 5000, scale: 1 }, view, world)
    const seen = visibleRect(lost, view)
    expect(seen.x).toBeLessThan(world.x + world.width)
    expect(seen.y + seen.height).toBeGreaterThan(world.y)
    const fine = { x: -500, y: 200, scale: 1 }
    expect(clampCamera(fine, view, world)).toEqual(fine)
  })

  it('maps the minimap back to the same world point', () => {
    const world = worldRect(5000)
    const map = { width: 240, height: 64 }
    const p = { x: 2345, y: 64 }
    const back = fromMap(toMap(p, world, map), world, map)
    expect(back.x).toBeCloseTo(p.x)
    expect(back.y).toBeCloseTo(p.y)
    const centered = centerOn(p, 1.2, view)
    const seen = visibleRect(centered, view)
    expect(seen.x + seen.width / 2).toBeCloseTo(p.x)
  })
})
