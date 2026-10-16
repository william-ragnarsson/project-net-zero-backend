// Unified diffs as rows for tree cards: the whole diff for a pinned card, and a compact view
// (the changed lines with one line of context) for a live card.
import { highlightPython, type Line } from './python'

export interface ParsedRow {
  kind: 'ctx' | 'add' | 'del' | 'hunk'
  old: number | null
  new: number | null
  /** the line without its +, - or space prefix */
  text: string
}

export interface ParsedDiff {
  rows: ParsedRow[]
  adds: number
  dels: number
}

export function parseDiff(diff: string): ParsedDiff {
  const rows: ParsedRow[] = []
  let o = 0
  let n = 0
  let adds = 0
  let dels = 0
  for (const raw of diff.replace(/\n$/, '').split('\n')) {
    if (/^(diff |--- |\+\+\+ |index )/.test(raw)) continue
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(raw)
    if (hunk !== null) {
      o = Number(hunk[1])
      n = Number(hunk[2])
      rows.push({ kind: 'hunk', old: null, new: null, text: '' })
      continue
    }
    const sign = raw[0]
    const text = raw.slice(1)
    if (sign === '+') {
      rows.push({ kind: 'add', old: null, new: n++, text })
      adds++
    } else if (sign === '-') {
      rows.push({ kind: 'del', old: o++, new: null, text })
      dels++
    } else rows.push({ kind: 'ctx', old: o++, new: n++, text })
  }
  return { rows, adds, dels }
}

/** A row as a card draws it: a gap row stands for skipped lines. */
export interface DiffRow {
  kind: 'ctx' | 'add' | 'del' | 'gap'
  /** the old line number for a removed line, else the new one */
  line: number | null
  tokens: Line
}

const isBlankCtx = (r: ParsedRow | undefined): boolean => r !== undefined && r.kind === 'ctx' && r.text.trim() === ''

const lineOf = (r: ParsedRow): number | null => (r.kind === 'del' ? r.old : r.new)

/** Every row of the diff, without hunk headers or blank context at either end. */
export function fullDiffRows(diff: string | null | undefined): DiffRow[] {
  if (!diff) return []
  const rows = parseDiff(diff).rows.filter((r) => r.kind !== 'hunk')
  while (isBlankCtx(rows[0])) rows.shift()
  while (isBlankCtx(rows.at(-1))) rows.pop()
  return rows.map((r) => ({
    kind: r.kind as DiffRow['kind'],
    line: lineOf(r),
    tokens: highlightPython(r.text)[0] ?? [],
  }))
}

/**
 * The changed lines with one context line either side, trimmed of blank ends and dedented by
 * their common indent. A gap row marks each place where lines were left out.
 */
export function compactDiff(diff: string | null | undefined): DiffRow[] {
  if (!diff) return []
  const rows = parseDiff(diff).rows
  const keep = new Set<number>()
  rows.forEach((r, j) => {
    if (r.kind !== 'add' && r.kind !== 'del') return
    keep.add(j)
    for (const k of [j - 1, j + 1]) if (rows[k]?.kind === 'ctx') keep.add(k)
  })
  const idx = [...keep].sort((a, b) => a - b)
  while (idx.length > 0 && isBlankCtx(rows[idx[0] ?? -1])) idx.shift()
  while (idx.length > 0 && isBlankCtx(rows[idx.at(-1) ?? -1])) idx.pop()
  const sel = idx.map((j) => rows[j]).filter((r): r is ParsedRow => r !== undefined)
  const indents = sel.filter((r) => r.text.trim() !== '').map((r) => /^\s*/.exec(r.text)?.[0].length ?? 0)
  const cut = indents.length > 0 ? Math.min(...indents) : 0
  const lines = highlightPython(sel.map((r) => r.text.slice(cut)).join('\n'))
  const out: DiffRow[] = []
  idx.forEach((j, q) => {
    const prev = idx[q - 1]
    if (prev !== undefined && j !== prev + 1) out.push({ kind: 'gap', line: null, tokens: [] })
    const r = sel[q]
    if (r === undefined) return
    out.push({ kind: r.kind as DiffRow['kind'], line: lineOf(r), tokens: lines[q] ?? [] })
  })
  return out
}
