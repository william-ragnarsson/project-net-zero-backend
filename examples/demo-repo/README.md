# toolbox

Small, dependency-free helpers for text, algorithms and tabular data.

This is the bundled demo repository of Project Net-Zero. The code is written
the way a lot of real code is: correct, documented and tested, and in places
slower than it needs to be. A demo run copies this tree, ranks its functions,
and tries to rewrite the slow ones without changing what they do. Some
functions are already as fast as they reasonably get, and two of them are
not safe to optimize automatically, on purpose.

## Layout

| Package   | Module         | Function                       |
| --------- | -------------- | ------------------------------ |
| `textkit` | `dedupe.py`    | `dedupe_preserve_order`        |
|           | `freq.py`      | `word_frequencies`             |
|           | `fields.py`    | `join_fields`                  |
| `algos`   | `primes.py`    | `primes_below`                 |
|           | `pairs.py`     | `has_pair_with_sum`            |
|           | `strings.py`   | `levenshtein`                  |
|           | `graph.py`     | `Graph.shortest_path_lengths`  |
|           | `search.py`    | `index_of_sorted`              |
|           | `walk.py`      | `random_walk`                  |
| `datakit` | `aggregate.py` | `total_by_category`            |
|           | `series.py`    | `moving_average`               |
|           | `stats.py`     | `zscore`                       |
|           | `rank.py`      | `top_k_inplace`                |
|           | `rates.py`     | `fetch_exchange_rates`         |

`random_walk` is nondeterministic and `fetch_exchange_rates` needs the
network, so Net-Zero's triage skips both.

## Requirements

Python 3.12 or later and nothing else: the code uses only the standard
library. `requirements.txt` and `requirements.lock` exist so tools that look
for a dependency list find one; both are empty.

## Tests

```sh
python -m pytest
```

`pytest.ini` puts the repository root on the import path and points pytest
at `tests/`. These are the repository's own tests; Net-Zero writes its own
for each function it optimizes and does not run these.

## Recorded LLM replies

A demo run replays recorded model replies from `.netzero-cassettes/`
instead of calling a model. They are generated from the hand-written stories
in `../demo-stories/`; see the README there.
