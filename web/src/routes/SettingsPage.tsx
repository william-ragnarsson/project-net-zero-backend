// /settings: read-only facts the server reports about itself (GET /api/capabilities).
import { useEffect, type ReactNode } from 'react'
import { useLocation } from 'react-router'
import { useCapabilities } from '../api/queries'
import { Co2 } from '../components/Co2'
import { PageHeader } from '../components/PageHeader'
import type { Capabilities } from '../gen/events'
import { NONE } from '../lib/format'
import { useTitle } from '../lib/useTitle'
import { Button } from '../ui/Button'
import { Delayed } from '../ui/Delayed'
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
    <div className="space-y-8">
      <Section title="Server">
        <Row label="Version">{caps.version}</Row>
        <Row label="Platform">{caps.platform}</Row>
        <Row label="Python">{caps.python}</Row>
        <Row label="Git">
          <Tool installed={caps.tools.git} />
        </Row>
        <Row label="uv">
          <Tool installed={caps.tools.uv} />
        </Row>
        <Row label="Demo repository">{caps.demo_available ? 'Available' : 'Not available'}</Row>
      </Section>

      <Section title="API key" id="api-key">
        <Row label="Anthropic API key">
          {caps.has_api_key ? (
            'Set'
          ) : caps.modes.includes('live') ? (
            // the fake pipeline reports no key but still runs live
            'Not needed, the server runs a scripted pipeline'
          ) : (
            <>
              Not set
              <p className="mt-1 text-sm text-ink-2">
                Set <code className="font-mono text-code">ANTHROPIC_API_KEY</code> in the server’s environment or{' '}
                <code className="font-mono text-code">.env</code> file and restart it.
              </p>
            </>
          )}
        </Row>
      </Section>

      <Section title="Models">
        <Row label="Triage">{models.triage ?? NONE}</Row>
        <Row label="Tests">{models.tests ?? NONE}</Row>
        <Row label="Test repair">{models.tests_repair ?? NONE}</Row>
        <Row label="Rewrite">{models.rewrite ?? NONE}</Row>
        <Row label="Rewrite repair">{models.rewrite_repair ?? NONE}</Row>
      </Section>

      <Section title="Measurement">
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

      <Section title="Keyboard">
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

function Section({ title, id, children }: { title: string; id?: string; children: ReactNode }) {
  return (
    <section id={id}>
      <h2 className="mb-3 text-md font-medium text-ink">{title}</h2>
      <dl className="grid grid-cols-[128px_1fr] gap-x-6 gap-y-2 text-base sm:grid-cols-[200px_1fr]">{children}</dl>
    </section>
  )
}

function Row({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <>
      <dt className="text-ink-2">{label}</dt>
      <dd className="min-w-0 break-words text-ink">{children}</dd>
    </>
  )
}

function Tool({ installed }: { installed: boolean }) {
  return installed ? 'Installed' : <span className="text-fail">Missing</span>
}

function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="rounded-sm border border-control bg-surface px-1.5 font-sans text-xs">{children}</kbd>
}
