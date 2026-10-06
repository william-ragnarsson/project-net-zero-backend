# Demo stories

Hand-written scripts for every model reply in a demo run on
[`../demo-repo`](../demo-repo). A demo run never calls a model: it replays
cassettes from `examples/demo-repo/.netzero-cassettes/`, and those cassettes
are built from the files here. Everything else in a demo run is real: the
sandboxed tests, input capture, static checks, differential check and
benchmark all run on your machine, so the outcomes below are what the code
does, not what the story claims.

There is one directory per eligible function of the demo repo (12 of the 14;
`random_walk` and `fetch_exchange_rates` are skipped by triage), named by the
function's slug: the function id with `:` replaced by `__`, for example
`algos.graph__Graph.shortest_path_lengths`. `triage.json` holds the ranking
reply.

## Files in a story

| File            | What it is                                                                 |
| --------------- | -------------------------------------------------------------------------- |
| `tests.py`      | The generated pytest file that passes on the original.                    |
| `tests_v1.py`   | Optional. A first test file that fails on the original; `tests.py` is then the repaired file. |
| `A.py` `B.py` `C.py` | The three candidate rewrites, one complete function definition each, docstring kept, no imports. |
| `X_repair.py`   | The reply to candidate `X` after it was rejected (one repair per candidate). |
| `story.json`    | The text fields of every reply, plus what each candidate is expected to do. |

Each test file imports the function from its module the way callers do
(`from algos.graph import Graph` for the method), has at least three tests,
and has exactly one `@pytest.mark.nz_workload` test whose calls take 2 to
30 ms on the original. Candidate files hold the function only; any
import a candidate needs is listed in its `new_imports`.

## `story.json`

```json
{
  "function_id": "datakit.aggregate:total_by_category",
  "tests_notes": "notes of the first test file",
  "tests_repair": null,
  "A": {"strategy": "...", "rationale": "...", "new_imports": [], "expect": "win"},
  "B": {"strategy": "...", "rationale": "...", "new_imports": ["from collections import Counter"], "expect": "differential_mismatch"},
  "C": {"strategy": "...", "rationale": "...", "new_imports": [], "expect": "slower"},
  "repairs": {
    "B": {"diagnosis": "...", "strategy": "...", "rationale": "...", "new_imports": [], "expect": "win"}
  },
  "notes": "what the story shows, for people"
}
```

- `tests_repair` is `null`, or `{"diagnosis", "notes"}` when `tests_v1.py` exists.
- `repairs` has an entry, and an `X_repair.py`, for every candidate that is
  rejected. Candidates that pass every check are benchmarked and never repaired.
- Text limits follow the pipeline's schemas: strategy at most 8 words,
  rationale 40, diagnosis 30, test notes 30.
- `expect` and `notes` are for people and checks; no cassette contains them.

## Cassettes built from a story

Paths are relative to `examples/demo-repo/.netzero-cassettes/`; `<fslug>` is
the directory name. Only `output` is required in a cassette file.

| Stage            | Cassette path                       | `output` built from                                             |
| ---------------- | ----------------------------------- | --------------------------------------------------------------- |
| `triage`         | `triage/_/_/0.json`                 | `triage.json` (`{"ratings": [...]}`)                            |
| `tests`          | `tests/<fslug>/_/0.json`            | `code` = `tests_v1.py` if present, else `tests.py`; `notes` = `tests_notes` |
| `tests_repair`   | `tests_repair/<fslug>/_/1.json`     | `code` = `tests.py`; `diagnosis`, `notes` from `tests_repair`   |
| `rewrite`        | `rewrite/<fslug>/<X>/0.json`        | `code` = `X.py`; `strategy`, `rationale`, `new_imports` from `X` |
| `rewrite_repair` | `rewrite_repair/<fslug>/<X>/1.json` | `code` = `X_repair.py`; the rest from `repairs.X`               |

## `expect` values

A candidate either passes every check and is benchmarked, or is rejected with
one of the pipeline's reject reasons.

| Value                   | Meaning                                                                 |
| ----------------------- | ----------------------------------------------------------------------- |
| `win`                   | Passes every check; at least 1.5x faster on the workload calls.         |
| `ns`                    | Passes every check; within about 5% of the original (no significant win). |
| `slower`                | Passes every check; more than 5% slower than the original.             |
| `syntax`                | The code does not parse.                                                |
| `static_rule`           | Breaks a static rule (here: changed decorators).                        |
| `identical`             | The same code as the original once docstrings are ignored.              |
| `tests_failed`          | Fails the generated tests on the candidate's tree.                      |
| `differential_mismatch` | Differs from the original on a captured call: return value, type or argument mutation. |
| `timeout`               | Over 4x slower than the original on the captured calls (the differential check's limit). |

## The stories

Speedups are the original's time over the candidate's, best of 9 rounds of
all workload calls, best of 3 alternating pairs, measured with the real
harness on one laptop. They are indicative; the run's own benchmark decides.

| Function                          | Expected outcome     | A                          | B                               | C                        | Repairs                                    |
| --------------------------------- | -------------------- | -------------------------- | ------------------------------- | ------------------------ | ------------------------------------------ |
| `Graph.shortest_path_lengths`     | accepted             | win 58x                    | win 70x                         | ns 1.00x                 |                                            |
| `has_pair_with_sum`               | accepted             | tests_failed               | differential_mismatch (sorts the caller's list) | win 1.66x       | A: win 264x; B: win 156x                   |
| `total_by_category`               | accepted             | win 12.7x                  | differential_mismatch (returns a Counter) | slower 0.83x   | B: win 7.2x                                |
| `moving_average`                  | accepted             | win 13.4x                  | win 11.6x                       | ns 1.00x                 |                                            |
| `primes_below`                    | accepted             | win 89x                    | win 17x                         | static_rule (adds `lru_cache`) | C: win 51x                           |
| `levenshtein`                     | accepted             | win 3.4x                   | win 2.7x                        | syntax                   | C: win 3.5x                                |
| `dedupe_preserve_order`           | accepted             | win 56x                    | win 54x                         | ns 1.00x                 |                                            |
| `word_frequencies`                | accepted, after a tests repair | win 20.7x        | win 23x                         | ns 1.00x                 |                                            |
| `index_of_sorted`                 | no_significant_win   | slower 0.77x               | ns 1.02x                        | ns 1.01x                 |                                            |
| `zscore`                          | no_significant_win   | tests_failed (cancellation at a 1e9 offset) | timeout (7x slower) | slower 0.73x          | A: ns 1.02x; B: slower 0.79x               |
| `join_fields`                     | no_significant_win   | slower 0.53x               | slower 0.83x                    | ns 0.96 to 1.01x         |                                            |
| `top_k_inplace`                   | all_rejected         | differential_mismatch      | differential_mismatch           | differential_mismatch    | A, C: differential_mismatch; B: identical  |

The first eight are the ones triage preselects, with or without
`triage.json`. The last four are listed but not preselected; tick them in
the triage list to see the other outcomes.

## Checking a change

Nothing here is generated, so keep the stories honest when the demo repo or
the pipeline changes: each `tests.py` must pass on the original, each
`tests_v1.py` must fail, and every candidate must still get its `expect`
under the real static check, splice, tests, differential check and timing.
The `ns` and `slower` labels near 0.95x are the most sensitive to noise.
