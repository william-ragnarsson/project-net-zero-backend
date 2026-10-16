import type { ReactNode } from 'react'
import { Card, CardHeader } from '../ui/Card'

const STEPS: readonly { title: string; command: string; note: ReactNode }[] = [
  {
    title: 'Start a database',
    command: 'make db',
    note: 'Postgres 17 on 127.0.0.1:54320, in Docker or Podman, or from a local install (brew install postgresql@17). A Postgres you already run works too.',
  },
  {
    title: 'Point net-zero at it, in .env',
    command: 'NETZERO_DATABASE_URL=postgresql://netzero:netzero@127.0.0.1:54320/netzero',
    note: '.env.example has the line.',
  },
  {
    title: 'Restart the server',
    command: 'make dev',
    note: 'It creates the tables, then copies every run in runs/ and each new event after that.',
  },
  {
    title: 'Optional: something to look at',
    command: 'uv run netzero db import web/public/replays/*.events.jsonl',
    note: 'Stores the bundled replays as a demo project.',
  },
]

/** How to turn history on, for a server running without a database. */
export function Setup() {
  return (
    <Card>
      <CardHeader title="Turn on history" description="Four steps, about a minute" />
      <ol className="divide-y divide-line border-t border-line">
        {STEPS.map((step, i) => (
          <li key={step.title} className="flex gap-3 px-5 py-4">
            <span
              aria-hidden="true"
              className="grid size-7 shrink-0 place-items-center rounded-full border border-line bg-page text-xs font-medium text-ink-2"
            >
              {i + 1}
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-base font-medium text-ink">{step.title}</p>
              <code className="mt-2 block overflow-x-auto whitespace-nowrap rounded-lg border border-line bg-sidebar px-3 py-2 font-mono text-code text-ink">
                {step.command}
              </code>
              <p className="mt-2 text-sm text-ink-3">{step.note}</p>
            </div>
          </li>
        ))}
      </ol>
    </Card>
  )
}
