import type { ReactNode } from 'react'

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
    <ol className="grid gap-px border border-dark-border bg-dark-border md:grid-cols-2">
      {STEPS.map((step, i) => (
        <li key={step.title} className="bg-dark p-6 md:p-8">
          <p className="label">Step {i + 1}</p>
          <p className="mt-3 text-white">{step.title}</p>
          <code className="mt-4 block overflow-x-auto whitespace-nowrap border border-dark-border bg-[#0d0d0d] px-3 py-2 font-mono text-xs text-neon">
            {step.command}
          </code>
          <p className="mt-3 text-sm leading-relaxed text-muted">{step.note}</p>
        </li>
      ))}
    </ol>
  )
}
