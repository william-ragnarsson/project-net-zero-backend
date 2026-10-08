// What the database holds, from /api/history/status: the log, the read models folded from
// it, and how far the fold has got.
import type { ReactNode } from 'react'
import type { HistoryStatus, HistoryTable } from '../gen/events'
import { formatAgo, formatBytes, formatInt } from '../lib/format'
import { FAIL, NEON } from '../timeline/theme'

const ABOUT: Readonly<Record<string, string>> = {
  events: 'every event of every run, in order',
  streams: 'one per run, with its version',
  projects: 'one per repository',
  runs: 'one per run: state and totals',
  function_results: 'one per function per run',
}

function TableRow({ table }: { table: HistoryTable | undefined }) {
  if (table === undefined) return null
  return (
    <li className="flex items-baseline gap-3 py-2">
      <span className="font-mono text-sm text-white">{table.name}</span>
      <span className="min-w-0 flex-1 truncate text-xs text-muted">{ABOUT[table.name]}</span>
      <span className="shrink-0 font-mono text-xs tabular-nums text-gray-300">{formatInt(table.rows)}</span>
      <span className="w-14 shrink-0 text-right font-mono text-xs tabular-nums text-muted">
        {formatBytes(table.bytes)}
      </span>
    </li>
  )
}

function Group({ title, tag, children }: { title: string; tag: string; children: ReactNode }) {
  return (
    <section className="border-t border-dark-border pt-5">
      <h3 className="flex items-baseline justify-between gap-4">
        <span className="text-white">{title}</span>
        <span className="label">{tag}</span>
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
    <div className="space-y-6">
      <div className="flex items-start gap-3">
        <span
          aria-hidden="true"
          className="mt-1.5 size-2 shrink-0 rounded-full"
          style={{ backgroundColor: status.ok ? NEON : FAIL }}
        />
        <div className="min-w-0 text-sm">
          <p className="text-white">{status.detail}</p>
          <p className="mt-1 truncate font-mono text-xs text-muted">
            {status.database}
            {status.server_version !== null && <> · Postgres {status.server_version.split(' ')[0]}</>}
            {status.last_sync_ts !== null && <> · synced {formatAgo(status.last_sync_ts, now)}</>}
          </p>
        </div>
      </div>

      <Group title="The log" tag="source of truth">
        <ul className="mt-2">
          <TableRow table={table('events')} />
          <TableRow table={table('streams')} />
        </ul>
        <p className="mt-2 text-xs leading-relaxed text-muted">
          Append-only. Each event is kept as the exact line the run wrote to its{' '}
          <code className="font-mono text-gray-400">events.jsonl</code>, one stream per run, and a trigger refuses
          every <code className="font-mono text-gray-400">UPDATE</code>,{' '}
          <code className="font-mono text-gray-400">DELETE</code> and{' '}
          <code className="font-mono text-gray-400">TRUNCATE</code>.
        </p>
      </Group>

      <Group title="Read models" tag="folded from the log">
        <ul className="mt-2">
          <TableRow table={table('projects')} />
          <TableRow table={table('runs')} />
          <TableRow table={table('function_results')} />
        </ul>
        <div className="mt-3">
          <div className="h-1 bg-dark-border">
            <div className="h-full" style={{ width: `${folded * 100}%`, backgroundColor: behind > 0 ? '#5a5a5a' : NEON }} />
          </div>
          <p className="mt-2 font-mono text-xs text-muted">
            folded through event {formatInt(status.projected_position)} of {formatInt(status.head_position)}
            {behind > 0 && <span className="text-caution"> · {formatInt(behind)} to go</span>}
          </p>
        </div>
        <p className="mt-2 text-xs leading-relaxed text-muted">
          The tables these pages read. They hold nothing the log doesn't:{' '}
          <code className="font-mono text-gray-400">uv run netzero db rebuild</code> drops them and folds every event
          again.
        </p>
      </Group>

      {status.recent.length > 0 && (
        <Group title="Latest events" tag="from the log">
          <table className="mt-3 w-full table-fixed font-mono text-xs">
            <thead className="text-left text-[#5a5a5a]">
              <tr>
                <th className="w-14 pb-2 font-normal">#</th>
                <th className="pb-2 font-normal">type</th>
                <th className="w-12 pb-2 text-right font-normal">seq</th>
                <th className="w-16 pb-2 text-right font-normal">size</th>
              </tr>
            </thead>
            <tbody>
              {status.recent.map((event) => (
                <tr key={event.position} title={`${event.stream_id}, recorded ${formatAgo(event.recorded_at, now)}`}>
                  <td className="py-1 tabular-nums text-muted">{event.position}</td>
                  <td className="truncate py-1 text-gray-300">{event.type}</td>
                  <td className="py-1 text-right tabular-nums text-muted">{event.stream_seq}</td>
                  <td className="py-1 text-right tabular-nums text-muted">{formatBytes(event.bytes)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Group>
      )}
    </div>
  )
}
