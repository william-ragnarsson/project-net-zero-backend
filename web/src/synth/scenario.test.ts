import { describe, expect, it } from 'vitest'
import index from '../../public/replays/index.json'
import committed from '../../public/replays/synthetic.events.jsonl?raw'
import { formatJsonl } from '../lib/jsonl'
import { buildSyntheticRun } from './scenario'

describe('synthetic replay', () => {
  it('is what `npm run synth` writes from the current scenario', () => {
    const run = buildSyntheticRun()
    expect(committed).toBe(formatJsonl(run.events))
    expect(index).toContainEqual(run.ref)
  })

  it('is deterministic', () => {
    expect(buildSyntheticRun().events).toEqual(buildSyntheticRun().events)
  })
})
