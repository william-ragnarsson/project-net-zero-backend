// Compiles the backend contract (src/gen/schema.json, written by `netzero export-schema`)
// into src/gen/events.ts. Run via `npm run gen:types` or `make types`.
import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { compile, type JSONSchema } from 'json-schema-to-typescript'

const SCHEMA_PATH = fileURLToPath(new URL('../src/gen/schema.json', import.meta.url))
const OUT_PATH = fileURLToPath(new URL('../src/gen/events.ts', import.meta.url))

type Node = { [key: string]: unknown }

const isNode = (v: unknown): v is Node => typeof v === 'object' && v !== null && !Array.isArray(v)

// Pydantic titles every property ("Run Id", "Seq", ...); json2ts would turn each into a
// numbered alias (Seq1, Seq2, ...). Only the $defs keep their titles, as interface names.
// Maps keyed by an enum (propertyNames.enum) become closed objects with optional keys, so
// counts_by_outcome reads as {accepted?: number, ...} instead of a string index signature.
function normalize(node: unknown, keepTitle: boolean): void {
  if (Array.isArray(node)) {
    for (const item of node) normalize(item, false)
    return
  }
  if (!isNode(node)) return
  if (!keepTitle) delete node.title
  const keys = isNode(node.propertyNames) ? node.propertyNames.enum : undefined
  if (Array.isArray(keys) && isNode(node.additionalProperties)) {
    const value = node.additionalProperties
    node.properties = Object.fromEntries(keys.map((k) => [String(k), value]))
    node.additionalProperties = false
    delete node.propertyNames
  }
  for (const [key, value] of Object.entries(node)) {
    if (key === '$defs' && isNode(value)) {
      for (const def of Object.values(value)) normalize(def, true)
    } else if (key === 'properties' && isNode(value)) {
      // a name map, not a schema: a property may itself be called "title"
      for (const prop of Object.values(value)) normalize(prop, false)
    } else {
      normalize(value, false)
    }
  }
}

/** Interface names of the event union, in the schema's oneOf order. */
function eventNames(schema: Node): string[] {
  const props = isNode(schema.properties) ? schema.properties : {}
  const event = isNode(props.event) ? props.event : {}
  const defs = isNode(schema.$defs) ? schema.$defs : {}
  const oneOf = Array.isArray(event.oneOf) ? event.oneOf : []
  const names = oneOf.map((ref) => {
    const key = isNode(ref) ? String(ref.$ref).replace('#/$defs/', '') : ''
    const def = defs[key]
    if (!isNode(def)) throw new Error(`schema.json: event ref ${key} has no $defs entry`)
    return typeof def.title === 'string' ? def.title : key
  })
  if (names.length === 0) throw new Error('schema.json: properties.event has no oneOf')
  return names
}

// json2ts tags every interface with where it came from; that is noise in a single-root schema.
const REFERENCED = 'This (?:interface|type) was referenced by `Contract`\'s JSON-Schema\\n \\* via the `definition` "[^"]+"\\.'

function stripProvenance(ts: string): string {
  return ts
    .replace(new RegExp(`/\\*\\*\\n \\* ${REFERENCED}\\n \\*/\\n`, 'g'), '')
    .replace(new RegExp(`\\n \\*\\n \\* ${REFERENCED}`, 'g'), '')
}

// json2ts emits one copy of a shared empty def per use (NoData, NoData1, ...); fold
// numbered copies that are identical to their base back into the base.
function foldCopies(ts: string): string {
  const bodies = new Map<string, string>()
  for (const m of ts.matchAll(/^export interface (\w+) (\{\}|\{\n[^]*?\n\})$/gm)) {
    bodies.set(m[1] ?? '', m[2] ?? '')
  }
  let out = ts
  for (const [name, body] of bodies) {
    const base = name.replace(/\d+$/, '')
    if (base !== name && bodies.get(base) === body) {
      out = out.replace(`export interface ${name} ${body}\n`, '').replace(new RegExp(`\\b${name}\\b`, 'g'), base)
    }
  }
  return out
}

function footer(names: string[]): string {
  return `
/** Every persisted event; \`type\` is the discriminant. */
export type RunEvent =
${names.map((n) => `  | ${n}`).join('\n')}

export type EventType = RunEvent['type']

export type EventOf<T extends EventType> = Extract<RunEvent, { type: T }>

export type RunState = RunStateChangedData['to_state']
export type RunMode = RunCreatedData['mode']
export type CandidateId = RankingEntry['candidate_id']
export type DecisionOutcome = FunctionDecisionData['outcome']
export type FunctionOutcome = FunctionCompletedData['outcome']
export type RejectReason = CandidateRejectedData['reason']
export type LlmStage = LlmUsageData['stage']
export type LogLevel = LogData['level']
export type LogSource = LogData['source']
`
}

async function main(): Promise<void> {
  const schema = JSON.parse(readFileSync(SCHEMA_PATH, 'utf8')) as Node
  const names = eventNames(schema)
  normalize(schema, true)
  const body = await compile(schema as JSONSchema, 'Contract', {
    additionalProperties: false,
    unreachableDefinitions: true,
    // a maxItems bound would otherwise expand into a union of every tuple length
    maxItems: -1,
    bannerComment:
      '// Generated by scripts/gen-types.ts from src/gen/schema.json. Do not edit; run `npm run gen:types`.',
    format: true,
  })
  for (const name of names) {
    if (!new RegExp(`^export interface ${name} \\{`, 'm').test(body)) {
      throw new Error(`json2ts did not emit an interface named ${name}`)
    }
  }
  writeFileSync(OUT_PATH, foldCopies(stripProvenance(body)) + footer(names))
  console.log(`wrote ${OUT_PATH} (${names.length} event types)`)
}

await main()
