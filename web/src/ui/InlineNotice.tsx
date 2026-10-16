import type { ReactNode } from 'react'

const TONE = {
  neutral: 'border border-line bg-sidebar text-ink',
  fail: 'bg-fail-tint text-fail',
  win: 'bg-accent-tint text-ink',
} as const

/** One line of news inside a page, with an optional action on the right. */
export function InlineNotice({
  tone = 'neutral',
  action,
  children,
}: {
  tone?: keyof typeof TONE
  action?: ReactNode
  children: ReactNode
}) {
  return (
    <div
      role={tone === 'fail' ? 'alert' : 'status'}
      className={`flex items-center justify-between gap-3 rounded-lg px-3.5 py-2.5 text-base ${TONE[tone]}`}
    >
      <div className="min-w-0">{children}</div>
      {action !== undefined && <div className="shrink-0">{action}</div>}
    </div>
  )
}
