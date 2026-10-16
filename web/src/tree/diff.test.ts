import { describe, expect, it } from 'vitest'
import { compactDiff, fullDiffRows, parseDiff, type DiffRow } from './diff'
import { lineText } from './python'

const DIFF = `--- a/textkit/dedupe.py
+++ b/textkit/dedupe.py
@@ -10,12 +10,9 @@ def dedupe_preserve_order(items):
 
     seen = []
     out = []
-    for item in items:
-        if item not in seen:
-            seen.append(item)
-            out.append(item)
+    return list(dict.fromkeys(items))
     # keep going
     a = 1
     b = 2
     c = 3
-    return out
+    return None
 
`

const show = (rows: DiffRow[]) => rows.map((r) => `${r.kind} ${r.line ?? ''} ${lineText(r.tokens)}`)

describe('parseDiff', () => {
  it('numbers old and new lines from the hunk header and skips the file headers', () => {
    const { rows, adds, dels } = parseDiff(DIFF)
    expect(adds).toBe(2)
    expect(dels).toBe(5)
    expect(rows[0]).toEqual({ kind: 'hunk', old: null, new: null, text: '' })
    expect(rows[1]).toEqual({ kind: 'ctx', old: 10, new: 10, text: '' })
    expect(rows.find((r) => r.kind === 'del')).toMatchObject({ old: 13, new: null })
    expect(rows.find((r) => r.kind === 'add')).toMatchObject({ old: null, new: 13 })
  })
})

describe('fullDiffRows', () => {
  it('keeps every row but the hunk header and blank context at either end', () => {
    const rows = fullDiffRows(DIFF)
    expect(rows[0]).toMatchObject({ kind: 'ctx', line: 11 })
    expect(rows.at(-1)).toMatchObject({ kind: 'add', line: 18 })
    expect(rows.filter((r) => r.kind === 'gap')).toEqual([])
    expect(fullDiffRows(null)).toEqual([])
  })
})

describe('compactDiff', () => {
  it('keeps one line of context, marks the gap and dedents by the smallest indent', () => {
    expect(show(compactDiff(DIFF))).toEqual([
      'ctx 12 out = []',
      'del 13 for item in items:',
      'del 14     if item not in seen:',
      'del 15         seen.append(item)',
      'del 16         out.append(item)',
      'add 13 return list(dict.fromkeys(items))',
      'ctx 14 # keep going',
      'gap  ',
      'ctx 17 c = 3',
      'del 21 return out',
      'add 18 return None',
    ])
  })

  it('trims blank context at both ends', () => {
    const diff = '@@ -1,3 +1,3 @@\n \n-x = 1\n+x = 2\n \n'
    expect(show(compactDiff(diff))).toEqual(['del 2 x = 1', 'add 2 x = 2'])
  })

  it('highlights the dedented code', () => {
    const [first] = compactDiff('@@ -1,1 +1,1 @@\n-    return out\n+    return None\n')
    expect(first?.tokens).toEqual([
      { cls: 'k', text: 'return' },
      { cls: null, text: ' ' },
      { cls: 'i', text: 'out' },
    ])
  })

  it('is empty without a diff', () => {
    expect(compactDiff('')).toEqual([])
  })
})
