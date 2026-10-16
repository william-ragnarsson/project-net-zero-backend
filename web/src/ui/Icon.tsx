import type { ReactNode } from 'react'

export type IconName =
  | 'menu'
  | 'close'
  | 'check'
  | 'chevron-down'
  | 'chevron-right'
  | 'more'
  | 'play'
  | 'pause'
  | 'restart'
  | 'plus'
  | 'arrow-right'
  | 'dashboard'
  | 'runs'
  | 'replays'
  | 'settings'
  | 'leaf'
  | 'target'
  | 'trend-down'
  | 'clock'
  | 'list-check'
  | 'flask'
  | 'code'
  | 'gauge'
  | 'shield'
  | 'file'
  | 'laptop'
  | 'eye'
  | 'search'
  | 'key'
  | 'server'
  | 'cpu'
  | 'keyboard'
  | 'github'
  | 'history'
  | 'database'

const dot = (cx: number, cy: number) => <circle cx={cx} cy={cy} r="0.9" fill="currentColor" stroke="none" />

const paths: Record<IconName, ReactNode> = {
  menu: <path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11" />,
  close: <path d="M4 4l8 8M12 4l-8 8" />,
  check: <path d="M3.5 8.5l3 3 6-7" />,
  'chevron-down': <path d="M4 6l4 4 4-4" />,
  'chevron-right': <path d="M6 4l4 4-4 4" />,
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
  plus: <path d="M8 3.5v9M3.5 8h9" />,
  'arrow-right': <path d="M3.5 8h9M9 4.5L12.5 8 9 11.5" />,
  dashboard: (
    <>
      <rect x="2.5" y="2.5" width="4.5" height="5.5" rx="1" />
      <rect x="9" y="2.5" width="4.5" height="3.5" rx="1" />
      <rect x="2.5" y="10" width="4.5" height="3.5" rx="1" />
      <rect x="9" y="8" width="4.5" height="5.5" rx="1" />
    </>
  ),
  runs: (
    <>
      <path d="M6.5 4h7M6.5 8h7M6.5 12h7" />
      {dot(3.25, 4)}
      {dot(3.25, 8)}
      {dot(3.25, 12)}
    </>
  ),
  replays: (
    <>
      <circle cx="8" cy="8" r="5.75" />
      <path d="M6.75 5.9v4.2L10.1 8z" fill="currentColor" />
    </>
  ),
  settings: (
    <>
      <path d="M2.5 5h6.25M12 5h1.5M2.5 11h1.5M7.25 11h6.25" />
      <circle cx="10.4" cy="5" r="1.6" />
      <circle cx="5.6" cy="11" r="1.6" />
    </>
  ),
  leaf: (
    <>
      <path d="M13.25 2.75c-6 0-9.5 3-9.5 7.25 0 1 .25 1.9.6 2.6.75.35 1.6.6 2.65.6 4.25 0 6.25-3.75 6.25-10.45z" />
      <path d="M2.75 13.25L9 7" />
    </>
  ),
  target: (
    <>
      <circle cx="8" cy="8" r="5.75" />
      <circle cx="8" cy="8" r="2.5" />
    </>
  ),
  'trend-down': <path d="M2.5 4.5l4 4 2.5-2.5 4.5 4.5M13.5 6.75v3.75H9.75" />,
  clock: (
    <>
      <circle cx="8" cy="8" r="5.75" />
      <path d="M8 5v3.25l2.1 1.3" />
    </>
  ),
  'list-check': <path d="M2.5 4.5l1.1 1.1 1.9-2.2M2.5 10.5l1.1 1.1 1.9-2.2M8 4.75h5.5M8 10.75h5.5" />,
  flask: <path d="M6.25 2.5h3.5M6.75 2.5v4.25l-3.4 5.6a.9.9 0 0 0 .77 1.4h7.76a.9.9 0 0 0 .77-1.4l-3.4-5.6V2.5M4.9 10h6.2" />,
  code: <path d="M5.5 4.5L2 8l3.5 3.5M10.5 4.5L14 8l-3.5 3.5" />,
  gauge: (
    <>
      <path d="M2.6 11.75a5.5 5.5 0 1 1 10.8 0" />
      <path d="M8 10.5l2.75-3.25" />
    </>
  ),
  shield: (
    <>
      <path d="M8 2l5 1.9v4c0 3-2.1 5.15-5 6.1-2.9-.95-5-3.1-5-6.1v-4z" />
      <path d="M5.9 8.1l1.45 1.45 2.75-3" />
    </>
  ),
  file: (
    <>
      <path d="M9.25 2H4.5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h7a1 1 0 0 0 1-1V5.25z" />
      <path d="M9.25 2v3.25h3.25M6 9h4M6 11.25h2.5" />
    </>
  ),
  laptop: (
    <>
      <rect x="3" y="3.5" width="10" height="7" rx="1" />
      <path d="M1.5 12.75h13" />
    </>
  ),
  eye: (
    <>
      <path d="M1.75 8S4 3.75 8 3.75 14.25 8 14.25 8 12 12.25 8 12.25 1.75 8 1.75 8z" />
      <circle cx="8" cy="8" r="1.9" />
    </>
  ),
  search: (
    <>
      <circle cx="7" cy="7" r="4.25" />
      <path d="M10.2 10.2l3.3 3.3" />
    </>
  ),
  key: (
    <>
      <circle cx="5.25" cy="10.75" r="2.75" />
      <path d="M7.2 8.8l5.8-5.8M11 5l1.75 1.75M9.25 6.75l1.5 1.5" />
    </>
  ),
  server: (
    <>
      <rect x="2.5" y="2.75" width="11" height="4.5" rx="1" />
      <rect x="2.5" y="8.75" width="11" height="4.5" rx="1" />
      {dot(5, 5)}
      {dot(5, 11)}
    </>
  ),
  cpu: (
    <>
      <rect x="4" y="4" width="8" height="8" rx="1" />
      <path d="M6.5 1.75V4M9.5 1.75V4M6.5 12v2.25M9.5 12v2.25M1.75 6.5H4M1.75 9.5H4M12 6.5h2.25M12 9.5h2.25" />
    </>
  ),
  keyboard: (
    <>
      <rect x="1.75" y="4" width="12.5" height="8" rx="1.25" />
      <path d="M4.5 6.75h.01M7 6.75h.01M9.5 6.75h.01M12 6.75h.01M5.5 9.5h5" />
    </>
  ),
  history: <path d="M2.75 8a5.25 5.25 0 1 0 1.55-3.7M2.5 2.75v2.5H5M8 5.25V8l2 1.25" />,
  database: (
    <>
      <ellipse cx="8" cy="4" rx="5" ry="1.75" />
      <path d="M3 4v8c0 .97 2.24 1.75 5 1.75s5-.78 5-1.75V4M3 8c0 .97 2.24 1.75 5 1.75S13 8.97 13 8" />
    </>
  ),
  github: (
    <>
      <path d="M8 2.25a5.75 5.75 0 0 0-1.8 11.2c.3.05.4-.12.4-.28v-1c-1.6.35-1.95-.7-1.95-.7-.26-.66-.64-.84-.64-.84-.52-.36.04-.35.04-.35.58.04.88.6.88.6.52.88 1.36.63 1.69.48.05-.37.2-.63.37-.77-1.28-.15-2.62-.64-2.62-2.84 0-.63.22-1.14.6-1.54-.06-.15-.26-.73.06-1.52 0 0 .48-.15 1.58.59a5.4 5.4 0 0 1 2.87 0c1.1-.74 1.58-.59 1.58-.59.32.79.12 1.37.06 1.52.37.4.6.91.6 1.54 0 2.2-1.35 2.69-2.63 2.83.2.18.39.53.39 1.07v1.58c0 .16.1.34.4.28A5.75 5.75 0 0 0 8 2.25z" />
    </>
  ),
}

/** The app's icons: 16px line glyphs, drawn at 12 to 20px. */
export function Icon({ name, size = 16, className }: { name: IconName; size?: 12 | 14 | 16 | 20; className?: string }) {
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
