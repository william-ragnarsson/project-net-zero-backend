import { useId } from 'react'
import { Link, NavLink } from 'react-router'
import { useHealth, useRuns } from '../api/queries'
import { formatWhen } from '../lib/format'
import { recentRuns, repoOf, runValue, TONE_CLASS } from '../lib/runs'
import { buttonClass } from '../ui/Button'
import { LiveDot } from '../ui/LiveDot'
import { Logo } from './Logo'

const RECENT = 12

const row = 'flex h-8 items-center rounded-lg px-2 transition-colors duration-150'
const navClass =
  (size: string) =>
  ({ isActive }: { isActive: boolean }) =>
    `${row} ${size} ${isActive ? 'bg-surface text-ink ring-1 ring-line' : 'text-ink-2 hover:bg-hover hover:text-ink'}`

const navRow = navClass('text-base')
const runRow = navClass('gap-2 text-sm')

export function Sidebar() {
  const runs = useRuns().data ?? []
  const offline = useHealth().isError
  const recent = recentRuns(runs, RECENT)
  // the sidebar is mounted twice below 1024px (aside and drawer), so ids must be unique
  const recentId = useId()

  return (
    <div className="flex h-full flex-col px-3 py-4">
      <Link to="/" className="flex h-8 items-center px-2">
        <Logo />
      </Link>
      <Link to="/new" className={`${buttonClass('primary', 'md')} mt-4 w-full`}>
        New run
      </Link>
      <nav aria-label="Main" className="mt-4 flex flex-col gap-0.5">
        <NavLink to="/" end className={navRow}>
          Dashboard
        </NavLink>
        <NavLink to="/runs" className={navRow}>
          Runs
        </NavLink>
        <NavLink to="/replays" className={navRow}>
          Replays
        </NavLink>
      </nav>

      {recent.length > 0 && (
        <>
          <h2 id={recentId} className="mt-6 px-2 text-xs text-ink-3">
            Recent
          </h2>
          <ul aria-labelledby={recentId} className="mt-1 flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto">
            {recent.map((r) => {
              const repo = repoOf(r)
              const value = runValue(r)
              return (
                <li key={r.id}>
                  <NavLink to={`/runs/${encodeURIComponent(r.id)}`} title={`${repo.full} · ${formatWhen(r.created_ts)}`} className={runRow}>
                    <span className="min-w-0 truncate">{repo.name}</span>
                    <span className="ml-auto flex shrink-0 items-center gap-2">
                      {value.live && <LiveDot />}
                      <span className={`text-xs ${TONE_CLASS[value.tone]}`}>{value.text}</span>
                    </span>
                  </NavLink>
                </li>
              )
            })}
          </ul>
          {runs.length > RECENT && (
            <Link to="/runs" className="flex h-8 items-center px-2 text-sm text-ink-2 hover:text-ink">
              All runs
            </Link>
          )}
        </>
      )}

      <div className="mt-auto border-t border-line pt-3">
        <NavLink to="/settings" className={navRow}>
          Settings
        </NavLink>
        <p className={`mt-1 px-2 text-xs ${offline ? 'text-fail' : 'text-ink-3'}`}>
          {offline ? 'Server offline' : 'Local server'}
        </p>
      </div>
    </div>
  )
}
