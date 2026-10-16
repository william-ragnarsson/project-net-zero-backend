import * as Popover from '@radix-ui/react-popover'
import { createContext, useContext, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { buttonClass } from './Button'
import { Icon } from './Icon'

// A Radix Popover with menu semantics. Its role is overridden to "menu", which the keyboard
// shortcut guard (lib/shortcuts.ts) also looks for.

const CloseMenu = createContext<() => void>(() => {})

const ITEMS = '[role="menuitem"]'

function moveFocus(e: KeyboardEvent<HTMLDivElement>) {
  const items = Array.from(e.currentTarget.querySelectorAll<HTMLElement>(ITEMS))
  if (items.length === 0) return
  const at = items.indexOf(document.activeElement as HTMLElement)
  const last = items.length - 1
  const next =
    e.key === 'ArrowDown'
      ? at === last ? 0 : at + 1
      : e.key === 'ArrowUp'
        ? at <= 0 ? last : at - 1
        : e.key === 'Home'
          ? 0
          : e.key === 'End'
            ? last
            : null
  if (next === null) return
  e.preventDefault()
  items[next]?.focus()
}

/** A "more actions" button that opens a short list of items. */
export function Menu({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false)
  const content = useRef<HTMLDivElement>(null)
  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger aria-label={label} aria-haspopup="menu" className={buttonClass('quiet', 'md', true)}>
        <Icon name="more" />
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          ref={content}
          role="menu"
          align="end"
          sideOffset={6}
          onKeyDown={moveFocus}
          onOpenAutoFocus={(e) => {
            e.preventDefault()
            content.current?.querySelector<HTMLElement>(ITEMS)?.focus()
          }}
          className="z-50 min-w-[180px] rounded-lg border border-line bg-surface p-1 shadow-float animate-fade-in"
        >
          <CloseMenu.Provider value={() => setOpen(false)}>{children}</CloseMenu.Provider>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  )
}

const ITEM =
  'flex h-8 w-full items-center rounded-md px-2.5 text-left text-base text-ink hover:bg-hover focus-visible:bg-hover focus-visible:outline-none'

/**
 * A button item, or a link item when `href` is given (with `download` for a file).
 * `closeDelayMs` keeps the menu open a moment, for an item that confirms in its own label.
 */
export function MenuItem({
  onSelect,
  href,
  download,
  closeDelayMs,
  children,
}: {
  onSelect?: () => void
  href?: string
  download?: string
  closeDelayMs?: number
  children: ReactNode
}) {
  const close = useContext(CloseMenu)
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timer.current), [])
  const select = () => {
    onSelect?.()
    if (closeDelayMs === undefined) return close()
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(close, closeDelayMs)
  }
  if (href !== undefined) {
    return (
      <a role="menuitem" href={href} download={download} onClick={select} className={ITEM}>
        {children}
      </a>
    )
  }
  return (
    <button type="button" role="menuitem" onClick={select} className={ITEM}>
      {children}
    </button>
  )
}
