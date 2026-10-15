// The app's two keyboard shortcuts. Esc belongs to Radix (sheet, menu) and the tree.

export type Shortcut = 'new' | 'filter'

const TYPING = 'input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="slider"]'

/** Keys typed here are text (or move a slider), never shortcuts. */
export const isTypingTarget = (el: EventTarget | null): boolean => el instanceof Element && el.closest(TYPING) !== null

/**
 * An open menu counts as a dialog: the Menu primitive turns Radix Popover's `role="dialog"` into
 * `role="menu"`, so the dialog selector alone would let `n` fire while a menu has focus.
 */
export const OPEN_LAYER = '[role="dialog"][data-state="open"], [role="menu"][data-state="open"]'

export const layerOpen = (): boolean => document.querySelector(OPEN_LAYER) !== null

export function shortcutFor(e: KeyboardEvent, dialogOpen: boolean): Shortcut | null {
  if (e.metaKey || e.ctrlKey || e.altKey) return null
  if (dialogOpen || isTypingTarget(e.target)) return null
  if (e.key === 'n') return 'new'
  if (e.key === '/') return 'filter'
  return null
}
