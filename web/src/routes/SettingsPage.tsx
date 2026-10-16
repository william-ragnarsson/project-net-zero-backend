// /settings: read-only facts the server reports about itself (GET /api/capabilities).
import { useEffect, type ReactNode } from 'react'
import { useLocation } from 'react-router'
import { useCapabilities } from '../api/queries'
import { Co2 } from '../components/Co2'
import { PageHeader } from '../components/PageHeader'
import type { Capabilities } from '../gen/events'
import { NONE } from '../lib/format'
import { useTitle } from '../lib/useTitle'
import { Badge } from '../ui/Badge'
import { Button } from '../ui/Button'
import { Card, CardHeader } from '../ui/Card'
import { Delayed } from '../ui/Delayed'
import { Icon, type IconName } from '../ui/Icon'
import { InlineNotice } from '../ui/InlineNotice'
import { Page } from '../ui/Page'

const METHOD = { measured: 'Measured', calibrated: 'Calibrated', estimated: 'Estimated' } as const

export function SettingsPage() {
  useTitle('Settings')
  const caps = useCapabilities()
  const { hash } = useLocation()
  const loaded = caps.data !== undefined

  // "How to set it" links to #api-key, which exists only once the data is in
  useEffect(() => {
    if (loaded && hash !== '') document.getElementById(decodeURIComponent(hash.slice(1)))?.scrollIntoView()
  }, [loaded, hash])

  return (
    <Page>
      <PageHeader title="Settings" meta="Read-only values from the server’s environment" />
      {caps.data !== undefined ? (
        <Facts caps={caps.data} />
      ) : caps.isError ? (
        <InlineNotice
          tone="fail"
          action={
            <Button size="sm" onClick={() => void caps.refetch()}>
              Retry
            </Button>
          }
        >
          Can’t load the server settings
        </InlineNotice>
      ) : (
        <Delayed>
          <p className="text-base text-ink-3">Loading</p>
        </Delayed>
      )}
    </Page>
  )
}

function Facts({ caps }: { caps: Capabilities }) {
  const { models, power } = caps
  return (
    <div className="grid items-start gap-6 lg:grid-cols-2">
      <Section title="Server" icon="server" description="What this server runs on">
        <Row label="Version">{caps.version}</Row>
        <Row label="Platform">{caps.platform}</Row>
        <Row label="Python">{caps.python}</Row>
        <Row label="Git">
          <Tool installed={caps.tools.git} />
        </Row>
        <Row label="uv">
          <Tool installed={caps.tools.uv} />
        </Row>
        <Row label="Demo repository">
          {caps.demo_available ? <Badge tone="success">Available</Badge> : <Badge>Not available</Badge>}
        </Row>
      </Section>

      <Section title="API key" id="api-key" icon="key" description="Live runs call Claude with it">
        <Row label="Anthropic API key">
          {caps.has_api_key ? (
            <Badge tone="success">Set</Badge>
          ) : caps.modes.includes('live') ? (
            // the fake pipeline reports no key but still runs live
            'Not needed, the server runs a scripted pipeline'
          ) : (
            <>
              <Badge tone="caution">Not set</Badge>
              <p className="mt-2 text-sm text-ink-2">
                Set <code className="font-mono text-code">ANTHROPIC_API_KEY</code> in the server’s environment or{' '}
                <code className="font-mono text-code">.env</code> file and restart it.
              </p>
            </>
          )}
        </Row>
      </Section>

      <Section title="Models" icon="code" description="The Claude model for each step">
        <Row label="Triage">{models.triage ?? NONE}</Row>
        <Row label="Tests">{models.tests ?? NONE}</Row>
        <Row label="Test repair">{models.tests_repair ?? NONE}</Row>
        <Row label="Rewrite">{models.rewrite ?? NONE}</Row>
        <Row label="Rewrite repair">{models.rewrite_repair ?? NONE}</Row>
      </Section>

      <Section title="Measurement" icon="gauge" description={
          <>
            How <Co2 />e per call is worked out here
          </>
        }>
        {power === null ? (
          <Row label="Power">Not measured yet</Row>
        ) : (
          <>
            <Row label="Method">{METHOD[power.badge]}</Row>
            <Row label="CPU">{power.cpu_model}</Row>
            <Row label="CPU power">{power.p_core_w.toFixed(1)} W</Row>
            <Row label="Grid">
              {Math.round(power.grid.kg_per_kwh * 1000)} g <Co2 />
              e/kWh ({power.grid.country_iso})
            </Row>
          </>
        )}
      </Section>

      <Section title="Keyboard" icon="keyboard">
        <Row label={<Kbd>n</Kbd>}>New run</Row>
        <Row label={<Kbd>/</Kbd>}>Filter runs</Row>
        <Row label={<Kbd>Space</Kbd>}>Play or pause a replay</Row>
        <Row
          label={
            <>
              <Kbd>Left</Kbd> and <Kbd>Right</Kbd>
            </>
          }
        >
          Step through a replay
        </Row>
        <Row label={<Kbd>Esc</Kbd>}>Close or unpin</Row>
      </Section>
    </div>
  )
}

function Section({
  title,
  icon,
  description,
  id,
  children,
}: {
  title: string
  icon: IconName
  description?: ReactNode
  id?: string
  children: ReactNode
}) {
  return (
    <Card id={id} className="scroll-mt-6">
      <CardHeader
        title={
          <span className="flex items-center gap-2.5">
            <span aria-hidden="true" className="grid size-7 place-items-center rounded-md bg-hover text-ink-2">
              <Icon name={icon} />
            </span>
            {title}
          </span>
        }
        description={description}
      />
      <dl className="divide-y divide-line border-t border-line text-base">{children}</dl>
    </Card>
  )
}

function Row({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[112px_minmax(0,1fr)] items-baseline gap-x-4 px-5 py-2.5 sm:grid-cols-[150px_minmax(0,1fr)]">
      <dt className="text-sm text-ink-2">{label}</dt>
      <dd className="min-w-0 break-words text-ink">{children}</dd>
    </div>
  )
}

function Tool({ installed }: { installed: boolean }) {
  return installed ? <Badge tone="success">Installed</Badge> : <Badge tone="fail">Missing</Badge>
}

function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="rounded-sm border border-control bg-surface px-1.5 font-sans text-xs">{children}</kbd>
}
