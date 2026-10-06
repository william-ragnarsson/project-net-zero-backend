import type { ReactNode } from 'react'

/** The heading block every page starts with. */
export function PageHeader({ label, title, children }: { label: string; title: ReactNode; children?: ReactNode }) {
  return (
    <header className="border-b border-dark-border px-6 pb-8 pt-12 md:px-12 md:pt-16">
      <p className="label">{label}</p>
      <h1 className="mt-4 text-balance text-[clamp(1.9rem,3.4vw,2.9rem)] font-light leading-[1.08] tracking-[-0.03em] text-white">
        {title}
      </h1>
      {children !== undefined && <div className="mt-4 max-w-2xl leading-relaxed text-gray-400">{children}</div>}
    </header>
  )
}
