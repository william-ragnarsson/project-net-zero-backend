// A small Python highlighter for code in tree cards: a regex tokenizer, no grammar. React
// renders the tokens as spans; joining every token's text gives the source back exactly.

/** k keyword, f name after def or class, b builtin, i other name, s string, d triple-quoted
 * string, n number, m comment, o anything else; null is leading whitespace */
export type TokenClass = 'k' | 'f' | 'b' | 'i' | 's' | 'd' | 'n' | 'm' | 'o'

export interface Token {
  cls: TokenClass | null
  text: string
}

/** One source line as tokens. */
export type Line = Token[]

const KEYWORDS = new Set(
  'False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield match case'.split(
    ' ',
  ),
)
const BUILTINS = new Set(
  'abs all any bool bytes dict enumerate filter float frozenset getattr hasattr int isinstance iter len list map max min next object print range repr reversed round set slice sorted str sum super tuple type zip self cls ValueError TypeError KeyError IndexError Exception heapq itertools time re math'.split(
    ' ',
  ),
)

// groups: 1 string, 2 comment, 3 number, 4 name, 5 newline, 6 blanks, 7 any other character
const TOKEN =
  /([rRbBuUfF]{0,2}(?:"""[\s\S]*?(?:"""|$)|'''[\s\S]*?(?:'''|$)|"(?:\\.|[^"\\\n])*"?|'(?:\\.|[^'\\\n])*'?))|(#[^\n]*)|(\b\d[\d_]*(?:\.\d+)?(?:[eE][-+]?\d+)?\b)|([A-Za-z_][A-Za-z0-9_]*)|(\n)|([ \t]+)|([\s\S])/g

const TRIPLE = /^[rRbBuUfF]{0,2}("""|''')/

export function highlightPython(src: string): Line[] {
  const lines: Line[] = [[]]
  let cur: Line = lines[0] ?? []
  let prevName: string | null = null
  const push = (cls: TokenClass | null, text: string) => {
    if (text === '') return
    const last = cur.at(-1)
    // keep runs of plain text together, so a line has as few spans as it needs
    if (cls === null && last !== undefined && last.cls === null) last.text += text
    else cur.push({ cls, text })
  }
  for (const m of src.matchAll(TOKEN)) {
    const text = m[0]
    if (m[5] !== undefined) {
      cur = []
      lines.push(cur)
      continue
    }
    if (m[6] !== undefined) {
      push(null, text)
      continue
    }
    let cls: TokenClass
    if (m[1] !== undefined) cls = TRIPLE.test(text) ? 'd' : 's'
    else if (m[2] !== undefined) cls = 'm'
    else if (m[3] !== undefined) cls = 'n'
    else if (m[4] !== undefined)
      cls = KEYWORDS.has(text) ? 'k' : prevName === 'def' || prevName === 'class' ? 'f' : BUILTINS.has(text) ? 'b' : 'i'
    else cls = 'o'
    prevName = m[4] !== undefined ? text : null
    // a token that spans lines (a docstring) is split, and each part keeps its indent plain
    text.split('\n').forEach((part, j) => {
      if (j > 0) {
        cur = []
        lines.push(cur)
      }
      const indent = /^[ \t]*/.exec(part)?.[0] ?? ''
      push(null, indent)
      push(cls, part.slice(indent.length))
    })
  }
  return lines
}

/** The width of a line's leading indent, in characters: wrapped lines hang past it. */
export const lineIndent = (line: Line): number => {
  const first = line[0]
  return first !== undefined && first.cls === null ? (/^[ \t]*/.exec(first.text)?.[0].length ?? 0) : 0
}

export const lineText = (line: Line): string => line.map((t) => t.text).join('')
