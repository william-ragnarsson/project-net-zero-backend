# Project Net-Zero

Net-Zero makes Python code use less energy, one function at a time. Give it a
GitHub repository and it ranks the functions worth optimizing, writes tests for
them, and asks Claude for three rewrites of each. It keeps a rewrite only if the
rewrite passes those tests and a differential check against the original, and
if a benchmark shows a statistically significant drop of at least 5% in
estimated CO2e per call. On most machines that estimate is the CPU time of the
calls times a fixed power figure and grid factor; see
[Measuring carbon](#measuring-carbon). You get a patch, a zip of the optimized
repo and the evidence for every decision.

It is a FastAPI + SSE backend and a Typer CLI (the `netzero` package), plus a
Vite/React web UI in `web/` that is still being built. It started as a
HackEurope project.

## Quick start

### Prerequisites

- macOS or Linux. The sandbox relies on POSIX process groups and rlimits.
- [uv](https://docs.astral.sh/uv/) and git. uv installs Python 3.12 for you.
- Node 20+ and npm, for the web UI.
- Optional: an Anthropic API key. Live runs need one; demo and replay do not.
- Optional: Docker, Podman or a local Postgres 17, for the History tab
  (see [Run history in Postgres](#run-history-in-postgres)).

### Setup and run

```sh
cp .env.example .env   # optional: add your ANTHROPIC_API_KEY
make setup             # uv sync, Python 3.12, warm the demo cache, npm ci, netzero doctor
make run               # build the web UI, then serve UI + API on one port
```

Open <http://127.0.0.1:8000>. Set a different port with `make run PORT=9000`.

### Three ways to try it

| Mode | Needs | What happens |
| --- | --- | --- |
| Live | API key | Clones a public GitHub repo and runs the full pipeline with Claude. Web UI, or `uv run netzero run https://github.com/<owner>/<repo>`. |
| Demo | nothing | Runs the full pipeline on the bundled repo in `examples/demo-repo`. The model replies come from **hand-written cassettes** (see [Demo repo and cassettes](#demo-repo-and-cassettes)), not from Claude. The tests, capture, checks and benchmarks really run on your machine. Web UI, or `uv run netzero run --demo`. |
| Replay | nothing | Plays an `events.jsonl` back without running anything: any run's log with `uv run netzero replay <file>`, or a bundled replay in the web UI at `/replay`. Today the only bundled replay is synthetic (scripted events, not a measured run). |

### `.env`

`.env` is read from the repository root, whatever your working directory is.
`.env.example` contains:

| Line | Meaning |
| --- | --- |
| `ANTHROPIC_API_KEY=` | Turns on live mode. `NETZERO_ANTHROPIC_API_KEY` also works. Without a key, only demo and replay are available. |
| `# NETZERO_STAGES__REWRITE__MODEL=claude-opus-5-5` | Per-stage model override. Every stage defaults to `claude-haiku-4-5`. |
| `# NETZERO_STAGES__REWRITE__EFFORT=high` | Per-stage effort. It is sent only to models that accept it (Haiku 4.5 does not). |
| `# NETZERO_COUNTRY=SWE` | ISO3 country code for grid carbon intensity. Unset means CodeCarbon's world average. |
| `# NETZERO_PORT=8000` | Default port for `netzero serve` when run directly. `make run`/`serve`/`dev` pass `--port` themselves; use `make run PORT=9000` there. |
| `# NETZERO_DATABASE_URL=postgresql://...` | Turns on run history in Postgres. `make db` starts a database at this address. Unset means no history; everything else works the same. |

## How a run works

A run is a folder, `runs/<YYYYMMDD-HHMMSS-slug>/`. Its `events.jsonl` is the
source of truth, and `run.json` is a projection of it. Only one run can be
active at a time; the lock (`runs/.active.lock`) is shared by the server and
the CLI.

```
created -> cloning -> installing -> discovering -> triaging -> awaiting_selection
        -> optimizing -> finalizing -> completed
any non-terminal state -> failed | cancelled | interrupted
```

1. **cloning**: shallow (`--depth 1`), single-branch HTTPS clone of
   `https://github.com/<owner>/<repo>`, optionally at a branch or tag; limits
   120 s, 200 MB, 20,000 files, at least one `.py` file. The demo repo is
   copied instead and committed with a fixed date.
2. **installing**: `uv venv` (Python 3.12), then `uv pip install` of pytest
   plus the deps from `requirements.txt`, another runtime `requirements*.txt`,
   or a compiled `pyproject.toml`/`setup.py`/`setup.cfg`. Best effort: on
   failure the run continues with pytest only. The project itself is never
   installed; it is imported via `PYTHONPATH`, and an import probe records
   which modules import. The power probe runs in parallel.
3. **discovering / triaging**: heuristic scoring keeps the top
   `NETZERO_MAX_FUNCTIONS` (20) eligible functions, then Claude rates up to
   `NETZERO_TRIAGE_LLM_TOP` of those (so at most 20 by default) for potential
   and testability; `score = 0.5 * heuristic + 0.5 * potential * testability`.
   Skipped: nested, async, generator and dunder functions, properties and
   similar decorators, under 4 or over 150 lines, I/O, nondeterminism,
   global/nonlocal writes, modules that do not import.
4. **awaiting_selection**: up to 20 eligible functions are listed, followed by
   up to 20 skipped ones with their skip reason; the top 8 eligible ones
   scoring at least 0.15 are preselected. Confirm or change the selection in
   the UI, the CLI prompt, `--select`, or `POST .../select`.
5. **optimizing**: the selected functions run one after another (below).
6. **finalizing**: writes the patch and the zip.

### Per-function flow

```
write tests (Claude) -> run twice on the original --fail/flaky, 2 repairs--> skipped_untestable
        |
capture inputs + reference outputs, re-run for determinism --nothing usable--> skipped_capture
        |
        +-- baseline bench of the original (holds the bench slot first)
        +-- A: write -> static check -> splice -> tests + differential (1 repair) -> bench
        +-- B: same, in parallel
        +-- C: same, in parallel
        |
decision (Holm + gate) -> winner | no_significant_win | all_rejected
        |
merge winner into trunk -> re-verify -> commit (accepted) | git reset --hard (reverted)
```

- **Tests**: one pytest file per function with at least 3 tests and exactly
  one `@pytest.mark.nz_workload` test; no network/OS imports, I/O, mocking or
  monkeypatching. A failing or flaky file gets up to 2 repairs.
- **Capture**: a pytest plugin pickles the arguments of every outermost call:
  at most 256 distinct calls and 32 MB in total, a call over 4 MB is dropped,
  and calls from tests other than the `nz_workload` one may fill at most 75%
  of either cap. A reference run records return values, exceptions, argument
  mutations and timings; a second run checks determinism.
- **Candidates**: A, B and C get different hints (*algorithmic*,
  *builtins/stdlib*, *conservative micro-optimizations*). The static check
  rejects a changed name, signature, async-ness or decorators,
  `global`/`nonlocal`, new I/O calls, forbidden imports (`socket`,
  `subprocess`, `shutil`, `ctypes`, ...), undefined names, and code identical
  to the original. A candidate that passes is spliced into its own copy of
  the trunk (`work/cand/<slug>/<A|B|C>`, a plain directory copy without
  `.git`) and runs the generated tests plus the differential check, which
  compares return values, exception types (by name, not message) and
  argument mutations on every captured call. It also rejects the candidate
  (reason `timeout`) if its total time on the calls whose original took at
  least 1 ms is more than 4x the original's, or if any single call runs over
  10 s. Calls under 1 ms are not timed against the limit, and the re-verify
  after merge does not check speed. A candidate rejected by a check gets 1
  repair; benched candidates are never repaired. Reject
  reasons: `syntax`, `static_rule`, `tests_failed`, `differential_mismatch`,
  `timeout`, `llm_error`, `identical`, `bench_failed`,
  `measurement_inconsistent`.
- **Acceptance gate**: a candidate wins only if (1) its Holm-adjusted p <
  0.05 (one-sided Welch t-test: the candidate uses less), (2) the upper end of
  the two-sided 95% CI of the change is below 0, and (3) the change is at most
  -5%. Among those that pass, the largest reduction wins; ties go to the
  smaller diff, then A, B, C. Details under [Measuring carbon](#measuring-carbon).
- **Merge, re-verify, revert**: the winner is spliced into the trunk
  (`work/trunk`, a worktree on branch `netzero/trunk`), then every generated
  test file that passed on the original plus this function's differential
  check re-runs there. On success it commits `netzero: optimize <qualname>
  (<pct>%)`; otherwise `git reset --hard` and the outcome is `reverted`.

Function outcomes: `accepted`, `no_significant_win`, `all_rejected`,
`reverted`, `skipped_untestable`, `skipped_capture`, `failed`, `cancelled`.

### Outputs

| Path under `runs/<id>/` | What |
| --- | --- |
| `events.jsonl`, `run.json` | The event log, and the run state projected from it |
| `out/<id>.patch` | `git diff` from the base commit to the optimized trunk. Only written if something merged. |
| `out/<id>.zip` | `git archive` of the optimized trunk, plus `netzero-report.json`, the generated tests under `netzero-tests/` and, if something merged, `netzero.patch` |
| `fn/<slug>/merge.diff` | The merge diff of one accepted function. `<slug>` is the id with `:` replaced by `__` (for example `textkit.dedupe__dedupe_preserve_order`), shortened and given a hash suffix for long or ambiguous ids |
| `repo/`, `venv/`, `work/`, `logs/`, `home/`, `tmp/` | The clone, the venv, the trunk worktree and the candidate copies, logs, and the sandbox `HOME`/`TMPDIR` |

The generated tests are not wired into the repo's own test suite, and the
repo's own suite is never run.

## Measuring carbon

All measurement code is in `netzero/bench/`.

**Power source.** `netzero/bench/probe.py` asks CodeCarbon 3.2.2, in a child
process, what it would measure with. The result is cached in
`.netzero-cache/power-profile.json`.

| Source | When | Badge | Method |
| --- | --- | --- | --- |
| `rapl` | Linux with a readable RAPL package counter (`/sys/class/powercap/intel-rapl:*/energy_uj`) | `measured` | `codecarbon_task` |
| `powermetrics` | Apple Silicon where `sudo -n powermetrics` works | `estimated`, or `calibrated` after `netzero calibrate` | `codecarbon_model` |
| `tdp_estimate` | Everything else | `estimated` | `codecarbon_model` |

Only `rapl` reads a meter during benchmarks. powermetrics is read only by
`netzero calibrate`, to set `P_core`; benchmarks on the `powermetrics` source
still use the CPU-time model below.

**Energy per trial.** Outside RAPL, `E = cpu_s * P_core + wall_s * P_ram`:
`cpu_s` is the CPU time of the worker's calling thread during the timed calls
(`time.thread_time()`, garbage collector paused), so work in other threads or
processes is not counted; `wall_s` is the wall time of those calls; `P_core`
is CodeCarbon's CPU-load model for one busy thread (`TDP / cpu_count`; CPUs
missing from CodeCarbon's table get a generic 85 W TDP); and `P_ram` is
CodeCarbon's RAM model. With RAPL, CPU energy is the package energy that
CodeCarbon's `start_task`/`stop_task` measures around the timed calls
(arguments are copied first). That window also includes the worker's IPC
round trip and the thread hops that start and stop the meter, and RAM is
still `wall_s * P_ram`, so even a `measured` result has modelled RAM energy
and a static grid factor. If the RAPL meter cannot be set up, the run falls
back to the model and the badge becomes `estimated`. A meter error during a
trial is not recovered: in a candidate's comparison that candidate is
rejected as `bench_failed`, and in the baseline the function fails.

`netzero calibrate` (macOS) reads powermetrics "CPU Power" for 3 s idle and
3 s with one spinning thread, then stores `P_core = max(busy - idle, 1 W)`.
If the probe itself fails, a cached profile for the same grid setting is
used; without one, the fallback is 85 W TDP over all cores, a RAM floor of
3 W (ARM) or 10 W (x86), and the world-average grid.

**Grid intensity.** `NETZERO_COUNTRY` (ISO3) selects CodeCarbon's energy mix
for that country; unset or unknown countries fall back to CodeCarbon's world
average (475 g/kWh). This is a static average, not real-time data. Then
`g = kWh * kg_per_kWh * 1000`.

**Benchmark.** Benches run one at a time and hold a readers-writer "quiet
gate" exclusively, so none of this run's other sandboxed processes
(candidate tests, differential checks, bench-worker start-up) runs at the
same time. The netzero process itself (LLM calls, static checks, file copies,
the event stream) and any other load on the machine still compete, and
nothing pins the benchmark to a CPU or controls frequency scaling or turbo.
A persistent worker process replays the calls captured while the generated
`nz_workload` test ran (all captured calls only if that test made none).
Calibration sizes a trial to about 0.75 s (at most 100,000 calls). The
baseline is a warmup plus 16 trials of the original; it is informational
(shown in the UI, with a warning when the CV across trials is above 25%).
Each candidate is then compared with 16 trials per arm in ABBA order (AB, BA,
AB, ...), which re-measures the original: the % change, p-value and grams
saved come from these two arms, not from the baseline. Trial j of both arms
replays the same samples, and the ABBA order spreads slow drift over both
arms alike. It does not remove bursts of other load.

**Statistics** (`netzero/bench/stats.py`). A one-sided Welch t-test on the
per-trial `log(g per call)`; `delta_pct = (exp(mean log diff) - 1) * 100`, the
change in the geometric mean; a two-sided 95% Welch-Satterthwaite t interval;
Holm's step-down correction over the candidates that were benched, passed
the sanity check and were not rejected; then the 3-part gate above.
**Sanity check**: if `|delta| >= 5%` and its sign disagrees with the sign of
the CPU-time change, the candidate is rejected as `measurement_inconsistent`.
What these numbers do not cover:

- The t-test treats the two arms as independent samples, although trial j
  of each arm replays the same inputs. Ignoring that pairing usually makes
  the test conservative.
- Holm corrects only across the 0 to 3 eligible candidates of one function.
  There is no correction across functions, so with 8 functions selected the
  run-wide chance of at least one false accept is higher than 5%.
- The p-value reflects trial-to-trial noise on this machine with this one
  fixed workload. It says nothing about other inputs or other hardware.
- In model mode the sanity check is close to tautological: CO2e is computed
  from the same thread CPU time it is compared with, so it can only catch a
  large wall-time (RAM) effect.

**Reported numbers.** Per accepted function: the % change, its CI and
p-values, and `g_saved_per_1m_calls` / `kwh_saved_per_1m_calls`, computed as
`(mean original - mean candidate) * 1e6` from the arithmetic means of the two
comparison arms. The % change is a geometric-mean change, so the two figures
can disagree in size. Run details also carry cost assumptions (EUR 0.20/kWh,
EU ETS EUR 70/t, voluntary market EUR 15/t, 1,000,000 calls/year); yearly
projections are computed in the UI.

**Limits.** In model mode (every source except `rapl`) the energy is CPU
time times a constant (plus wall time times the RAM constant), so the %
change is mostly a CPU-time change and absolute grams depend on the assumed
`P_core`, `P_ram` and grid factor. **The relative % is the meaningful number;
treat grams and euros as estimates.** Because `cpu_s` counts only the
calling thread and the static check does not forbid `threading`, a rewrite
that moved work onto another thread would look cheaper in model mode, and
the sanity check would not catch it. RAPL reads the whole package, so other
load on the machine shows up. Savings per call only hold if the inputs of
the generated `nz_workload` test (written by Claude, or by hand in the demo)
look like production calls.

## CLI reference

Run commands as `uv run netzero <command>`. Every command takes `--help`.

| Command | Key options | What it does |
| --- | --- | --- |
| `serve` | `--host` (127.0.0.1), `--port` (8000), `--reload` | Serves the API and the built web UI on one port. Warns when bound to a non-local address. |
| `run [URL]` | `--demo`, `--ref`, `-y/--yes`, `--select ID` (repeatable), `--json`, `-v` | Runs from the terminal. Takes exactly one of a URL or `--demo`. A running `serve` can follow the run at `/runs/<id>`. At selection: `--yes` takes the preselection; otherwise `--select` ids, else a `[Y/n]` prompt on a TTY (n cancels), else the preselection. The first Ctrl+C cancels the run; a second stops it at once. |
| `replay PATH` | `--speed` (4.0; 0 = instant, gaps capped at 1.5 s), `--check`, `-v` | Plays an `events.jsonl` in the terminal. `--check` only validates it against the event grammar. |
| `fake` | `-o/--out`, `--scenario demo\|short\|fail_env`, `--seed`, `--speed`, `-v` | Generates a scripted run (no git, uv or LLM) in virtual time and checks its grammar. This is the FakePipeline the UI develops against. |
| `record` | `--cassettes`, `--select`, `-o/--out` (`web/public/replays/demo.events.jsonl`), `-v` | Runs the demo, copies its events to `--out` and adds an entry to `index.json` next to it. Without `--cassettes` it replays the hand-written cassettes and needs no key. With `--cassettes` it calls Claude and overwrites the matching cassette files (needs a key). |
| `demo-cassettes` | | Deletes `examples/demo-repo/.netzero-cassettes` and rebuilds it from `examples/demo-stories`. |
| `calibrate` | `--force` | Measures `P_core` with powermetrics on macOS (about 10 s, needs passwordless sudo). On failure it prints the sudoers line to add. |
| `doctor` | `--refresh` (re-probe power) | Required checks: git, uv, Python 3.12. Advisory checks: node 20+, built web UI, API key, demo cassettes, and how CO2 will be measured. |
| `export-schema` | `--out` (`web/src/gen/schema.json`), `--check` | Writes the JSON schema of the event/API contract. `--check` fails if the file is stale. |
| `db status` | | What the history database holds, table by table. Needs `NETZERO_DATABASE_URL`, like every `db` command, and creates the tables on first use. |
| `db sync` | | Stores every run under `runs/` that isn't stored yet, plus any events added since, and folds them into the read models. The server does this on its own every 1.5 s. |
| `db import FILE...` | | Stores `events.jsonl` files from anywhere, for example the bundled replays. Already stored events are skipped. |
| `db rebuild` | | Drops the read models and folds every stored event again. The events stay. |

Exit codes of `run`: **0** completed; **1** failed, or rejected before
starting (no API key, a run already active, invalid URL); **2** bad
arguments (both or neither of URL and `--demo`); **130** cancelled or
interrupted. `record`, `fake`, `replay --check`, `doctor`, `calibrate`,
`demo-cassettes` and `export-schema --check` exit 1 on failure; `fake` exits
2 on a bad scenario or speed.

## HTTP API

Everything is under `/api`. Errors raised by the routes below (unknown run,
missing artifact, wrong state, rejected run, invalid body) return
`{detail, code, active_run}`, where `active_run` is null unless `code` is
`run_active`. Unknown routes and wrong methods return FastAPI's plain
`{detail}` with no `code`.

| Route | Returns |
| --- | --- |
| `GET /api/health` | `{ok, version}` |
| `GET /api/capabilities` | Version, `has_api_key`, available modes, active run, platform, Python, git/uv, cached power profile, models per stage, `demo_available` |
| `POST /api/runs` | 201 with a `RunSummary`. Body: `{"github_url": "...", "ref": "main"}` or `{"demo": true}`; optional `auto_select`. Errors: 400 `invalid_url`/`bad_request`, 412 `no_api_key`/`not_ready`, 409 `run_active` |
| `GET /api/runs` | A list of `RunSummary` |
| `GET /api/runs/{id}` | `RunDetail` (functions, triage, power, settings, artifacts, LLM cost), or 404 `not_found` |
| `POST /api/runs/{id}/select` | `{"function_ids": [...]}` with 1 to 20 ids. 404 `not_found` for an unknown run, 409 `bad_state` outside `awaiting_selection`, 400 for unknown or skipped ids |
| `POST /api/runs/{id}/cancel` | `RunSummary`. Idempotent; waits up to about 5 s. 404 `not_found` for an unknown id, 409 `bad_state` if the run is driven by another netzero process (cancel it there) |
| `GET /api/runs/{id}/events` | Server-sent events (below) |
| `GET /api/runs/{id}/events.jsonl` | The full log as NDJSON, complete lines only |
| `GET /api/runs/{id}/artifacts/patch`, `.../artifacts/zip`, `.../artifacts/functions/{function_id}/diff` | `netzero-<id>.patch`, the optimized repo zip, one function's merge diff; 404 `not_found` if absent |
| `GET /api/history/status` | Always 200. `enabled` is false without `NETZERO_DATABASE_URL`; otherwise `ok`, rows per table, the stored and projected event positions, the newest events, and the last error in `detail` |
| `GET /api/history/projects` | One summary per repository with a stored run, most recently run first |
| `GET /api/history/projects/{id}` | A project's runs, each function's results across runs, candidate and rejection counts; 404 `not_found` |
| `GET /api/history/activity` | Notable events across all runs, newest first. `?project=`, `?limit=` (30, max 100), `?before=` (the previous page's `next`) |
| `GET /api/history/runs/{id}/events.jsonl` | A stored run's log, byte for byte what the run wrote |
| `GET /api/history/runs/{id}/diff?function_id=` | The diff a run merged or proposed for one function |

The history reads answer 503 `no_database` when history is off or the
database can't be reached.

Every other path serves the built SPA from `web/dist`. Unknown paths without
a file extension fall back to `index.html`; missing files (with an extension)
and unknown `/api/` paths return 404. Bundled replays are static files under
`/replays/`. Without a build, every non-API path shows a placeholder page
that tells you to run `make run`. FastAPI's generated docs are at `/docs` and
its OpenAPI schema at `/openapi.json`; the web UI's generated types come from
`netzero export-schema` instead (see **Schema** below).

**SSE semantics.** The first frame is a comment that sets `retry: 2000`. The
stream resumes after `max(Last-Event-ID, ?after=)`; every event frame has
`id` set to its `seq` and the event JSON as `data`. After 15 s of silence the
server sends `event: heartbeat` (no `id`, never persisted). The stream closes
after the terminal event, and a finished run whose client is caught up gets
204, which stops `EventSource` from reconnecting. A slow client whose queue
overflows is disconnected and resumes from its last id. Runs driven by
another process (for example `netzero run`) are tailed from disk.

**Event grammar** (`netzero/events.py`, `SCHEMA_VERSION = 1`).

- The envelope is `{seq, ts, run_id, function_id?, candidate_id?, attempt?,
  type, data}`. `seq` is always the first key, starts at 1 and has no gaps.
- Every `X.started` is followed by exactly one `X.completed` with the same
  scope, carrying `ok`, `duration_ms` and `error`.
- The terminal event (`run.completed`, `run.failed`, `run.cancelled` or
  `run.interrupted`) is the last line.
- Families: `run.*` (created, state_changed, power.*, clone, env, discovery,
  triage, selection.confirmed, artifacts), `function.*` (started,
  tests.write, tests.run, capture, baseline, decision, merge, completed),
  `candidate.*` (write, check, rejected, bench.queued, bench), `llm.usage`
  (tokens, cost, cassette `off|hit|recorded|miss`) and `log`.

**Schema.** The Pydantic models are the contract.
`netzero export-schema` writes `web/src/gen/schema.json`, and `make types`
also regenerates the TypeScript types in `web/src/gen/`.

## Configuration

Settings come from environment variables or `.env` with the `NETZERO_`
prefix. Nested fields use `__`. Defaults are from `netzero/config.py`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `NETZERO_HOST`, `NETZERO_PORT` | `127.0.0.1`, `8000` | Bind address for `serve` |
| `NETZERO_RUNS_DIR` | `<repo>/runs` | Where runs are stored |
| `NETZERO_COUNTRY` | unset (world average) | ISO3 code for grid intensity |
| `NETZERO_STAGES__<STAGE>__MODEL` | `claude-haiku-4-5` | `<STAGE>` is one of `TRIAGE`, `TESTS`, `TESTS_REPAIR`, `REWRITE`, `REWRITE_REPAIR` |
| `NETZERO_STAGES__<STAGE>__EFFORT` | unset | `low\|medium\|high\|xhigh\|max`, for models that support it |
| `NETZERO_STAGES__<STAGE>__THINKING` | `false` | Adaptive or budgeted thinking where the model supports it |
| `NETZERO_STAGES__<STAGE>__MAX_TOKENS` | `8000` (triage `6000`; setting any other `NETZERO_STAGES__TRIAGE__*` variable resets triage to `8000` unless you also set this) | Output budget. A truncated answer is retried once with twice the budget |
| `NETZERO_LLM_CONCURRENCY` | `4` | Parallel Claude requests |
| `NETZERO_CASSETTE_MODE` | `off` | `off\|record\|replay` for live runs. Demo runs always replay and `netzero record --cassettes` always records, whatever this says. Cassettes always live in `examples/demo-repo/.netzero-cassettes` |
| `NETZERO_MAX_FUNCTIONS`, `NETZERO_PRESELECT`, `NETZERO_PRESELECT_MIN_SCORE`, `NETZERO_TRIAGE_LLM_TOP` | `20`, `8`, `0.15`, `60` | Eligible functions kept and listed (up to as many skipped ones are listed too), preselection count and minimum score, functions sent to Claude for ranking (capped by `NETZERO_MAX_FUNCTIONS`) |
| `NETZERO_TEST_REPAIRS`, `NETZERO_CANDIDATE_REPAIRS` | `2`, `1` | Repair attempts |
| `NETZERO_N_TRIALS`, `NETZERO_TRIAL_TARGET_S`, `NETZERO_BASELINE_CV_WARN_PCT` | `16`, `0.75`, `25.0` | Trials per arm, target trial length (s), noisy-baseline warning |
| `NETZERO_ALPHA`, `NETZERO_MIN_EFFECT_PCT` | `0.05`, `5.0` | Acceptance gate |
| `NETZERO_CLONE_TIMEOUT_S`, `NETZERO_CLONE_MAX_MB`, `NETZERO_CLONE_MAX_FILES` | `120`, `200`, `20000` | Clone limits |
| `NETZERO_INSTALL_TIMEOUT_S`, `NETZERO_TEST_TIMEOUT_S` | `600`, `120` | Install and per-step timeouts |
| `NETZERO_EUR_PER_KWH`, `NETZERO_EU_ETS_EUR_PER_T`, `NETZERO_VCM_EUR_PER_T`, `NETZERO_CALLS_PER_YEAR` | `0.20`, `70`, `15`, `1000000` | Assumptions for the projections |
| `NETZERO_SSE_HEARTBEAT_S` | `15` | SSE idle heartbeat |
| `NETZERO_DATABASE_URL`, `NETZERO_HISTORY_SYNC_S` | unset, `1.5` | Postgres for run history, and how often the server copies new events into it |
| `NETZERO_FAKE_PIPELINE`, `NETZERO_FAKE_SCENARIO`, `NETZERO_FAKE_SPEED`, `NETZERO_FAKE_SEED` | `false`, `demo`, `1.0`, `0` | Server runs the scripted FakePipeline instead of the real one (UI development; no key needed). Scenarios: `demo`, `short`, `fail_env` |

## Demo repo and cassettes

`examples/demo-repo` is a small stdlib-only package with 14 functions.

| Function | Expected outcome |
| --- | --- |
| `algos.graph:Graph.shortest_path_lengths`, `algos.pairs:has_pair_with_sum`, `algos.primes:primes_below`, `algos.strings:levenshtein`, `datakit.aggregate:total_by_category`, `datakit.series:moving_average`, `textkit.dedupe:dedupe_preserve_order`, `textkit.freq:word_frequencies` | accepted (preselected) |
| `algos.search:index_of_sorted`, `datakit.stats:zscore`, `textkit.fields:join_fields` | no_significant_win |
| `datakit.rank:top_k_inplace` | all_rejected (every candidate and repair fails the differential check or is identical) |
| `algos.walk:random_walk`, `datakit.rates:fetch_exchange_rates` | skipped by triage (nondeterministic; network I/O) |

"Expected" is what the stories were written to show; the run's own benchmark
decides, so outcomes near the 5% threshold can vary between machines.

**The demo cassettes are hand-written.** No API key was available while this
was built, so no reply in `examples/demo-repo/.netzero-cassettes` was
recorded from Claude. People wrote each test file, candidate rewrite and
repair in `examples/demo-stories/` (one directory per function, plus
`triage.json`). `netzero demo-cassettes` turns them into the 60 cassette
files. Every cassette has `"model": "hand-written"` and no token usage, so a
demo run costs $0. Everything else in a demo run is real. See
`examples/demo-stories/README.md` for the format and per-candidate
expectations.

- **Rebuild** after editing a story: `make demo-cassettes`. It deletes the
  cassette folder first, so it also discards any recorded cassettes.
- **Re-record from Claude**: `uv run netzero record --cassettes` (needs
  `ANTHROPIC_API_KEY`; overwrites the cassette files the run touches).
- **Bundled replay**: `make record-demo` (`uv run netzero record`) runs the
  demo on a fixed mix of 8 functions and writes
  `web/public/replays/demo.events.jsonl`, which may not exist yet in your
  checkout. The entry it adds to `index.json` says the replies are
  hand-written even after `--cassettes`; edit it if you re-record. Run
  `make web` afterwards so `netzero serve` serves the new file.
- **Replay index**: `web/public/replays/index.json` lists
  `{id, title, description, src, functions, duration_ms}`. Today it holds one
  synthetic run (`synthetic.events.jsonl`, six functions, one per outcome)
  whose events are scripted, not measured.

## Run history in Postgres

Optional. With `NETZERO_DATABASE_URL` set, the server copies every run's
events into Postgres and the web UI's History tab shows them: one card per
repository, runs over time, which functions improved across runs, and the
diff each run merged.

```sh
make db        # Postgres 17 on 127.0.0.1:54320: Docker or Podman via compose.yaml, else a local cluster in .netzero-db/
# uncomment NETZERO_DATABASE_URL in .env, then restart the server
uv run netzero db import web/public/replays/*.events.jsonl   # optional: something to look at
```

`make db-stop` stops it and keeps the data. A Postgres you already run works
too; the database must be UTF8.

**How it is stored** (`netzero/history/`, schema in
`migrations/0001_init.sql`). It is an event store. Each run is a stream,
`run:<id>`. The `events` table holds every line of `events.jsonl` exactly as
written, with `type`, `ts`, `data` and the scope pulled out into generated
columns. A trigger refuses `UPDATE`, `DELETE` and `TRUNCATE` on it, so it only
grows. Appends check the stream's version (optimistic concurrency), so two
writers can't interleave a run. `projects`, `runs` and `function_results` are
read models that a checkpointed projector folds from the events, using the
same rules as `run.json`. `netzero db rebuild` drops and refolds them.

The files under `runs/` stay the source of truth. Postgres is a copy: the
server ships new lines every 1.5 s, picks up runs that `netzero run` wrote
while it was down, and catches up after the database comes back.

## Web UI (in progress)

The frontend is being built now; expect changes. `web/src/router.tsx`
currently defines `/` (landing page), `/runs/:runId` (a run, live over SSE or
finished), `/replay` (replay of a bundled run), `/history` and
`/history/:projectId` (run history from Postgres) and `*` (not found).

To work on the UI: `make run` serves the production build via `netzero
serve`; `make dev` runs `netzero serve --reload` plus the Vite dev server,
which proxies `/api`; `NETZERO_FAKE_PIPELINE=1 make dev` gives scripted runs
with no key, git or uv work.

## Development

| Target | What it does |
| --- | --- |
| `make setup` | `uv sync`, `uv python install 3.12`, warm the demo install cache, `npm ci` in `web/`, `netzero doctor` |
| `make help` | Short list of the main targets |
| `make web` | Build the UI into `web/dist/` (`npm --prefix web run build`) |
| `make run` / `make serve` / `make dev` | Build the UI and serve / serve only / API with reload + Vite dev server |
| `make test` | `uv run pytest` (unit + integration; excludes `e2e`) |
| `make test-e2e` | `pytest -m e2e`: a full demo-repo run with cassette replay |
| `make test-web` | `tsc -b` + vitest in `web/` |
| `make lint` | `ruff check` + `ruff format --check` on `netzero` and `tests` |
| `make types` | `netzero export-schema` + `npm run gen:types` (regenerates `web/src/gen/`) |
| `make db` / `make db-stop` | Start or stop Postgres for run history (see [Run history in Postgres](#run-history-in-postgres)) |
| `make record-demo`, `make demo-cassettes`, `make doctor` | See the CLI reference |
| `make clean` | Remove `runs/`, `.netzero-cache/` and `web/dist/` (the literal `runs/`; a custom `NETZERO_RUNS_DIR` is left alone) |

**Tests.** `tests/unit` holds fast tests; `tests/integration` covers the
API/SSE, bench, env, per-function flow, harness, orchestrator, prelude and
sandbox runner (some skip when `uv` is missing); `tests/e2e` holds the demo
run. Markers: `slow`
(benchmarks and other long tests; included in `make test`, skip with
`-m "not slow and not e2e"`) and `e2e` (excluded by default via `addopts`).

Run `make types` whenever `netzero/events.py` or `netzero/api/schemas.py`
changes; `uv run netzero export-schema --check` tells you whether the
committed schema is stale.

**Layout.**

```
netzero/cli.py       Typer CLI (entry point `netzero`)
netzero/config.py    Settings (NETZERO_*, .env)
netzero/events.py    Event and run models: the contract with the UI
netzero/api/         FastAPI app: routes, SSE, artifacts, SPA static files, schema root
netzero/pipeline/    Orchestrator, state machine, clone/env/discovery/triage, per-function flow, store, FakePipeline
netzero/history/     Postgres run history: event store, projector, queries, background sync
netzero/bench/       Power probe, calibration, bench lane, statistics, units
netzero/llm/         Anthropic client, prompts, structured outputs, cassettes, demo stories, pricing
netzero/sandbox/     Child processes, env scrubbing, process groups, and the in-venv harness (pytest plugin, capture, diffcheck, bench worker, rlimit launcher)
examples/            demo-repo (the target) and demo-stories (hand-written model replies)
web/                 Vite + React UI (in progress)
tests/               unit, integration, e2e
```

## Safety and limits

- **It runs the target repository's code on your machine**: the generated
  tests, input capture, differential checks and bench workers. Installing
  dependencies can also run the project's build code. There is no container
  or VM sandbox. What it does instead:
  - **A venv per run**, under `runs/<id>/venv`.
  - **A scrubbed environment**: sandboxed code gets an allow-listed
    environment (no API keys, tokens or cloud credentials, single-threaded
    BLAS, `HOME`/`TMPDIR` inside the run folder). git and uv run with
    secret-looking variables removed and global/system git config disabled.
  - **rlimits** for pytest, input capture and the differential check: CPU
    time is the step timeout plus 5 s, files 512 MB, file descriptors 4096 (a
    limit the OS refuses is skipped). Bench workers, the import probe and
    dependency installs get no rlimits; they are bounded only by wall-clock
    timeouts and killed with their process group.
  - **Process groups**: every child gets its own, registered in
    `procs.jsonl` and killed on cancel, timeout or crash recovery.
- The static checks keep obvious I/O, network and subprocess use out of
  generated tests and rewrites. They do not constrain the repository's own
  code.
- The clone accepts only public `https://github.com/<owner>/<repo>` URLs:
  shallow, no submodules, with size and file limits.
- The server has no authentication and binds to 127.0.0.1; do not expose it.
- Correctness evidence covers only the generated tests and captured inputs.
  Review the patch before you apply it.
- Target code runs on Python 3.12, and functions are optimized one at a time
  on one machine. Benchmarks are slow by design: 16 trials per arm at about
  0.75 s each.
