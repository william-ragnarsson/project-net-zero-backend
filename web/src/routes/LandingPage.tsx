// /: what net zero does, for someone who hasn't used it yet. The app itself starts at /dashboard.
import { useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { Co2 } from '../components/Co2'
import { Logo } from '../components/Logo'
import { useTitle } from '../lib/useTitle'
import { useReplayIndex } from '../sources/queries'
import { replayHref } from '../sources/replay'
import { Badge } from '../ui/Badge'
import { buttonClass } from '../ui/Button'
import { Icon, type IconName } from '../ui/Icon'

const COLUMN = 'mx-auto w-full max-w-[1120px] px-4 sm:px-8'

const SECTIONS = [
  { id: 'how', label: 'How it works' },
  { id: 'trust', label: 'Why trust it' },
  { id: 'numbers', label: 'The numbers' },
]

const STEPS: { icon: IconName; title: string; body: string }[] = [
  {
    icon: 'target',
    title: 'Pick the functions',
    body: 'It clones the repository and ranks the functions that do the most work. Up to 20 are listed and the top 8 are ticked. You choose.',
  },
  {
    icon: 'list-check',
    title: 'Write the tests first',
    body: 'Claude writes at least three tests per function, plus one that works it hard. They have to pass on the original before anything changes.',
  },
  {
    icon: 'code',
    title: 'Try three rewrites',
    body: 'One algorithmic, one using builtins and the standard library, one cautious. Each must pass the tests and match the original on recorded calls.',
  },
  {
    icon: 'gauge',
    title: 'Keep only real wins',
    body: 'Every rewrite is benchmarked against the original. The best one is kept if it uses at least 5% less and the difference holds up.',
  },
]

const FEATURES: { icon: IconName; title: string; body: ReactNode }[] = [
  {
    icon: 'shield',
    title: 'Same behaviour',
    body: 'Besides the tests, each rewrite is run on calls recorded from the original. Return values, exception types and changes to the arguments all have to match.',
  },
  {
    icon: 'flask',
    title: 'Measured, not guessed',
    body: 'A rewrite that looks faster once isn’t enough. It needs a statistically significant drop of at least 5%, corrected for trying three at a time.',
  },
  {
    icon: 'check',
    title: 'You stay in charge',
    body: 'You choose which functions to try, and it works on a copy. Your repository only changes when you apply the patch.',
  },
  {
    icon: 'file',
    title: 'Every decision on record',
    body: 'Each run is saved as a log of events. Open it later to see why a rewrite was kept or thrown out, or replay the whole run.',
  },
  {
    icon: 'laptop',
    title: 'Runs on your machine',
    body: 'The server, the tests and the benchmarks run locally. Live runs call Claude with your own Anthropic API key.',
  },
  {
    icon: 'eye',
    title: 'Watch it work',
    body: 'Follow a run as it happens: each function, its tests and its three rewrites, with results as they come in.',
  },
]

const OUTPUTS: { icon: IconName; label: string }[] = [
  { icon: 'file', label: 'A patch with every kept rewrite' },
  { icon: 'list-check', label: 'The generated tests, in a zip of the repository' },
  { icon: 'gauge', label: 'The measurements behind each decision' },
]

export function LandingPage() {
  useTitle(null)
  // straight into the first bundled replay; the list until the index has loaded
  const first = useReplayIndex().data?.[0]
  const replay = first === undefined ? '/replays' : replayHref(first.id)

  return (
    <div className="min-h-screen bg-page">
      <NavBar />

      <main id="main">
        <section className="relative overflow-hidden">
          {/* a soft green wash behind the hero */}
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-x-0 top-0 h-[560px] bg-[radial-gradient(60%_60%_at_50%_0%,var(--nz-accent-tint),transparent)]"
          />
          <div className={`${COLUMN} relative pb-16 pt-14 text-center sm:pb-24 sm:pt-24`}>
            <span className="inline-flex items-center gap-2 rounded-full border border-line bg-surface px-3 py-1 text-sm text-ink-2 shadow-card">
              <span className="size-1.5 rounded-full bg-accent" />
              For Python repositories on GitHub
            </span>
            <h1 className="mx-auto mt-6 max-w-[760px] text-balance text-2xl font-semibold tracking-tight text-ink sm:text-display">
              Make your Python use less energy.
            </h1>
            <p className="mx-auto mt-5 max-w-[600px] text-pretty text-md text-ink-2 sm:text-lg sm:font-normal">
              net zero finds the functions that do the most work, has Claude rewrite them, and keeps a rewrite only when
              it behaves the same and measurably uses less energy.
            </p>
            <div className="mt-8 flex flex-wrap justify-center gap-3">
              <Link to="/new" className={`${buttonClass('primary', 'md')} h-11 px-5 text-md`}>
                Start a run
                <Icon name="arrow-right" />
              </Link>
              <Link to={replay} className={`${buttonClass('secondary', 'md')} h-11 px-5 text-md`}>
                <Icon name="play" size={14} className="text-accent" />
                Watch a replay
              </Link>
            </div>
            <ul className="mt-6 flex flex-wrap justify-center gap-x-6 gap-y-2 text-sm text-ink-2">
              {['Runs on your own machine', 'The demo needs no API key', 'You review every change'].map((t) => (
                <li key={t} className="flex items-center gap-1.5">
                  <Icon name="check" size={14} className="text-accent" />
                  {t}
                </li>
              ))}
            </ul>

            <RunPreview />
          </div>
        </section>

        <section id="how" className="scroll-mt-16 border-t border-line bg-surface py-20 sm:py-24">
          <div className={COLUMN}>
            <SectionHead
              eyebrow="How it works"
              title="From a repository to a patch you can review"
              lead="Each function goes through the same four steps. Most rewrites don’t make it, and that’s the point."
            />
            <ol className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {STEPS.map((s, i) => (
                <li key={s.title} className="rounded-xl border border-line bg-page p-5">
                  <div className="flex items-center justify-between">
                    <span className="grid size-9 place-items-center rounded-lg bg-surface text-accent shadow-card ring-1 ring-line">
                      <Icon name={s.icon} />
                    </span>
                    <span className="text-sm tabular-nums text-ink-3">0{i + 1}</span>
                  </div>
                  <h3 className="mt-4 text-md font-medium text-ink">{s.title}</h3>
                  <p className="mt-1.5 text-pretty text-base text-ink-2">{s.body}</p>
                </li>
              ))}
            </ol>
            <div className="mt-6 flex flex-col gap-4 rounded-xl border border-line bg-page p-5 lg:flex-row lg:items-center lg:gap-8">
              <p className="shrink-0 text-base font-medium text-ink">At the end you get</p>
              <ul className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:gap-x-8">
                {OUTPUTS.map((o) => (
                  <li key={o.label} className="flex items-center gap-2 text-base text-ink-2">
                    <Icon name={o.icon} className="shrink-0 text-accent" />
                    {o.label}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </section>

        <section id="trust" className="scroll-mt-16 py-20 sm:py-24">
          <div className={COLUMN}>
            <SectionHead
              eyebrow="Why trust it"
              title="Every change has to earn its place"
              lead="A faster function that gives a different answer is a bug. So the checks come before the speed-up, not after."
            />
            <ul className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {FEATURES.map((f) => (
                <li key={f.title} className="rounded-xl border border-line bg-surface p-6 shadow-card">
                  <span className="grid size-9 place-items-center rounded-lg bg-accent-tint text-accent">
                    <Icon name={f.icon} />
                  </span>
                  <h3 className="mt-4 text-md font-medium text-ink">{f.title}</h3>
                  <p className="mt-1.5 text-pretty text-base text-ink-2">{f.body}</p>
                </li>
              ))}
            </ul>
          </div>
        </section>

        <section id="numbers" className="scroll-mt-16 border-y border-line bg-surface py-20 sm:py-24">
          <div className={`${COLUMN} grid items-center gap-12 lg:grid-cols-2`}>
            <div>
              <SectionHead
                align="left"
                eyebrow="The numbers"
                title="Honest about what it measures"
                lead={
                  <>
                    The <Co2 /> figures are estimates. They are good for telling whether a rewrite beats the original,
                    which is the decision that matters. Treat the absolute grams as a rough guide.
                  </>
                }
              />
              <p className="mt-4 text-pretty text-base text-ink-2">
                On Linux with a readable RAPL counter the processor’s energy is measured. On a Mac you can calibrate
                the power figure once. Everywhere else it falls back to a fixed estimate, and the run says which one
                it used.
              </p>
            </div>
            <Formula />
          </div>
        </section>

        <section className="py-20 sm:py-24">
          <div className={COLUMN}>
            <div className="relative overflow-hidden rounded-2xl bg-accent px-6 py-12 text-center sm:px-12 sm:py-16">
              <div
                aria-hidden="true"
                className="pointer-events-none absolute inset-0 bg-[radial-gradient(50%_80%_at_50%_0%,rgba(255,255,255,0.16),transparent)]"
              />
              <h2 className="relative text-balance text-xl font-semibold tracking-tight text-white sm:text-2xl">
                Try it on the demo first
              </h2>
              <p className="relative mx-auto mt-3 max-w-[520px] text-pretty text-md text-white/80">
                The demo runs the whole pipeline on a small bundled repository. Claude’s replies are written in advance,
                so it needs no API key, but the tests and benchmarks really run. Or just watch a recorded run.
              </p>
              <div className="relative mt-8 flex flex-wrap justify-center gap-3">
                <Link
                  to="/new"
                  className="inline-flex h-11 items-center gap-1.5 rounded-lg bg-white px-5 text-md font-medium text-accent transition-colors duration-150 hover:bg-accent-tint"
                >
                  Start a run
                </Link>
                <Link
                  to={replay}
                  className="inline-flex h-11 items-center gap-1.5 rounded-lg px-5 text-md font-medium text-white ring-1 ring-white/40 transition-colors duration-150 hover:bg-white/10"
                >
                  Watch a replay
                </Link>
              </div>
            </div>
          </div>
        </section>
      </main>

      <Footer />
    </div>
  )
}

function NavBar() {
  const [open, setOpen] = useState(false)
  const close = () => setOpen(false)

  return (
    <header className="sticky top-0 z-30 border-b border-line bg-[rgba(246,246,243,0.82)] backdrop-blur-md">
      <div className={`${COLUMN} flex h-14 items-center gap-6`}>
        <Link to="/" className="flex items-center" onClick={close}>
          <Logo />
        </Link>
        <nav aria-label="Main" className="hidden flex-1 items-center gap-1 md:flex">
          {SECTIONS.map((s) => (
            <a key={s.id} href={`#${s.id}`} className={buttonClass('quiet', 'sm')}>
              {s.label}
            </a>
          ))}
          <Link to="/replays" className={buttonClass('quiet', 'sm')}>
            Replays
          </Link>
        </nav>
        <div className="ml-auto flex items-center gap-2">
          <Link to="/dashboard" className={`${buttonClass('secondary', 'sm')} hidden sm:inline-flex`}>
            Open the app
          </Link>
          <Link to="/new" className={buttonClass('primary', 'sm')}>
            Start a run
          </Link>
          <button
            type="button"
            aria-label={open ? 'Close menu' : 'Open menu'}
            aria-expanded={open}
            aria-controls="landing-menu"
            onClick={() => setOpen((o) => !o)}
            className={`${buttonClass('quiet', 'sm', true)} md:hidden`}
          >
            <Icon name={open ? 'close' : 'menu'} />
          </button>
        </div>
      </div>
      {open && (
        <nav id="landing-menu" aria-label="Menu" className="border-t border-line bg-page md:hidden">
          <div className={`${COLUMN} flex flex-col py-2`}>
            {SECTIONS.map((s) => (
              <a key={s.id} href={`#${s.id}`} onClick={close} className="rounded-lg px-2 py-2.5 text-base text-ink-2 hover:bg-hover hover:text-ink">
                {s.label}
              </a>
            ))}
            <Link to="/replays" className="rounded-lg px-2 py-2.5 text-base text-ink-2 hover:bg-hover hover:text-ink">
              Replays
            </Link>
            <Link to="/dashboard" className="rounded-lg px-2 py-2.5 text-base text-ink-2 hover:bg-hover hover:text-ink">
              Open the app
            </Link>
          </div>
        </nav>
      )}
    </header>
  )
}

function SectionHead({
  eyebrow,
  title,
  lead,
  align = 'center',
}: {
  eyebrow: string
  title: string
  lead: ReactNode
  align?: 'center' | 'left'
}) {
  return (
    <div className={align === 'center' ? 'mx-auto max-w-[640px] text-center' : 'max-w-[560px]'}>
      <p className="text-sm font-medium text-accent">{eyebrow}</p>
      <h2 className="mt-2 text-balance text-xl font-semibold tracking-tight text-ink sm:text-2xl">{title}</h2>
      <p className="mt-4 text-pretty text-md text-ink-2">{lead}</p>
    </div>
  )
}

// The demo run's dedupe_preserve_order (web/src/synth/catalog.ts). Its numbers are scripted, and the caption says so.
const CANDIDATES: { id: string; strategy: string; note: string; pct: string; tone: 'success' | 'neutral' }[] = [
  { id: 'A', strategy: 'Track seen items in a set', note: 'Repaired after a failed test', pct: '−71.8%', tone: 'neutral' },
  { id: 'B', strategy: 'Use dict.fromkeys', note: 'Kept: the biggest drop', pct: '−78.6%', tone: 'success' },
  { id: 'C', strategy: 'Hoist the bound method', note: 'Not a significant change', pct: '−2.1%', tone: 'neutral' },
]

const DIFF: { sign: ' ' | '-' | '+'; text: string }[] = [
  { sign: ' ', text: 'def dedupe_preserve_order(items):' },
  { sign: '-', text: '    result = []' },
  { sign: '-', text: '    for item in items:' },
  { sign: '-', text: '        if item not in result:' },
  { sign: '-', text: '            result.append(item)' },
  { sign: '-', text: '    return result' },
  { sign: '+', text: '    return list(dict.fromkeys(items))' },
]

const DIFF_LINE = { ' ': 'text-ink-2', '-': 'bg-fail-tint text-fail', '+': 'bg-accent-tint text-accent' }

function RunPreview() {
  return (
    <figure className="mx-auto mt-14 max-w-[960px] text-left sm:mt-20">
      <div className="overflow-hidden rounded-2xl border border-line bg-surface shadow-lift">
        <div className="flex items-center gap-2 border-b border-line bg-page px-4 py-2.5">
          <span className="flex gap-1.5" aria-hidden="true">
            <span className="size-2.5 rounded-full bg-control" />
            <span className="size-2.5 rounded-full bg-control" />
            <span className="size-2.5 rounded-full bg-control" />
          </span>
          <span className="mx-auto truncate rounded-md bg-surface px-3 py-0.5 text-xs text-ink-3 ring-1 ring-line">
            127.0.0.1:8000/replays/synthetic
          </span>
          <span className="w-[42px]" aria-hidden="true" />
        </div>

        <div className="grid grid-cols-[minmax(0,1fr)] md:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]">
          <div className="border-b border-line p-5 md:border-b-0 md:border-r">
            <p className="text-xs text-ink-3">textkit.dedupe</p>
            <p className="mt-0.5 font-mono text-[13px] font-medium text-ink">dedupe_preserve_order</p>
            <p className="mt-1 text-sm text-ink-2">3 rewrites tried, 1 kept</p>
            <ul className="mt-4 space-y-2">
              {CANDIDATES.map((c) => (
                <li
                  key={c.id}
                  className={`flex items-center gap-3 rounded-lg border px-3 py-2.5 ${
                    c.tone === 'success' ? 'border-accent-soft bg-accent-tint' : 'border-line bg-surface'
                  }`}
                >
                  <span
                    className={`grid size-6 shrink-0 place-items-center rounded-md text-xs font-medium ${
                      c.tone === 'success' ? 'bg-accent text-white' : 'bg-hover text-ink-2'
                    }`}
                  >
                    {c.id}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm text-ink">{c.strategy}</span>
                    <span className="block truncate text-xs text-ink-3">{c.note}</span>
                  </span>
                  {c.tone === 'success' ? (
                    <Badge tone="success">{c.pct}</Badge>
                  ) : (
                    <span className="shrink-0 text-sm tabular-nums text-ink-3">{c.pct}</span>
                  )}
                </li>
              ))}
            </ul>
          </div>

          <div className="min-w-0 p-5">
            <p className="text-sm font-medium text-ink">Kept rewrite</p>
            <pre className="mt-3 overflow-x-auto rounded-lg border border-line py-2 font-mono text-code">
              {DIFF.map((l, i) => (
                <span key={i} className={`block px-3 ${DIFF_LINE[l.sign]}`}>
                  <span aria-hidden="true" className="mr-3 inline-block w-2 select-none opacity-60">
                    {l.sign}
                  </span>
                  {l.text}
                </span>
              ))}
            </pre>
            <dl className="mt-4 grid grid-cols-3 gap-2 text-center">
              {(
                [
                  ['tests', 'Tests', 'pass'],
                  ['calls', 'Recorded calls', 'match'],
                  [
                    'co2',
                    <>
                      <Co2 />e per call
                    </>,
                    '−78.6%',
                  ],
                ] as const
              ).map(([id, k, v]) => (
                <div key={id} className="rounded-lg bg-page px-2 py-2.5">
                  <dt className="text-xs text-ink-3">{k}</dt>
                  <dd className="mt-0.5 text-sm font-medium text-accent">{v}</dd>
                </div>
              ))}
            </dl>
          </div>
        </div>
      </div>
      <figcaption className="mt-3 text-center text-sm text-ink-3">
        A function from the demo replay. The replay is scripted, so its numbers are examples, not measurements.
      </figcaption>
    </figure>
  )
}

const TERMS: [string, string][] = [
  ['CPU, wall time', 'Measured for each call by the benchmark'],
  ['P_core', 'Calibrated once on a Mac, otherwise estimated from the chip'],
  ['P_ram', 'CodeCarbon’s model for memory'],
  ['grid factor', 'Your country with NETZERO_COUNTRY, or the world average'],
]

function Formula() {
  return (
    <div className="rounded-2xl border border-line bg-page p-6 sm:p-8">
      <p className="text-sm text-ink-3">
        <Co2 />e per call, when there’s no RAPL meter
      </p>
      <p className="mt-2 font-mono text-[13px] leading-relaxed text-ink sm:text-[14px]">
        (CPU time × P_core + wall time × P_ram)
        <br />× grid factor
      </p>
      <dl className="mt-6 divide-y divide-line border-t border-line">
        {TERMS.map(([term, what]) => (
          <div key={term} className="flex flex-col gap-0.5 py-3 sm:flex-row sm:gap-4">
            <dt className="w-28 shrink-0 font-mono text-code text-ink">{term}</dt>
            <dd className="text-sm text-ink-2">{what}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

const FOOTER: { title: string; links: { label: string; to: string; anchor?: boolean }[] }[] = [
  {
    title: 'App',
    links: [
      { label: 'Dashboard', to: '/dashboard' },
      { label: 'New run', to: '/new' },
      { label: 'Runs', to: '/runs' },
      { label: 'Replays', to: '/replays' },
    ],
  },
  {
    title: 'Learn',
    links: SECTIONS.map((s) => ({ label: s.label, to: `#${s.id}`, anchor: true })),
  },
]

function Footer() {
  return (
    <footer className="border-t border-line bg-surface">
      <div className={`${COLUMN} grid gap-10 py-12 sm:grid-cols-[2fr_1fr_1fr]`}>
        <div>
          <Logo />
          <p className="mt-3 max-w-[320px] text-pretty text-sm text-ink-2">
            Rewrites Python functions to use less energy, and keeps only the rewrites it can back up. Started as a
            HackEurope project.
          </p>
        </div>
        {FOOTER.map((col) => (
          <div key={col.title}>
            <h2 className="text-sm font-medium text-ink">{col.title}</h2>
            <ul className="mt-3 space-y-2">
              {col.links.map((l) => (
                <li key={l.label}>
                  {l.anchor === true ? (
                    <a href={l.to} className="text-sm text-ink-2 hover:text-ink">
                      {l.label}
                    </a>
                  ) : (
                    <Link to={l.to} className="text-sm text-ink-2 hover:text-ink">
                      {l.label}
                    </Link>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      <div className="border-t border-line">
        <p className={`${COLUMN} py-5 text-pretty text-xs text-ink-3`}>
          Runs on your own machine. Live runs need an Anthropic API key; the demo and the replays work without one.
        </p>
      </div>
    </footer>
  )
}
