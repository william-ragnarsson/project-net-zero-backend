import { useId, type ReactNode } from 'react'
import { Link, NavLink } from 'react-router'
import { useHealth, useRuns } from '../api/queries'
import { formatWhen } from '../lib/format'
import { recentRuns, repoOf, runStatus, runValue, TONE_CLASS, type StatusTone } from '../lib/runs'
import { buttonClass } from '../ui/Button'
import { Icon, type IconName } from '../ui/Icon'
import { Logo } from './Logo'

const RECENT = 8

// the dot before each recent run: green breathing while it runs, then how it ended
const DOT: Record<StatusTone, string> = {
  live: 'bg-accent motion-safe:animate-breathe',
  success: 'bg-accent',
  caution: 'bg-caution motion-safe:animate-breathe',
  fail: 'bg-fail',
  neutral: 'bg-control',
}

const rowClass = (isActive: boolean) =>
  `group flex h-8 items-center gap-2.5 rounded-lg px-2 transition-colors duration-150 ${
    isActive ? 'bg-surface text-ink shadow-card ring-1 ring-line' : 'text-ink-2 hover:bg-hover hover:text-ink'
  }`

function NavItem({ to, icon, children }: { to: string; icon: IconName; children: ReactNode }) {
  return (
    <NavLink to={to} className={({ isActive }) => `${rowClass(isActive)} text-base`}>
      {({ isActive }) => (
        <>
          <Icon name={icon} className={isActive ? 'text-accent' : 'text-ink-3 group-hover:text-ink-2'} />
          {children}
        </>
      )}
    </NavLink>
  )
}

export function Sidebar() {
  const runs = useRuns().data ?? []
  const health = useHealth()
  const offline = health.isError
  const recent = recentRuns(runs, RECENT)
  // the sidebar is mounted twice below 1024px (aside and drawer), so ids must be unique
  const recentId = useId()

  return (
    <div className="flex h-full flex-col px-3 py-4">
      <Link to="/dashboard" className="flex h-8 items-center px-2">
        <Logo />
      </Link>
      <Link to="/new" className={`${buttonClass('primary', 'md')} mt-5 w-full justify-between`}>
        <span className="flex items-center gap-1.5">
          <Icon name="plus" />
          New run
        </span>
        <kbd aria-hidden="true" className="rounded bg-white/15 px-1.5 font-sans text-xs text-white/80">
          N
        </kbd>
      </Link>

      <nav aria-label="Main" className="mt-5 flex flex-col gap-0.5">
        <NavItem to="/dashboard" icon="dashboard">
          Dashboard
        </NavItem>
        <NavItem to="/runs" icon="runs">
          Runs
        </NavItem>
        <NavItem to="/replays" icon="replays">
          Replays
        </NavItem>
      </nav>

      {recent.length > 0 && (
        <>
          <div className="mt-6 flex items-center justify-between px-2">
            <h2 id={recentId} className="text-xs font-medium text-ink-3">
              Recent runs
            </h2>
            {runs.length > RECENT && (
              <Link to="/runs" className="text-xs text-ink-3 hover:text-ink">
                All runs
              </Link>
            )}
          </div>
          <ul aria-labelledby={recentId} className="mt-1.5 flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto">
            {recent.map((r) => {
              const repo = repoOf(r)
              const value = runValue(r)
              return (
                <li key={r.id}>
                  <NavLink
                    to={`/runs/${encodeURIComponent(r.id)}`}
                    title={`${repo.full} · ${formatWhen(r.created_ts)}`}
                    className={({ isActive }) => `${rowClass(isActive)} text-sm`}
                  >
                    <span aria-hidden="true" className={`size-1.5 shrink-0 rounded-full ${DOT[runStatus(r).tone]}`} />
                    <span className="min-w-0 truncate">{repo.name}</span>
                    <span className={`ml-auto shrink-0 text-xs tabular-nums ${TONE_CLASS[value.tone]}`}>{value.text}</span>
                  </NavLink>
                </li>
              )
            })}
          </ul>
        </>
      )}

      <div className="mt-auto space-y-2 pt-4">
        <NavItem to="/settings" icon="settings">
          Settings
        </NavItem>
        <div className="flex items-center gap-2.5 rounded-lg border border-line bg-surface px-3 py-2">
          <span
            aria-hidden="true"
            className={`size-2 shrink-0 rounded-full ${offline ? 'bg-fail' : 'bg-accent ring-4 ring-accent-tint'}`}
          />
          <div className="min-w-0 leading-tight">
            <p className={`text-sm font-medium ${offline ? 'text-fail' : 'text-ink'}`}>
              {offline ? 'Server offline' : 'Local server'}
            </p>
            <p className="truncate text-xs text-ink-3">
              {offline ? 'Start it with make run' : health.data === undefined ? 'Connecting' : `Version ${health.data.version}`}
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
