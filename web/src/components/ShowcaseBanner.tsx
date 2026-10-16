import { Link } from 'react-router'
import { useShowcaseKind } from '../api/queries'
import { SHOWCASE_RUNS } from '../showcase/config'
import { Icon } from '../ui/Icon'

/** The showcase build's one standing line: what these runs are, and where to run your own. */
export function ShowcaseBanner() {
  const kind = useShowcaseKind()
  const what = kind === undefined ? '' : `${capitalize(SHOWCASE_RUNS[kind])}. `
  return (
    <div
      role="note"
      className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line bg-sidebar px-4 py-2 text-sm text-ink-2 sm:px-8 lg:px-10"
    >
      <span className="flex min-w-0 items-center gap-2">
        <Icon name="eye" size={14} className="shrink-0 text-ink-3" />
        {what}Nothing here runs live.
      </span>
      <Link to="/new" className="inline-flex items-center gap-1 font-medium text-ink hover:text-accent">
        Run it on your own code
        <Icon name="arrow-right" size={12} />
      </Link>
    </div>
  )
}

const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)
