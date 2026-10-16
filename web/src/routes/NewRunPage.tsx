// /new: a form that starts a run on a GitHub repository, the bundled demo beside it, and what happens next.
import { useEffect, useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router'
import { isApiError } from '../api/client'
import { useCapabilities, useCreateRun, useRuns } from '../api/queries'
import { PageHeader } from '../components/PageHeader'
import type { CreateRunRequest, RunSummary } from '../gen/events'
import { isActive, parseRepoUrl } from '../lib/runs'
import { useTitle } from '../lib/useTitle'
import { Badge } from '../ui/Badge'
import { Button, buttonClass } from '../ui/Button'
import { Card, CardHeader } from '../ui/Card'
import { Field } from '../ui/Field'
import { Icon, type IconName } from '../ui/Icon'
import { InlineNotice } from '../ui/InlineNotice'
import { Input } from '../ui/Input'
import { Page } from '../ui/Page'

const INVALID = 'Enter a GitHub repository URL, like https://github.com/owner/repo'

const noticeLink = buttonClass('secondary', 'sm')

const NEXT: { icon: IconName; title: string; body: string }[] = [
  { icon: 'target', title: 'It finds candidates', body: 'It clones the repository, installs what it can, and ranks up to 20 functions.' },
  { icon: 'list-check', title: 'You choose', body: 'The best eight are picked for you. Change the selection, then confirm.' },
  { icon: 'flask', title: 'It tries rewrites', body: 'Tests first, then three rewrites per function, each checked and benchmarked.' },
  { icon: 'file', title: 'You get a patch', body: 'Only the rewrites that passed and used less energy, with the numbers for each.' },
]

export function NewRunPage() {
  useTitle('New run')
  const navigate = useNavigate()
  const { key } = useLocation()
  const [params] = useSearchParams()
  const [repo, setRepo] = useState(() => params.get('repo') ?? '')
  const [ref, setRef] = useState(() => params.get('ref') ?? '')
  const [invalid, setInvalid] = useState(false)

  const caps = useCapabilities().data
  const runs = useRuns().data
  const create = useCreateRun()
  const error = create.error

  // `n` lands here from anywhere, including from this page, which does not remount
  useEffect(() => document.getElementById('repo')?.focus(), [key])

  // Gates apply once capabilities arrive; until then the form stays usable and the server's 412 guards it.
  const noLive = caps !== undefined && !caps.modes.includes('live')
  const demo = caps?.demo_available === true

  // A 409 names the run in the way before ['runs'] does, until the list says it has finished.
  const raced = isApiError(error, 'run_active') ? error.activeRun : null
  const racedOver = raced !== null && runs?.some((r) => r.id === raced.id && !isActive(r)) === true
  const active: RunSummary | null = runs?.find(isActive) ?? (racedOver ? null : raced)
  // a 409 without the run in its body still gets the notice, but the buttons stay usable
  const showActive = active !== null || (isApiError(error, 'run_active') && raced === null)
  const busy = active !== null || create.isPending
  const startingRepo = create.isPending && create.variables?.demo !== true

  const fieldError = invalid
    ? INVALID
    : isApiError(error, 'invalid_url') || isApiError(error, 'bad_request')
      ? error.detail
      : null
  // run_active has its own notice, and a network failure shows in the header's status line
  const failNotice =
    isApiError(error) && !['run_active', 'invalid_url', 'bad_request', 'network'].includes(error.code) ? error.detail : null

  const start = (body: CreateRunRequest) =>
    create.mutate(body, { onSuccess: (summary) => void navigate(`/runs/${encodeURIComponent(summary.id)}`) })

  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    if (noLive || busy) return
    const url = parseRepoUrl(repo)
    if (url === null) {
      setInvalid(true)
      document.getElementById('repo')?.focus()
      return
    }
    setInvalid(false)
    start({ github_url: url, ref: ref.trim() || null })
  }

  const editRepo = (value: string) => {
    setRepo(value)
    setInvalid(false)
    // the server's field error was about the old value
    if (isApiError(error, 'invalid_url') || isApiError(error, 'bad_request')) create.reset()
  }

  const notices = [
    noLive && demo && (
      <InlineNotice
        key="no-key"
        action={
          <Link to="/settings#api-key" className={noticeLink}>
            How to set it
          </Link>
        }
      >
        Live runs need an Anthropic API key on the server
      </InlineNotice>
    ),
    noLive && !demo && (
      <InlineNotice
        key="no-runs"
        action={
          <Link to="/settings#api-key" className={noticeLink}>
            Open settings
          </Link>
        }
      >
        This server can’t start runs yet
      </InlineNotice>
    ),
    showActive && (
      <InlineNotice
        key="active"
        action={
          active !== null && (
            <Link to={`/runs/${encodeURIComponent(active.id)}`} className={noticeLink}>
              Open it
            </Link>
          )
        }
      >
        Another run is in progress
      </InlineNotice>
    ),
    failNotice !== null && (
      <InlineNotice key="fail" tone="fail">
        {failNotice}
      </InlineNotice>
    ),
  ].filter(Boolean)

  return (
    <Page>
      <PageHeader title="New run" meta="Point it at a public GitHub repository, or try the bundled demo" />
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="min-w-0 space-y-6">
          {notices.length > 0 && <div className="space-y-3">{notices}</div>}
          <Card>
            <CardHeader
              title="Start from GitHub"
              description="It clones the repository to this machine and works on its own copy."
              action={<Icon name="github" size={20} className="text-ink-3" />}
            />
            <form className="space-y-5 border-t border-line px-5 py-5" onSubmit={submit} noValidate>
              <Field label="GitHub repository" htmlFor="repo" error={fieldError}>
                <Input
                  id="repo"
                  name="repo"
                  placeholder="https://github.com/owner/repo"
                  autoFocus
                  autoComplete="off"
                  spellCheck={false}
                  value={repo}
                  onChange={(e) => editRepo(e.target.value)}
                  disabled={noLive}
                  aria-invalid={fieldError !== null}
                  aria-describedby={fieldError !== null ? 'repo-error' : undefined}
                />
              </Field>
              <Field label="Branch or tag" htmlFor="ref" hint="Optional">
                <Input
                  id="ref"
                  name="ref"
                  placeholder="main"
                  autoComplete="off"
                  spellCheck={false}
                  value={ref}
                  onChange={(e) => setRef(e.target.value)}
                  disabled={noLive}
                  className="max-w-[240px]"
                />
              </Field>
              <div className="flex flex-wrap items-center justify-between gap-3 pt-1">
                <p className="text-sm text-ink-3">You confirm the functions before any rewriting starts.</p>
                <Button type="submit" variant="primary" disabled={noLive || busy}>
                  {startingRepo ? 'Starting' : 'Start run'}
                </Button>
              </div>
            </form>
          </Card>
          {demo && (
            <Card className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center">
              <span aria-hidden="true" className="grid size-10 shrink-0 place-items-center rounded-lg bg-accent-tint text-accent">
                <Icon name="flask" size={20} />
              </span>
              <div className="min-w-0 flex-1">
                <h2 className="text-md font-medium text-ink">Try the demo repository</h2>
                <p className="mt-0.5 text-sm text-ink-2">
                  A small bundled repository with 14 functions. Claude’s replies are written in advance, so it needs
                  no API key. The tests and benchmarks really run.
                </p>
              </div>
              <Button
                variant={noLive ? 'primary' : 'secondary'}
                disabled={busy}
                onClick={() => start({ demo: true })}
                className="self-start sm:self-auto"
              >
                Run the demo
              </Button>
            </Card>
          )}
        </div>
        <aside className="space-y-6">
          <Card>
            <CardHeader title="What happens next" />
            <ol className="space-y-4 px-5 pb-5">
              {NEXT.map((step, i) => (
                <li key={step.title} className="flex gap-3">
                  <span
                    aria-hidden="true"
                    className="grid size-7 shrink-0 place-items-center rounded-full border border-line bg-page text-xs font-medium text-ink-2"
                  >
                    {i + 1}
                  </span>
                  <span className="min-w-0">
                    <span className="block text-base font-medium text-ink">{step.title}</span>
                    <span className="block text-sm text-ink-3">{step.body}</span>
                  </span>
                </li>
              ))}
            </ol>
          </Card>
          {caps !== undefined && (
            <Card>
              <CardHeader title="This server" />
              <dl className="space-y-2.5 px-5 pb-5 text-sm">
                <div className="flex items-center justify-between gap-3">
                  <dt className="text-ink-2">Live runs</dt>
                  <dd>{noLive ? <Badge tone="caution">Needs an API key</Badge> : <Badge tone="success">Ready</Badge>}</dd>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <dt className="text-ink-2">Demo</dt>
                  <dd>{demo ? <Badge tone="success">Ready</Badge> : <Badge>Not in this build</Badge>}</dd>
                </div>
              </dl>
            </Card>
          )}
        </aside>
      </div>
    </Page>
  )
}
