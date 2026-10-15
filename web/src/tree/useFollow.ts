// Follow: the page keeps the live layer in view while a run plays. Following stops on the first
// gesture that scrolls the page by hand, and the follow chip turns it back on.
import { useCallback, useEffect, useRef, useState } from 'react'
import { clamp } from './layout'

/** The space kept free below the viewport's bottom edge. */
export const FOLLOW_BOTTOM = 24

/** A sideways wheel, or one with shift, scrolls across and never moves the page down. */
export const sideways = (e: WheelEvent): boolean => e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)

/** A gesture a code body scrolls itself is not a page scroll. */
export function scrollsInside(target: EventTarget | null, across: boolean): boolean {
  const box = target instanceof Element ? target.closest('[data-codebox]') : null
  if (box === null) return false
  return across ? box.scrollWidth > box.clientWidth + 1 : box.scrollHeight > box.clientHeight + 1
}

const pageMax = (): number => document.documentElement.scrollHeight - window.innerHeight

/** Whether the page can scroll further in the direction of `dy`. */
export function pageCanScroll(dy: number): boolean {
  if (dy < 0) return window.scrollY > 0.5
  if (dy > 0) return window.scrollY < pageMax() - 0.5
  return false
}

const KEYS = new Set(['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End'])
const OWN_KEYS = 'input, textarea, select, [role=slider], [role=radiogroup]'

/** The space a sticky bar takes at the top, plus the shell's top bar below lg. */
export function followInset(topInset: number): number {
  return topInset + (window.innerWidth < 1024 ? 48 : 0)
}

/**
 * The scroll position that shows the box `top..bot` (tree coordinates): centred between the
 * inset and the bottom margin when it fits, else with its bottom just above the viewport's.
 */
export function followTarget(
  box: { top: number; bot: number },
  treeTop: number,
  inset: number,
  viewportH: number,
  maxScroll: number,
): number {
  const bottomEdge = viewportH - FOLLOW_BOTTOM
  const avail = bottomEdge - inset
  const h = box.bot - box.top
  const want = h <= avail ? treeTop + box.top - inset - (avail - h) / 2 : treeTop + box.bot - bottomEdge
  return clamp(want, 0, Math.max(0, maxScroll))
}

export interface Follow {
  following: boolean
  stop(): void
  resume(): void
  /** the current value, for the frame loop, which runs outside React */
  ref: { readonly current: boolean }
}

/** Following is on by default. */
export function useFollow(): Follow {
  const [following, setFollowing] = useState(true)
  const ref = useRef(true)

  const set = useCallback((on: boolean) => {
    ref.current = on
    setFollowing(on)
  }, [])
  const stop = useCallback(() => {
    if (ref.current) set(false)
  }, [set])
  const resume = useCallback(() => set(true), [set])

  useEffect(() => {
    const onWheel = (e: WheelEvent) => {
      if (scrollsInside(e.target, sideways(e)) || sideways(e) || !pageCanScroll(e.deltaY)) return
      stop()
    }
    const onTouch = (e: TouchEvent) => {
      if (scrollsInside(e.target, false) || pageMax() <= 1) return
      stop()
    }
    // a press on the page's own scrollbar
    const onDown = (e: MouseEvent) => {
      if (e.clientX >= document.documentElement.clientWidth) stop()
    }
    const onKey = (e: KeyboardEvent) => {
      if (!KEYS.has(e.key)) return
      const t = e.target
      if (t instanceof HTMLElement && (t.isContentEditable || t.closest(OWN_KEYS) !== null)) return
      stop()
    }
    window.addEventListener('wheel', onWheel, { passive: true })
    window.addEventListener('touchmove', onTouch, { passive: true })
    window.addEventListener('mousedown', onDown, { passive: true })
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('wheel', onWheel)
      window.removeEventListener('touchmove', onTouch)
      window.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [stop])

  return { following, stop, resume, ref }
}
