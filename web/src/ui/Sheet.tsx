import * as Dialog from '@radix-ui/react-dialog'
import { useRef, type ReactNode } from 'react'
import { Button } from './Button'
import { Icon } from './Icon'

/** A panel that slides in from the left: the sidebar below 1024px. Esc and the scrim close it. */
export function Sheet({
  open,
  onOpenChange,
  title,
  children,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  children: ReactNode
}) {
  // Radix hands focus back to a Dialog.Trigger on close. The sheet is opened by a plain button
  // outside it, so remember what had focus when it opened and return there instead.
  const opener = useRef<HTMLElement | null>(null)
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-scrim animate-fade-in" />
        <Dialog.Content
          aria-describedby={undefined}
          onOpenAutoFocus={() => {
            opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
          }}
          onCloseAutoFocus={(e) => {
            e.preventDefault()
            opener.current?.focus()
          }}
          className="fixed inset-y-0 left-0 z-50 flex w-[280px] max-w-[85vw] flex-col bg-sidebar shadow-float animate-sheet-in focus:outline-none"
        >
          <Dialog.Title className="sr-only">{title}</Dialog.Title>
          {children}
          <Dialog.Close asChild>
            <Button variant="quiet" icon aria-label="Close menu" className="absolute right-2 top-2">
              <Icon name="close" />
            </Button>
          </Dialog.Close>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
