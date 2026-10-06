// Recorded runs served as static files: public/replays/index.json lists them, each one an
// events.jsonl as the backend writes it.
import type { ReplayRef, RunEvent } from '../gen/events'
import { parseJsonl } from '../lib/jsonl'

export const REPLAY_INDEX_URL = '/replays/index.json'

export interface ReplayFile {
  events: RunEvent[]
  /** lines that were not JSON or not shaped like an event */
  skipped: number
}

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)

/**
 * The envelope fields the reducer reads before it dispatches on `type`. Payloads are trusted
 * to match their type, as they do on the live stream; unknown types pass so the reducer can
 * count them.
 */
export const isEventLike = (v: unknown): v is RunEvent =>
  isRecord(v) &&
  Number.isInteger(v.seq) &&
  typeof v.ts === 'number' &&
  typeof v.run_id === 'string' &&
  typeof v.type === 'string' &&
  isRecord(v.data)

export function parseReplay(text: string): ReplayFile {
  const { values, errors, partial } = parseJsonl(text)
  const events = values.filter(isEventLike)
  return { events, skipped: errors.length + (partial === null ? 0 : 1) + values.length - events.length }
}

async function fetchText(url: string, signal?: AbortSignal): Promise<string> {
  const res = await fetch(url, signal === undefined ? {} : { signal })
  if (!res.ok) throw new Error(`${url}: ${res.status} ${res.statusText}`.trim())
  // a single-page-app fallback answers a missing file with index.html and a 200
  if (res.headers.get('content-type')?.startsWith('text/html')) throw new Error(`${url}: not found`)
  return res.text()
}

export async function fetchReplay(src: string, signal?: AbortSignal): Promise<ReplayFile> {
  return parseReplay(await fetchText(src, signal))
}

export async function fetchReplayIndex(signal?: AbortSignal): Promise<ReplayRef[]> {
  const value: unknown = JSON.parse(await fetchText(REPLAY_INDEX_URL, signal))
  if (!Array.isArray(value)) throw new Error(`${REPLAY_INDEX_URL}: expected a list of replays`)
  return value.filter((r): r is ReplayRef => isRecord(r) && typeof r.id === 'string' && typeof r.src === 'string')
}
