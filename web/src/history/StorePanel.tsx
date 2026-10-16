// What the database holds, from /api/history/status: the log, the read models folded from
// it, and how far the fold has got. It sits in a card, under the card's header.
import type { ReactNode } from 'react'
import type { HistoryStatus, HistoryTable } from '../gen/events'
import { formatAgo, formatBytes, formatInt } from '../lib/format'

const ABOUT: Readonly<Record<string, string>> = {
  events: 'every event of every run, in order',
  streams: 'one per run, with its version',
  projects: 'one per repository',
  runs: 'one per run: state and totals',
  function_results: 'one per function per run',
}

const Code = ({ children }: { children: ReactNode }) => <code className="font-mono text-code text-ink-2">{children}</code>

function TableRow({ table }: { table: HistoryTable | undefined }) {
  if (table === undefined) return null
  return (
    <li className="flex items-baseline gap-3 py-1.5">
      <span className="font-mono text-code text-ink">{table.name}</span>
      <span className="min-w-0 flex-1 truncate text-xs text-ink-3">{ABOUT[table.name]}</span>
      <span className="shrink-0 text-sm tabular-nums text-ink-2">{formatInt(table.rows)}</span>
      <span className="w-14 shrink-0 text-right text-xs tabular-nums text-ink-3">{formatBytes(table.bytes)}</span>
    </li>
  )
}

function Group({ title, tag, children }: { title: string; tag: string; children: ReactNode }) {
  return (
    <section className="border-t border-line px-5 py-4">
      <h3 className="flex items-baseline justify-between gap-4">
        <span className="text-base font-medium text-ink">{title}</span>
        <span className="text-xs text-ink-3">{tag}</span>
      </h3>
      {children}
    </section>
  )
}

export function StorePanel({ status, now }: { status: HistoryStatus; now: number }) {
  const table = (name: string) => status.tables.find((t) => t.name === name)
  const behind = status.head_position - status.projected_position
  const folded = status.head_position === 0 ? 1 : status.projected_position / status.head_position

  return (
    <div>
      <div className="flex items-start gap-3 border-t border-line px-5 py-4">
        <span
          aria-hidden="true"
          className={`mt-1.5 size-2 shrink-0 rounded-full ${status.ok ? 'bg-accent' : 'bg-fail'}`}
        />
        <div className="min-w-0 text-sm">
          <p className="text-ink">{status.detail}</p>
          <p className="mt-0.5 truncate text-xs text-ink-3">
            <span className="font-mono">{status.database}</span>
            {status.server_version !== null && <> · Postgres {status.server_version.split(' ')[0]}</>}
            {status.last_sync_ts !== null && <> · synced {formatAgo(status.last_sync_ts, now)}</>}
          </p>
        </div>
      </div>

      <Group title="The log" tag="Source of truth">
        <ul className="mt-2">
          <TableRow table={table('events')} />
          <TableRow table={table('streams')} />
        </ul>
        <p className="mt-2 text-xs leading-relaxed text-ink-3">
          Append-only. Each event is kept as the exact line the run wrote to its <Code>events.jsonl</Code>, one stream
          per run, and a trigger refuses every <Code>UPDATE</Code>, <Code>DELETE</Code> and <Code>TRUNCATE</Code>.
        </p>
      </Group>

      <Group title="Read models" tag="Folded from the log">
        <ul className="mt-2">
          <TableRow table={table('projects')} />
          <TableRow table={table('runs')} />
          <TableRow table={table('function_results')} />
        </ul>
        <div className="mt-3">
          <div className="h-1.5 overflow-hidden rounded-full bg-hover">
            <div
              className={`h-full rounded-full ${behind > 0 ? 'bg-control-strong' : 'bg-accent'}`}
              style={{ width: `${folded * 100}%` }}
            />
          </div>
          <p className="mt-2 text-xs tabular-nums text-ink-3">
            Folded through event {formatInt(status.projected_position)} of {formatInt(status.head_position)}
            {behind > 0 && <span className="text-caution"> · {formatInt(behind)} to go</span>}
          </p>
        </div>
        <p className="mt-2 text-xs leading-relaxed text-ink-3">
          The tables these pages read. They hold nothing the log doesn’t: <Code>uv run netzero db rebuild</Code> drops
          them and folds every event again.
        </p>
      </Group>

      {status.recent.length > 0 && (
        <Group title="Latest events" tag="From the log">
          <table className="mt-2 w-full table-fixed text-xs">
            <thead className="text-left text-ink-3">
              <tr>
                <th className="w-14 pb-1.5 font-normal">#</th>
                <th className="pb-1.5 font-normal">Type</th>
                <th className="w-12 pb-1.5 text-right font-normal">Seq</th>
                <th className="w-16 pb-1.5 text-right font-normal">Size</th>
              </tr>
            </thead>
            <tbody>
              {status.recent.map((event) => (
                <tr key={event.position} title={`${event.stream_id}, recorded ${formatAgo(event.recorded_at, now)}`}>
                  <td className="py-1 tabular-nums text-ink-3">{event.position}</td>
                  <td className="truncate py-1 font-mono text-code text-ink-2">{event.type}</td>
                  <td className="py-1 text-right tabular-nums text-ink-3">{event.stream_seq}</td>
                  <td className="py-1 text-right tabular-nums text-ink-3">{formatBytes(event.bytes)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Group>
      )}
    </div>
  )
}
