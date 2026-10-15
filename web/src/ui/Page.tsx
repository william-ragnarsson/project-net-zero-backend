import type { ReactNode } from 'react'

/** The main column. `wide` is for pages that draw the tree. */
export function Page({ width = 'default', children }: { width?: 'default' | 'wide'; children: ReactNode }) {
  return (
    <div
      className={`mx-auto w-full px-4 py-6 sm:px-8 lg:px-10 lg:py-8 ${width === 'wide' ? 'max-w-[1280px]' : 'max-w-[1040px]'}`}
    >
      {children}
    </div>
  )
}
