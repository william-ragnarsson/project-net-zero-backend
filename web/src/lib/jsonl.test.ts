import { describe, expect, it } from 'vitest'
import { formatJsonl, parseJsonl } from './jsonl'

describe('parseJsonl', () => {
  it('parses one value per line and skips blank lines', () => {
    expect(parseJsonl('{"a":1}\n\n[2]\r\n"x"\n')).toEqual({ values: [{ a: 1 }, [2], 'x'], errors: [], partial: null })
  })

  it('reports bad lines with their line number instead of throwing', () => {
    const { values, errors } = parseJsonl('1\n{oops\n3\n')
    expect(values).toEqual([1, 3])
    expect(errors).toMatchObject([{ line: 2, text: '{oops' }])
  })

  it('returns an unterminated, unparseable last line as partial', () => {
    expect(parseJsonl('1\n{"seq":2,')).toEqual({ values: [1], errors: [], partial: '{"seq":2,' })
    expect(parseJsonl('1\n2')).toEqual({ values: [1, 2], errors: [], partial: null })
  })

  it('round-trips formatJsonl', () => {
    const values = [{ seq: 1, data: { lines: ['a\nb'] } }, null, 'text']
    const text = formatJsonl(values)
    expect(text.endsWith('\n')).toBe(true)
    expect(text.split('\n')).toHaveLength(values.length + 1)
    expect(parseJsonl(text).values).toEqual(values)
  })
})
