// Writes the synthetic replay to public/replays and lists it in public/replays/index.json.
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { ReplayRef } from '../src/gen/events'
import { formatJsonl } from '../src/lib/jsonl'
import { buildSyntheticRun } from '../src/synth/scenario'

const replays = join(dirname(fileURLToPath(import.meta.url)), '..', 'public', 'replays')
const index = join(replays, 'index.json')

async function readIndex(): Promise<ReplayRef[]> {
  try {
    return JSON.parse(await readFile(index, 'utf8')) as ReplayRef[]
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === 'ENOENT') return []
    throw err
  }
}

const { events, ref } = buildSyntheticRun()
await mkdir(replays, { recursive: true })
await writeFile(join(replays, 'synthetic.events.jsonl'), formatJsonl(events))

// other replays (recorded runs) may share the index: replace ours, keep theirs
const refs = await readIndex()
const at = refs.findIndex((r) => r.id === ref.id)
if (at >= 0) refs[at] = ref
else refs.push(ref)
await writeFile(index, JSON.stringify(refs, null, 2) + '\n')

console.log(`wrote ${events.length} events (${ref.duration_ms} ms of run time) to ${ref.src}`)
