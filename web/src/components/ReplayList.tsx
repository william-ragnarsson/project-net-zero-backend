import { Link } from 'react-router'
import { formatClock } from '../lib/format'
import { useReplayIndex } from '../sources/queries'
import { replayHref } from '../sources/replay'

/** The recorded runs in public/replays/index.json, as a hairline grid of links. */
export function ReplayList() {
  const index = useReplayIndex()
  if (index.isPending) return <p className="label">Loading replays</p>
  if (index.isError) return <p className="text-sm text-fail">Could not load the replay list: {index.error.message}</p>
  if (index.data.length === 0) {
    return (
      <p className="text-sm text-muted">
        No replays yet. <code className="font-mono text-gray-300">npm run synth</code> writes one.
      </p>
    )
  }
  return (
    <ul className="grid gap-px border border-dark-border bg-dark-border md:grid-cols-2">
      {index.data.map((ref) => (
        <li key={ref.id} className="bg-dark">
          <Link to={replayHref(ref.src)} className="group block h-full p-6 transition-colors hover:bg-dark-card">
            <p className="text-lg text-white transition-colors group-hover:text-neon">{ref.title}</p>
            {ref.description !== '' && <p className="mt-2 text-sm leading-relaxed text-gray-400">{ref.description}</p>}
            <p className="mt-4 font-mono text-xs text-muted">
              {ref.functions} functions · {formatClock(ref.duration_ms)}
            </p>
          </Link>
        </li>
      ))}
      {/* the grid's hairline color would otherwise fill the empty last cell */}
      {index.data.length % 2 === 1 && <li aria-hidden="true" className="hidden bg-dark md:block" />}
    </ul>
  )
}
