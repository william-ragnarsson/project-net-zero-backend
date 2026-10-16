import type { HTMLAttributes, TableHTMLAttributes, TdHTMLAttributes, ThHTMLAttributes } from 'react'

// A whole-row link is a <Link> in the first cell with `after:absolute after:inset-0 after:content-['']`;
// an interactive row is `relative` so that overlay covers it.

const join = (base: string, extra: string | undefined) => (extra === undefined ? base : `${base} ${extra}`)

export function Table({ className, ...rest }: TableHTMLAttributes<HTMLTableElement>) {
  return (
    <div className="-mx-3 overflow-x-auto">
      <table className={join('w-full border-collapse text-left text-base', className)} {...rest} />
    </div>
  )
}

export const THead = (props: HTMLAttributes<HTMLTableSectionElement>) => <thead {...props} />

export const TBody = (props: HTMLAttributes<HTMLTableSectionElement>) => <tbody {...props} />

export function Tr({ interactive = false, className, ...rest }: HTMLAttributes<HTMLTableRowElement> & { interactive?: boolean }) {
  const base = interactive ? 'relative border-b border-line transition-colors duration-150 hover:bg-hover' : 'border-b border-line'
  return <tr className={join(base, className)} {...rest} />
}

export function Th({ align = 'left', className, ...rest }: ThHTMLAttributes<HTMLTableCellElement> & { align?: 'left' | 'right' }) {
  const base = `h-9 border-b border-line px-3 text-sm font-normal text-ink-3${align === 'right' ? ' text-right' : ''}`
  return <th className={join(base, className)} {...rest} />
}

export function Td({
  align = 'left',
  muted = false,
  className,
  ...rest
}: TdHTMLAttributes<HTMLTableCellElement> & { align?: 'left' | 'right'; muted?: boolean }) {
  let base = 'h-11 px-3 align-middle'
  if (align === 'right') base += ' text-right tabular-nums'
  if (muted) base += ' text-ink-3'
  return <td className={join(base, className)} {...rest} />
}
