import { describe, expect, it } from 'vitest'
import { eventsUrl } from '../api/client'
import { SHOWCASE, showcasePath } from './config'

const ID = '20260101-000000-demo'

describe('showcasePath', () => {
  it('maps the reads the showcase answers to the files netzero showcase writes', () => {
    expect(showcasePath('/api/health')).toBe('/showcase/health.json')
    expect(showcasePath('/api/runs')).toBe('/showcase/runs.json')
    expect(showcasePath(`/api/runs/${ID}`)).toBe(`/showcase/runs/${ID}/run.json`)
    expect(showcasePath(`/api/runs/${ID}/events.jsonl`)).toBe(`/showcase/runs/${ID}/events.jsonl`)
  })

  it('answers nothing else', () => {
    for (const path of [
      '/api/capabilities',
      `/api/runs/${ID}/events?after=0`,
      `/api/runs/${ID}/select`,
      `/api/runs/${ID}/artifacts/patch`,
      '/api/runs/..%2F..%2Fsecret',
      '/api/runs/../run.json',
      '/api/history/projects',
    ]) {
      expect(showcasePath(path), path).toBeNull()
    }
  })

  it('is off in the normal build, which the tests run as', () => {
    expect(SHOWCASE).toBe(false)
    expect(eventsUrl(ID)).toBe(`/api/runs/${ID}/events.jsonl`)
  })
})
