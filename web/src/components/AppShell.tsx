import { Link, NavLink, Outlet } from 'react-router'
import { Logo } from './Logo'

const navLink = ({ isActive }: { isActive: boolean }) =>
  `text-sm transition-colors ${isActive ? 'text-neon' : 'text-gray-400 hover:text-white'}`

/** The website's frame: a blurred top bar, then a centered column with hairline sides. */
export function AppShell() {
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-50 border-b border-dark-border bg-dark/80 backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-[1240px] items-center justify-between border-dark-border px-6 md:border-x md:px-12">
          <Link to="/" aria-label="Project Net Zero home">
            <Logo />
          </Link>
          <nav className="flex items-center gap-8">
            <NavLink to="/replay" className={navLink}>
              Replays
            </NavLink>
            <NavLink to="/history" className={navLink}>
              History
            </NavLink>
          </nav>
        </div>
      </header>
      <main className="mx-auto min-h-[calc(100vh-4rem)] max-w-[1240px] border-dark-border md:border-x">
        <Outlet />
      </main>
    </div>
  )
}
