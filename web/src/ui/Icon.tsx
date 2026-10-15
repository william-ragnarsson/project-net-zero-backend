import type { ReactNode } from 'react'

export type IconName = 'menu' | 'close' | 'check' | 'chevron-down' | 'more' | 'play' | 'pause' | 'restart'

const paths: Record<IconName, ReactNode> = {
  menu: <path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11" />,
  close: <path d="M4 4l8 8M12 4l-8 8" />,
  check: <path d="M3.5 8.5l3 3 6-7" />,
  'chevron-down': <path d="M4 6l4 4 4-4" />,
  more: (
    <g fill="currentColor" stroke="none">
      <circle cx="3.5" cy="8" r="1.1" />
      <circle cx="8" cy="8" r="1.1" />
      <circle cx="12.5" cy="8" r="1.1" />
    </g>
  ),
  play: <path d="M5 3.5v9l7-4.5z" fill="currentColor" />,
  pause: <path d="M5.5 3.5v9M10.5 3.5v9" />,
  restart: <path d="M3.2 6.5A5 5 0 1 1 3 8.5M3 3.5v3h3" />,
}

/** The app's only icons: a handful of 16px line glyphs. */
export function Icon({ name, size = 16, className }: { name: IconName; size?: 12 | 16; className?: string }) {
  return (
    <svg
      viewBox="0 0 16 16"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className}
    >
      {paths[name]}
    </svg>
  )
}
