// The showcase build (`npm run build:showcase`, VITE_SHOWCASE=1) is a static site of finished
// runs with no server behind it. `netzero showcase` writes the runs to public/showcase
// (netzero/showcase.py); each GET the UI makes on /api maps to one of those files, and
// nothing else is answered. Starting a run means cloning the repository and running it locally.

export const SHOWCASE: boolean = import.meta.env.VITE_SHOWCASE === '1'

const BASE = '/showcase'

/** The showcase's own file, beside the ones that stand in for the API. */
export const SHOWCASE_META = `${BASE}/showcase.json`

export interface ShowcaseMeta {
  /** Runs made up to fill the pages (`netzero showcase --samples`), on repositories that don't exist. */
  samples: string[]
  /** Artifact URL in a log -> its static copy. A download missing from it is hidden. */
  artifacts: Record<string, string>
}

/** 'sample' when every run is made up, 'mixed' when some are. */
export type ShowcaseKind = 'sample' | 'mixed' | 'recorded'

export const showcaseKind = (samples: number, runs: number): ShowcaseKind =>
  samples === 0 ? 'recorded' : samples >= runs ? 'sample' : 'mixed'

/** What the showcase's runs are, wherever a page names them. */
export const SHOWCASE_RUNS: Record<ShowcaseKind, string> = {
  sample: 'sample runs on made-up repositories',
  mixed: 'recorded runs and samples on made-up repositories',
  recorded: 'recorded runs',
}

export const OFFLINE_TEXT = SHOWCASE ? 'Can’t load the runs on this site. Check your connection.' : 'Can’t reach the net-zero server'

export const REPO_URL = 'https://github.com/william-ragnarsson/project-net-zero-backend'

/** A button that starts a run leads to the local setup steps in the showcase, and says so. */
export const startLabel = (label: string): string => (SHOWCASE ? 'Run it yourself' : label)

// RUN_ID_RE in netzero/pipeline/store.py
const RUN = /^\/api\/runs\/(\d{8}-\d{6}-[a-z0-9][a-z0-9-]{0,47})(\/events\.jsonl)?$/

/** The static file that stands in for a GET on the API, or null when the showcase has none. */
export function showcasePath(apiPath: string): string | null {
  if (apiPath === '/api/health') return `${BASE}/health.json`
  if (apiPath === '/api/runs') return `${BASE}/runs.json`
  const m = RUN.exec(apiPath)
  if (m === null) return null
  return `${BASE}/runs/${m[1]}/${m[2] === undefined ? 'run.json' : 'events.jsonl'}`
}
