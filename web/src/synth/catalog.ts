// The functions of the synthetic run, written against the bundled demo repository so the
// replay reads like a real one. Each scripts one outcome; scenario.ts plays them out.
import type { CandidateId, FunctionOutcome, Mismatch, TriageItem } from '../gen/events'

type Rating = NonNullable<TriageItem['llm_potential']>

export interface PyFunction {
  module: string
  qualname: string
  doc: string
  /** the whole function, dedented, ending in a newline */
  source: string
  imports?: readonly string[]
}

export interface Triage {
  heuristic: number
  heuristicReasons: readonly string[]
  potential: Rating
  testability: Rating
  score: number
  reasons: readonly string[]
  skipReason?: string
}

/** One generated test file; `failing` names the test that fails on the original. */
export interface Draft {
  body: string
  failing?: string
  message?: string
  /** what a repair changed */
  diagnosis?: string
}

export interface Rewrite {
  code: string
  strategy: string
  rationale: string
  newImports?: readonly string[]
}

export type Rejection =
  | { reason: 'syntax'; problems: readonly string[] }
  | { reason: 'tests_failed'; failing: string; message: string }
  | { reason: 'differential_mismatch'; path: string; expected: string; actual: string }

/** One candidate attempt: benched at `delta` % CO₂ per call, or rejected (then maybe repaired). */
export type CandidatePlan = { rewrite: Rewrite; diagnosis?: string } & (
  | { delta: number; sigma?: number }
  | { reject: Rejection; repair?: CandidatePlan }
)

export interface Capture {
  nCalls: number
  previews: readonly string[]
  mutatesArgs?: boolean
  /** why two identical calls disagree; the function is then skipped */
  problem?: string
}

export interface SyntheticFunction extends PyFunction {
  expected: FunctionOutcome
  triage: Triage
  estCallS: number
  drafts: readonly Draft[]
  capture: Capture
  candidates?: Readonly<Record<CandidateId, CandidatePlan>>
  /** the merged trunk disagrees with the captured calls: the merge is reverted */
  mergeMismatches?: readonly Mismatch[]
}

const DEDUPE_TESTS = (iterableExpected: string) => `
def test_keeps_first_occurrence_order():
    assert dedupe_preserve_order([3, 1, 3, 2, 1]) == [3, 1, 2]


def test_empty_input():
    assert dedupe_preserve_order([]) == []


def test_accepts_any_iterable():
    assert dedupe_preserve_order("abracadabra") == ${iterableExpected}


@pytest.mark.nz_workload
def test_workload_many_duplicates():
    items = [(i * 7919) % 1000 for i in range(5000)]
    out = dedupe_preserve_order(items)
    assert len(out) == 1000
    assert out[:3] == [0, 919, 838]
`

const dedupe: SyntheticFunction = {
  module: 'textkit.dedupe',
  qualname: 'dedupe_preserve_order',
  doc: 'Order-preserving de-duplication.',
  source: `def dedupe_preserve_order(items):
    """Return the unique items of \`\`items\`\` in first-seen order."""
    result = []
    for item in items:
        if item not in result:
            result.append(item)
    return result
`,
  expected: 'accepted',
  triage: {
    heuristic: 0.58,
    heuristicReasons: ['list membership test inside a loop'],
    potential: 'high',
    testability: 'high',
    score: 0.92,
    reasons: ['O(n²): `item not in result` scans a list on every iteration', 'pure function of its input; easy to test'],
  },
  estCallS: 0.0184,
  drafts: [
    {
      body: DEDUPE_TESTS('["a", "b", "c", "d", "r"]'),
      failing: 'test_accepts_any_iterable',
      message: "assert ['a', 'b', 'r', 'c', 'd'] == ['a', 'b', 'c', 'd', 'r']",
    },
    {
      body: DEDUPE_TESTS('["a", "b", "r", "c", "d"]'),
      diagnosis: 'The function keeps first-seen order; the expected list was sorted. Fixed the expectation.',
    },
  ],
  capture: {
    nCalls: 7,
    previews: [
      'dedupe_preserve_order([3, 1, 3, 2, 1]) -> [3, 1, 2]',
      "dedupe_preserve_order('abracadabra') -> ['a', 'b', 'r', 'c', 'd']",
      'dedupe_preserve_order([0, 919, 838, 757, …5000 items]) -> [0, 919, 838, …1000 items]',
    ],
  },
  candidates: {
    A: {
      rewrite: {
        code: `def dedupe_preserve_order(items):
    """Return the unique items of \`\`items\`\` in first-seen order."""
    return list(set(items))
`,
        strategy: 'set() conversion',
        rationale: 'A set drops duplicates in one O(n) pass.',
      },
      reject: { reason: 'tests_failed', failing: 'test_keeps_first_occurrence_order', message: 'assert [1, 2, 3] == [3, 1, 2]' },
      repair: {
        rewrite: {
          code: `def dedupe_preserve_order(items):
    """Return the unique items of \`\`items\`\` in first-seen order."""
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
`,
          strategy: 'track seen items in a set',
          rationale: 'Set membership is O(1), so the loop is O(n) instead of O(n²).',
        },
        diagnosis: 'set() loses first-seen order; the repair keeps a seen-set beside the result list.',
        delta: -71.8,
      },
    },
    B: {
      rewrite: {
        code: `def dedupe_preserve_order(items):
    """Return the unique items of \`\`items\`\` in first-seen order."""
    return list(dict.fromkeys(items))
`,
        strategy: 'dict.fromkeys keeps first-seen order',
        rationale: 'dicts preserve insertion order; building one dedupes in C in a single pass.',
      },
      delta: -78.6,
    },
    C: {
      rewrite: {
        code: `def dedupe_preserve_order(items):
    """Return the unique items of \`\`items\`\` in first-seen order."""
    result = []
    append = result.append
    for item in items:
        if item not in result:
            append(item)
    return result
`,
        strategy: 'hoist the bound method out of the loop',
        rationale: 'Saves an attribute lookup per new item; the scan itself is unchanged.',
      },
      delta: -2.1,
      sigma: 0.05,
    },
  },
}

const MOVING_TESTS = `
def test_simple_window():
    assert moving_average([1, 2, 3, 4, 5], 2) == [1.5, 2.5, 3.5, 4.5]


def test_window_equal_to_length():
    assert moving_average([1, 2, 3], 3) == [2.0]


def test_float_values():
    assert moving_average([0.1, 0.2, 0.3, 0.4], 2) == pytest.approx([0.15, 0.25, 0.35])


def test_rejects_non_positive_window():
    with pytest.raises(ValueError):
        moving_average([1, 2, 3], 0)


@pytest.mark.nz_workload
def test_workload_6000_points_window_50():
    values = [(i * 37) % 101 for i in range(6000)]
    out = moving_average(values, 50)
    assert len(out) == 5951
    assert out[0] == sum(values[:50]) / 50
`

const movingAverage: SyntheticFunction = {
  module: 'datakit.series',
  qualname: 'moving_average',
  doc: 'Time series helpers.',
  source: `def moving_average(values, window):
    """Means of each full \`\`window\`\`-sized slice of \`\`values\`\`."""
    if window <= 0:
        raise ValueError("window must be positive")
    averages = []
    for start in range(len(values) - window + 1):
        chunk = values[start : start + window]
        averages.append(sum(chunk) / window)
    return averages
`,
  expected: 'reverted',
  triage: {
    heuristic: 0.51,
    heuristicReasons: ['slice inside a loop', 'sum() over each slice'],
    potential: 'high',
    testability: 'medium',
    score: 0.78,
    reasons: ['O(n·w): every window is re-sliced and re-summed', 'float results: a rewrite may change rounding'],
  },
  estCallS: 0.00148,
  drafts: [{ body: MOVING_TESTS }],
  capture: {
    nCalls: 9,
    previews: [
      'moving_average([1, 2, 3, 4, 5], 2) -> [1.5, 2.5, 3.5, 4.5]',
      'moving_average([0.1, 0.2, 0.3, 0.4], 2) -> [0.15000000000000002, 0.25, 0.35]',
      'moving_average([0, 37, 74, …6000 items], 50) -> [49.02, 49.76, …5951 items]',
    ],
  },
  candidates: {
    A: {
      rewrite: {
        code: `def moving_average(values, window):
    """Means of each full \`\`window\`\`-sized slice of \`\`values\`\`."""
    if window <= 0:
        raise ValueError("window must be positive")
    if len(values) < window:
        return []
    total = sum(values[:window])
    averages = [total / window]
    for i in range(window, len(values)):
        total += values[i] - values[i - window]
        averages.append(total / window)
    return averages
`,
        strategy: 'running sum',
        rationale: 'Each step adds one value and drops one, so the whole pass is O(n).',
      },
      delta: -88.4,
    },
    B: {
      rewrite: {
        code: `def moving_average(values, window):
    """Means of each full \`\`window\`\`-sized slice of \`\`values\`\`."""
    if window <= 0:
        raise ValueError("window must be positive")
    prefix = [0, *itertools.accumulate(values)]
    return [(prefix[i + window] - prefix[i]) / window for i in range(len(values) - window + 1)]
`,
        strategy: 'prefix sums with itertools.accumulate',
        rationale: 'One cumulative pass in C; each window mean is then a single subtraction.',
        newImports: ['import itertools'],
      },
      delta: -84.9,
    },
    C: {
      rewrite: {
        code: `def moving_average(values, window):
    """Means of each full \`\`window\`\`-sized slice of \`\`values\`\`."""
    if window <= 0:
        raise ValueError("window must be positive")
    return [sum(values[start : start + window]) / window for start in range(len(values) - window + 1)]
`,
        strategy: 'list comprehension',
        rationale: 'Avoids the append call per window; the slicing work is unchanged.',
      },
      delta: -3.6,
      sigma: 0.05,
    },
  },
  mergeMismatches: [
    { sample_idx: 1, kind: 'return', path: 'return[1]', expected: '0.25', actual: '0.25000000000000006' },
    { sample_idx: 4, kind: 'return', path: 'return[2]', expected: '0.35', actual: '0.35000000000000014' },
  ],
}

const TOPK_TESTS = `
def test_returns_k_largest_descending():
    assert top_k_inplace([3, 9, 1, 7, 5], 2) == [9, 7]


def test_k_zero_returns_empty():
    assert top_k_inplace([4, 2, 8], 0) == []


def test_k_larger_than_list():
    assert top_k_inplace([2, 1], 5) == [2, 1]


@pytest.mark.nz_workload
def test_workload_top_10_of_20k():
    values = [(i * 7919) % 20_011 for i in range(20_000)]
    assert len(top_k_inplace(values, 10)) == 10
`

const topK: SyntheticFunction = {
  module: 'datakit.rank',
  qualname: 'top_k_inplace',
  doc: 'Ranking helpers.',
  source: `def top_k_inplace(values, k):
    """The \`\`k\`\` largest values, descending. Sorts \`\`values\`\` in place."""
    values.sort(reverse=True)
    return values[:k]
`,
  expected: 'all_rejected',
  triage: {
    heuristic: 0.12,
    heuristicReasons: ['short function'],
    potential: 'medium',
    testability: 'medium',
    score: 0.74,
    reasons: [
      'sorts the whole list to take k items; heapq.nlargest is O(n log k)',
      'mutates its argument: a rewrite must keep the in-place sort',
    ],
  },
  estCallS: 0.0075,
  drafts: [{ body: TOPK_TESTS }],
  capture: {
    nCalls: 6,
    mutatesArgs: true,
    previews: [
      'top_k_inplace([3, 9, 1, 7, 5], 2) -> [9, 7]  (args[0] -> [9, 7, 5, 3, 1])',
      'top_k_inplace([4, 2, 8], 0) -> []  (args[0] -> [8, 4, 2])',
      'top_k_inplace([0, 7919, 15838, …20000 items], 10) -> [20010, 20009, …10 items]',
    ],
  },
  candidates: {
    A: {
      rewrite: {
        code: `def top_k_inplace(values, k):
    """The \`\`k\`\` largest values, descending. Sorts \`\`values\`\` in place."""
    return heapq.nlargest(k, values
`,
        strategy: 'heapq.nlargest',
        rationale: 'O(n log k) selection instead of a full O(n log n) sort.',
        newImports: ['import heapq'],
      },
      reject: { reason: 'syntax', problems: ["SyntaxError: '(' was never closed (line 3)"] },
    },
    B: {
      rewrite: {
        code: `def top_k_inplace(values, k):
    """The \`\`k\`\` largest values, descending. Sorts \`\`values\`\` in place."""
    values.sort()
    return values[-k:][::-1]
`,
        strategy: 'ascending sort and a negative slice',
        rationale: 'Take the last k of an ascending sort and reverse them.',
      },
      reject: { reason: 'tests_failed', failing: 'test_k_zero_returns_empty', message: 'assert [8, 4, 2] == []' },
    },
    C: {
      rewrite: {
        code: `def top_k_inplace(values, k):
    """The \`\`k\`\` largest values, descending. Sorts \`\`values\`\` in place."""
    ranked = sorted(values, reverse=True)
    return ranked[:k]
`,
        strategy: 'sorted() copy',
        rationale: "Same sort, without touching the caller's list.",
      },
      reject: { reason: 'differential_mismatch', path: 'args[0]', expected: '[9, 7, 5, 3, 1]', actual: '[3, 9, 1, 7, 5]' },
    },
  },
}

const JOIN_TESTS = `
def test_joins_with_default_comma():
    assert join_fields(["a", 1, 2.5]) == "a,1,2.5"


def test_custom_separator():
    assert join_fields(["x", "y"], sep="|") == "x|y"


def test_empty():
    assert join_fields([]) == ""


@pytest.mark.nz_workload
def test_workload_wide_record():
    record = list(range(200))
    assert join_fields(record).count(",") == 199
`

const joinFields: SyntheticFunction = {
  module: 'textkit.fields',
  qualname: 'join_fields',
  doc: 'Delimited records.',
  source: `def join_fields(fields, sep=","):
    """Join \`\`fields\`\` with \`\`sep\`\`, converting each field with \`\`str\`\`."""
    parts = []
    for field in fields:
        parts.append(str(field))
    return sep.join(parts)
`,
  expected: 'no_significant_win',
  triage: {
    heuristic: 0.33,
    heuristicReasons: ['append inside a loop'],
    potential: 'low',
    testability: 'high',
    score: 0.52,
    reasons: ['already linear; only constant factors to win', 'called once per exported row'],
  },
  estCallS: 0.0000214,
  drafts: [{ body: JOIN_TESTS }],
  capture: {
    nCalls: 5,
    previews: [
      "join_fields(['a', 1, 2.5]) -> 'a,1,2.5'",
      "join_fields(['x', 'y'], sep='|') -> 'x|y'",
      "join_fields([0, 1, 2, …200 items]) -> '0,1,2,3,4,5,…690 chars'",
    ],
  },
  candidates: {
    A: {
      rewrite: {
        code: `def join_fields(fields, sep=","):
    """Join \`\`fields\`\` with \`\`sep\`\`, converting each field with \`\`str\`\`."""
    return sep.join(map(str, fields))
`,
        strategy: 'map(str, …) straight into join',
        rationale: 'No intermediate list or per-item append call.',
      },
      delta: -4.2,
      sigma: 0.03,
    },
    B: {
      rewrite: {
        code: `def join_fields(fields, sep=","):
    """Join \`\`fields\`\` with \`\`sep\`\`, converting each field with \`\`str\`\`."""
    return sep.join([str(field) for field in fields])
`,
        strategy: 'list comprehension',
        rationale: 'join() materializes its input anyway; a comprehension builds it faster.',
      },
      delta: -3.1,
      sigma: 0.04,
    },
    C: {
      rewrite: {
        code: `def join_fields(fields, sep=","):
    """Join \`\`fields\`\` with \`\`sep\`\`, converting each field with \`\`str\`\`."""
    parts = []
    append = parts.append
    for field in fields:
        append(str(field))
    return sep.join(parts)
`,
        strategy: 'hoist the bound method out of the loop',
        rationale: 'Saves one attribute lookup per field.',
      },
      delta: -0.7,
      sigma: 0.07,
    },
  },
}

const NORMALIZE_TESTS = (name: string, ch: string) => String.raw`
def test_collapses_runs():
    assert normalize_whitespace("a  b\t\tc") == "a b c"


def test_strips_ends():
    assert normalize_whitespace("  hello \n") == "hello"


def test_empty():
    assert normalize_whitespace("") == ""


def test_${name}():
    assert normalize_whitespace("a${ch}b") == "a b"


@pytest.mark.nz_workload
def test_workload_long_text():
    text = "lorem \t ipsum\n\n dolor " * 500
    assert normalize_whitespace(text).count(" ") == 1499
`

const normalize: SyntheticFunction = {
  module: 'textkit.normalize',
  qualname: 'normalize_whitespace',
  doc: 'Whitespace normalisation.',
  source: String.raw`def normalize_whitespace(text):
    """Collapse runs of whitespace to one space and strip both ends."""
    out = ""
    pending_space = False
    for ch in text:
        if ch in " \t\n\r":
            pending_space = bool(out)
        else:
            if pending_space:
                out += " "
                pending_space = False
            out += ch
    return out
`,
  expected: 'skipped_untestable',
  triage: {
    heuristic: 0.62,
    heuristicReasons: ['string concatenation inside a loop'],
    potential: 'medium',
    testability: 'low',
    score: 0.24,
    reasons: ["`out +=` per character; ' '.join(text.split()) is the idiom", "its notion of whitespace is narrower than str.split()'s"],
  },
  estCallS: 0.0009,
  drafts: [
    {
      body: NORMALIZE_TESTS('non_breaking_space', ' '),
      failing: 'test_non_breaking_space',
      message: String.raw`assert 'a\xa0b' == 'a b'`,
    },
    {
      body: NORMALIZE_TESTS('form_feed', String.raw`\f`),
      failing: 'test_form_feed',
      message: String.raw`assert 'a\x0cb' == 'a b'`,
      diagnosis: 'Dropped the non-breaking-space case; checking another whitespace character instead.',
    },
    {
      body: NORMALIZE_TESTS('vertical_tab', String.raw`\v`),
      failing: 'test_vertical_tab',
      message: String.raw`assert 'a\x0bb' == 'a b'`,
      diagnosis: 'Form feed is not collapsed either; trying vertical tab.',
    },
  ],
  capture: { nCalls: 4, previews: [] },
}

const PARSE_TESTS = `
def test_parses_pairs():
    [rec] = parse_records(["a=1;b=2"])
    assert rec["a"] == "1" and rec["b"] == "2"


def test_ignores_empty_pairs():
    [rec] = parse_records(["a=1;;"])
    assert set(rec) == {"a", "_parsed_at"}


def test_one_record_per_line():
    assert len(parse_records(["a=1", "b=2", "c=3"])) == 3


@pytest.mark.nz_workload
def test_workload_2000_lines():
    lines = [f"id={i};name=item{i};qty={i % 7}" for i in range(2000)]
    assert len(parse_records(lines)) == 2000
`

const parseRecords: SyntheticFunction = {
  module: 'datakit.io_free',
  qualname: 'parse_records',
  doc: 'Record parsing.',
  imports: ['import time'],
  source: `def parse_records(lines):
    """Parse \`\`k=v;k=v\`\` lines into dicts, stamping each with the parse time."""
    records = []
    for line in lines:
        record = {}
        for pair in line.split(";"):
            if pair:
                key, _, value = pair.partition("=")
                record[key.strip()] = value.strip()
        record["_parsed_at"] = time.time()
        records.append(record)
    return records
`,
  expected: 'skipped_capture',
  triage: {
    heuristic: 0.41,
    heuristicReasons: ['nested loops', 'calls time.time()'],
    potential: 'low',
    testability: 'low',
    score: 0.19,
    reasons: ['stamps each record with time.time(): outputs are not reproducible'],
  },
  estCallS: 0.0041,
  drafts: [{ body: PARSE_TESTS }],
  capture: {
    nCalls: 5,
    previews: [
      "parse_records(['a=1;b=2']) -> [{'a': '1', 'b': '2', '_parsed_at': 1767225600.104}]",
      "parse_records(['a=1;b=2']) -> [{'a': '1', 'b': '2', '_parsed_at': 1767225600.231}]",
    ],
    problem: "two identical calls returned different values at [0]['_parsed_at']",
  },
}

/** Rated by triage but not optimized: one the user deselected, one that is not optimizable. */
export const UNSELECTED: readonly (PyFunction & { triage: Triage })[] = [
  {
    module: 'textkit.freq',
    qualname: 'word_frequencies',
    doc: 'Word counts.',
    source: `def word_frequencies(text):
    """Count case-insensitive, whitespace-separated words in \`\`text\`\`."""
    counts = {}
    for word in text.split():
        if word.lower() in counts:
            counts[word.lower()] += 1
        else:
            counts[word.lower()] = 1
    return counts
`,
    triage: {
      heuristic: 0.47,
      heuristicReasons: ['repeated method call in a loop'],
      potential: 'medium',
      testability: 'high',
      score: 0.81,
      reasons: ['`word.lower()` is computed up to three times per word', 'collections.Counter does the counting in C'],
    },
  },
  {
    module: 'datakit.rates',
    qualname: 'fetch_exchange_rates',
    doc: 'Exchange rates.',
    imports: ['import json', 'import urllib.request'],
    source: `def fetch_exchange_rates(base="EUR"):
    """Latest exchange rates for \`\`base\`\`, from the network."""
    with urllib.request.urlopen(f"https://rates.example.org/latest?base={base}") as resp:
        return json.load(resp)
`,
    triage: {
      heuristic: 0.37,
      heuristicReasons: ['I/O call'],
      potential: 'none',
      testability: 'none',
      score: 0,
      reasons: ['dominated by network latency; nothing to win on the CPU'],
      skipReason: 'network I/O: urllib.request.urlopen',
    },
  },
]

/** The selection, in the order the run optimizes it. */
export const SELECTED: readonly SyntheticFunction[] = [dedupe, movingAverage, topK, joinFields, normalize, parseRecords]
