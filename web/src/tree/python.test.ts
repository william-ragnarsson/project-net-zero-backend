import { describe, expect, it } from 'vitest'
import { highlightPython, lineIndent, lineText, type Line } from './python'

const classes = (line: Line | undefined) => (line ?? []).filter((t) => t.cls !== null).map((t) => `${t.cls}:${t.text}`)

describe('highlightPython', () => {
  it('marks keywords, the defined name, builtins and other names', () => {
    const [line] = highlightPython('def dedupe(items):')
    expect(classes(line)).toEqual(['k:def', 'f:dedupe', 'o:(', 'i:items', 'o:)', 'o::'])
    expect(classes(highlightPython('return len(set(xs))')[0])).toEqual([
      'k:return',
      'b:len',
      'o:(',
      'b:set',
      'o:(',
      'i:xs',
      'o:)',
      'o:)',
    ])
  })

  it('keeps a # inside a string out of comments', () => {
    expect(classes(highlightPython('x = "a#b"  # note')[0])).toEqual(['i:x', 'o:=', 's:"a#b"', 'm:# note'])
  })

  it('splits a triple-quoted docstring across lines and keeps each indent plain', () => {
    const lines = highlightPython('def f():\n    """One.\n\n    Two."""\n    return 1')
    expect(lines).toHaveLength(5)
    expect(lines[1]).toEqual([
      { cls: null, text: '    ' },
      { cls: 'd', text: '"""One.' },
    ])
    expect(lines[2]).toEqual([])
    expect(lines[3]).toEqual([
      { cls: null, text: '    ' },
      { cls: 'd', text: 'Two."""' },
    ])
    expect(classes(lines[4])).toEqual(['k:return', 'n:1'])
  })

  it('reads numbers, prefixed strings and comments', () => {
    expect(classes(highlightPython("n = 1_000 + 2.5e-3 + f'{x}'  # c")[0])).toEqual([
      'i:n',
      'o:=',
      'n:1_000',
      'o:+',
      'n:2.5e-3',
      'o:+',
      "s:f'{x}'",
      'm:# c',
    ])
  })

  it('round-trips the source exactly', () => {
    const src = 'class A:\n\tdef run(self, k=3):  # go\n\t\treturn [x for x in self.xs if x > k]\n\n\'\'\'open'
    expect(highlightPython(src).map(lineText).join('\n')).toBe(src)
  })

  it('measures the leading indent of a line', () => {
    const lines = highlightPython('if x:\n        y = 1\n')
    expect(lineIndent(lines[0] ?? [])).toBe(0)
    expect(lineIndent(lines[1] ?? [])).toBe(8)
  })
})
