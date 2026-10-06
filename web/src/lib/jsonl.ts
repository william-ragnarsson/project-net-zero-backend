export interface JsonlError {
  /** 1-based line number in the input. */
  line: number
  text: string
  message: string
}

export interface JsonlResult {
  values: unknown[]
  errors: JsonlError[]
  /** An unterminated last line that does not parse yet: the head of a line still being written. */
  partial: string | null
}

/**
 * Parses newline-delimited JSON. Blank lines are skipped and bad lines are reported, not
 * thrown. A last line without a newline that fails to parse is returned as `partial`
 * instead of an error, so a file read while it is being appended to stays usable.
 */
export function parseJsonl(text: string): JsonlResult {
  const lines = text.split('\n')
  const terminated = text.endsWith('\n')
  const values: unknown[] = []
  const errors: JsonlError[] = []
  let partial: string | null = null

  lines.forEach((raw, i) => {
    const line = raw.endsWith('\r') ? raw.slice(0, -1) : raw
    if (line.trim() === '') return
    try {
      values.push(JSON.parse(line))
    } catch (err) {
      if (!terminated && i === lines.length - 1) {
        partial = line
      } else {
        errors.push({ line: i + 1, text: line, message: err instanceof Error ? err.message : String(err) })
      }
    }
  })
  return { values, errors, partial }
}

/** One JSON value per line, each line terminated, as the backend writes events.jsonl. */
export const formatJsonl = (values: readonly unknown[]): string =>
  values.map((v) => `${JSON.stringify(v)}\n`).join('')
