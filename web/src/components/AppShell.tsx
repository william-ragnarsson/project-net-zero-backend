import { useEffect, useState } from 'react'
import { Link, Outlet, useLocation } from 'react-router'
import { useHealthEdges } from '../api/useHealthEdges'
import { useShortcuts } from '../lib/useShortcuts'
import { SHOWCASE } from '../showcase/config'
import { Button } from '../ui/Button'
import { Icon } from '../ui/Icon'
import { Sheet } from '../ui/Sheet'
import { Logo } from './Logo'
import { ShowcaseBanner } from './ShowcaseBanner'
import { Sidebar } from './Sidebar'

const WIDE = '(min-width: 1024px)'

/** A fixed sidebar from 1024px; below that, a slim top bar whose menu button opens it as a drawer. */
export function AppShell() {
  const [drawer, setDrawer] = useState(false)
  const { key } = useLocation()
  useShortcuts()
  useHealthEdges()

  // any navigation, including a link inside the drawer, closes it
  useEffect(() => setDrawer(false), [key])

  // and so does growing past the breakpoint, where the drawer has no button to close it
  useEffect(() => {
    const wide = window.matchMedia(WIDE)
    const onChange = () => {
      if (wide.matches) setDrawer(false)
    }
    wide.addEventListener('change', onChange)
    return () => wide.removeEventListener('change', onChange)
  }, [])

  return (
    <>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50 focus:rounded-lg focus:bg-surface focus:px-3 focus:py-2"
      >
        Skip to content
      </a>
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-60 border-r border-line bg-sidebar lg:block">
        <Sidebar />
      </aside>
      <header className="sticky top-0 z-20 flex h-12 items-center gap-1 border-b border-line bg-page px-2 lg:hidden">
        <Button variant="quiet" icon aria-label="Open menu" aria-expanded={drawer} onClick={() => setDrawer(true)}>
          <Icon name="menu" />
        </Button>
        <Link to="/dashboard" className="px-1">
          <Logo />
        </Link>
      </header>
      <Sheet open={drawer} onOpenChange={setDrawer} title="Menu">
        <Sidebar />
      </Sheet>
      <div className="lg:pl-60">
        {SHOWCASE && <ShowcaseBanner />}
        <main id="main" tabIndex={-1} className="focus:outline-none">
          <Outlet />
        </main>
      </div>
    </>
  )
}
