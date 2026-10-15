import * as RadixTabs from '@radix-ui/react-tabs'
import type { ComponentProps } from 'react'

// The value is controlled by the page (`?tab=`), so a tab survives a reload and a shared link.

const join = (base: string, extra: string | undefined) => (extra === undefined ? base : `${base} ${extra}`)

export const TabsRoot = RadixTabs.Root

export const TabsList = ({ className, ...rest }: ComponentProps<typeof RadixTabs.List>) => (
  <RadixTabs.List className={join('flex gap-6 border-b border-line', className)} {...rest} />
)

export const TabsTrigger = ({ className, ...rest }: ComponentProps<typeof RadixTabs.Trigger>) => (
  <RadixTabs.Trigger
    className={join(
      '-mb-px h-10 border-b-2 border-transparent text-base text-ink-2 transition-colors duration-150 hover:text-ink data-[state=active]:border-ink data-[state=active]:text-ink',
      className,
    )}
    {...rest}
  />
)

export const TabsContent = ({ className, ...rest }: ComponentProps<typeof RadixTabs.Content>) => (
  <RadixTabs.Content className={join('pt-6 focus-visible:outline-none', className)} {...rest} />
)
