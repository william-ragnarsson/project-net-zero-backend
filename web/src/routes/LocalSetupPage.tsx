// /new in the showcase build: the showcase can't start runs, so this says how to run net-zero locally.
import { useEffect, useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { PageHeader } from '../components/PageHeader'
import { useTitle } from '../lib/useTitle'
import { REPO_URL } from '../showcase/config'
import { Badge } from '../ui/Badge'
import { Button, buttonClass } from '../ui/Button'
import { Card, CardFooter, CardHeader } from '../ui/Card'
import { Icon } from '../ui/Icon'
import { Page } from '../ui/Page'

const LOCAL_URL = 'http://127.0.0.1:8000'

const STEPS: { title: string; commands: string[]; note: ReactNode }[] = [
  {
    title: 'Clone the repository',
    commands: [`git clone ${REPO_URL}.git`, 'cd project-net-zero-backend'],
    note: 'Public, no account needed.',
  },
  {
    title: 'Add your API key',
    commands: ['cp .env.example .env'],
    note: (
      <>
        Put your <code className="font-mono text-code">ANTHROPIC_API_KEY</code> in <code className="font-mono text-code">.env</code>.
        It stays on your machine. The demo repository runs without one.
      </>
    ),
  },
  {
    title: 'Install',
    commands: ['make setup'],
    note: 'Python 3.12 and the dependencies through uv, the web UI through npm, then a check of what is missing.',
  },
  {
    title: 'Start it',
    commands: ['make run'],
    note: `Open ${LOCAL_URL} and point it at a public GitHub repository.`,
  },
]

const NEEDS: { label: string; optional?: boolean }[] = [
  { label: 'macOS or Linux' },
  { label: 'uv and git' },
  { label: 'Node 20+ and npm' },
  { label: 'An Anthropic API key', optional: true },
]

function Commands({ lines }: { lines: string[] }) {
  const [copied, setCopied] = useState(false)
  useEffect(() => {
    if (!copied) return
    const t = window.setTimeout(() => setCopied(false), 1500)
    return () => window.clearTimeout(t)
  }, [copied])
  const copy = () => {
    void navigator.clipboard?.writeText(lines.join('\n')).then(
      () => setCopied(true),
      () => {},
    )
  }
  return (
    <div className="flex items-start gap-2 rounded-lg border border-control bg-page py-1.5 pl-3 pr-1.5">
      <pre className="min-w-0 flex-1 overflow-x-auto py-1 font-mono text-code text-ink">{lines.join('\n')}</pre>
      <Button size="sm" variant="quiet" onClick={copy} aria-label={`Copy: ${lines.join('; ')}`}>
        {copied ? 'Copied' : 'Copy'}
      </Button>
    </div>
  )
}

export function LocalSetupPage() {
  useTitle('Run it yourself')
  return (
    <Page>
      <PageHeader title="Run it yourself" meta="This site only plays back finished runs. net-zero itself runs on your machine, with your own key." />
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
        <Card className="min-w-0">
          <CardHeader
            title="Set it up"
            description="Four commands, a few minutes."
            action={
              <a href={REPO_URL} className={buttonClass('secondary', 'sm')} target="_blank" rel="noreferrer">
                <Icon name="github" size={14} />
                GitHub
              </a>
            }
          />
          <ol className="space-y-5 border-t border-line px-5 py-5">
            {STEPS.map((step, i) => (
              <li key={step.title} className="flex gap-3">
                <span
                  aria-hidden="true"
                  className="grid size-7 shrink-0 place-items-center rounded-full border border-line bg-page text-xs font-medium text-ink-2"
                >
                  {i + 1}
                </span>
                <div className="min-w-0 flex-1 space-y-2">
                  <h3 className="pt-1 text-base font-medium text-ink">{step.title}</h3>
                  <Commands lines={step.commands} />
                  <p className="text-sm text-ink-3">{step.note}</p>
                </div>
              </li>
            ))}
          </ol>
          <CardFooter>
            The README covers the CLI, the settings and how the energy is measured.
          </CardFooter>
        </Card>
        <aside className="space-y-6">
          <Card>
            <CardHeader title="What you need" />
            <ul className="space-y-2.5 px-5 pb-5 text-sm">
              {NEEDS.map((n) => (
                <li key={n.label} className="flex items-center justify-between gap-3">
                  <span className="text-ink-2">{n.label}</span>
                  {n.optional === true && <Badge>Optional</Badge>}
                </li>
              ))}
            </ul>
          </Card>
          <Card>
            <CardHeader title="Why not here?" />
            <p className="px-5 pb-5 text-sm text-ink-2">
              A run installs a repository, runs its tests and benchmarks, and calls Claude. That belongs on your machine,
              with your key, so this site only plays back finished runs.
            </p>
            <CardFooter>
              <Link to="/runs" className="inline-flex items-center gap-1 text-ink-2 hover:text-ink">
                See the runs
                <Icon name="arrow-right" size={12} />
              </Link>
            </CardFooter>
          </Card>
        </aside>
      </div>
    </Page>
  )
}
