// /new: one form that starts a run on a GitHub repository, or on the bundled demo.
import { useEffect, useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router'
import { isApiError } from '../api/client'
import { useCapabilities, useCreateRun, useRuns } from '../api/queries'
import { PageHeader } from '../components/PageHeader'
import type { CreateRunRequest, RunSummary } from '../gen/events'
import { isActive, parseRepoUrl } from '../lib/runs'
import { useTitle } from '../lib/useTitle'
import { Button, buttonClass } from '../ui/Button'
import { Field } from '../ui/Field'
import { InlineNotice } from '../ui/InlineNotice'
import { Input } from '../ui/Input'
import { Page } from '../ui/Page'

const INVALID = 'Enter a GitHub repository URL, like https://github.com/owner/repo'

const noticeLink = buttonClass('secondary', 'sm')

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
      <PageHeader title="New run" />
      {notices.length > 0 && <div className="mb-6 max-w-[520px] space-y-3">{notices}</div>}
      <form className="max-w-[520px] space-y-5" onSubmit={submit} noValidate>
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
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" variant="primary" disabled={noLive || busy}>
            {startingRepo ? 'Starting' : 'Start run'}
          </Button>
          {demo && (
            <Button variant={noLive ? 'primary' : 'quiet'} disabled={busy} onClick={() => start({ demo: true })}>
              {noLive ? 'Run the demo' : 'Try the demo repository instead'}
            </Button>
          )}
        </div>
      </form>
    </Page>
  )
}
