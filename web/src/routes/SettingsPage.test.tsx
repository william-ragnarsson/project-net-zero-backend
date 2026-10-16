// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Capabilities } from '../gen/events'
import { SettingsPage } from './SettingsPage'

const CAPS: Capabilities = {
  version: '0.2.0',
  has_api_key: false,
  modes: ['live', 'demo'],
  active_run: null,
  platform: 'darwin-arm64',
  python: '3.12.15',
  tools: { git: true, uv: false },
  power: null,
  models: { triage: 'claude-haiku-4-5' },
  demo_available: true,
}

function renderSettings(caps: Capabilities) {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(Response.json(caps))))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  client.setQueryData(['capabilities'], caps)
  client.setQueryData(['health'], { ok: true, version: '0.2.0' })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

/** The value next to a label in the definition lists. */
const valueOf = (label: string) => screen.getByText(label, { selector: 'dt' }).nextElementSibling?.textContent

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('SettingsPage', () => {
  it('reports the server as plain text', () => {
    renderSettings(CAPS)
    expect(valueOf('Version')).toBe('0.2.0')
    expect(valueOf('Git')).toBe('Installed')
    expect(screen.getByText('Missing').className).toContain('text-fail')
    expect(valueOf('Demo repository')).toBe('Available')
    expect(valueOf('Triage')).toBe('claude-haiku-4-5')
    expect(valueOf('Rewrite')).toBe('·')
    expect(valueOf('Power')).toBe('Not measured yet')
  })

  it('explains a missing key only when live runs need one', () => {
    renderSettings(CAPS)
    // the fake pipeline runs live without a key
    expect(valueOf('Anthropic API key')).toBe('Not needed, the server runs a scripted pipeline')
    cleanup()

    renderSettings({ ...CAPS, modes: ['demo'] })
    expect(valueOf('Anthropic API key')).toContain('Not set')
    expect(document.getElementById('api-key')?.textContent).toContain('ANTHROPIC_API_KEY')
  })

  it('shows the power probe when there is one', () => {
    renderSettings({
      ...CAPS,
      power: {
        power_source: 'tdp_estimate',
        badge: 'calibrated',
        method: 'codecarbon_model',
        cpu_model: 'Apple M3',
        tdp_w: 22,
        cpu_count: 8,
        p_core_w: 3.456,
        p_ram_w: 1,
        grid: { country_iso: 'SWE', kg_per_kwh: 0.0412, source: 'codecarbon' },
        notes: [],
      },
    })
    expect(valueOf('Method')).toBe('Calibrated')
    expect(valueOf('CPU power')).toBe('3.5 W')
    expect(valueOf('Grid')).toBe('41 g CO2e/kWh (SWE)')
  })
})
