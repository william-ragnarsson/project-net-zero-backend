# CLAUDE.md

net-zero rewrites Python functions for less CO2 per call and measures the result. It has two halves: a FastAPI + SSE backend and Typer CLI in `netzero/`, and a Vite + React UI in `web/` that draws each run as a timeline. The README is the user-facing reference (pipeline, carbon method, CLI, HTTP API, config), and `make help` lists the commands. This file covers what those leave out: how the two halves connect, and what to check on the other side when you change one.

## The event log is the interface

A run is a folder, `runs/<id>/`. Its `events.jsonl` is the source of truth and everything else is a **fold** of it. The backend and the UI share no other state.

```
real pipeline / FakePipeline ─emit─▶ EventBus ─append─▶ runs/<id>/events.jsonl
                                        ├─▶ Projection ─▶ run.json ─▶ GET /api/runs/{id}
                                        ├─▶ SSE GET /api/runs/{id}/events ─▶ web reducer ─▶ RunModel
                                        └─▶ CLI renderer (netzero/render.py)
web/src/synth ─▶ web/public/replays/*.events.jsonl ─▶ replay driver ─▶ web reducer
```

- **Contract**: `netzero/events.py` (events and run records) and `netzero/api/schemas.py` (REST bodies plus the `Contract` export root). `web/src/gen/` is generated from them by `make types`. Edit the Python, regenerate, and commit both. `tests/unit/test_events.py` fails when `schema.json` is stale.
- **Grammar**: `netzero/pipeline/grammar.py` holds the ordering rules the reducer relies on. `seq` starts at 1 with no gaps. Every `X.started` is closed by exactly one `X.completed` with the same `(function_id, candidate_id, attempt)`. Only one function is open at a time. Candidate events come before `function.decision`. The terminal event is last and directly follows `run.state_changed`. Every fake, recorded and replayed run in the tests must pass it, and `uv run netzero replay --check <file>` checks any log.
- **Folds**: `netzero/pipeline/projection.py` builds `RunDetail` / `run.json`, and `web/src/state/reducer.ts` builds `RunModel`. They mirror each other on purpose. For example, both replace the TDP estimate with a calibrated `p_core_w`, and both use discovery's `heuristic_ranked` until triage completes. When you change a rule in one fold, change it in the other. `netzero/render.py` is a third consumer.
- **Producers**: three things emit events. The real pipeline (`pipeline/orchestrator.py`, `function_flow.py` and others), `FakePipeline` (`pipeline/fake.py`, turned on with `NETZERO_FAKE_PIPELINE=1`), and the TypeScript synthetic scenario (`web/src/synth/`) behind the bundled replay. A new event or payload field needs all three.

## Streaming behaviour both sides depend on

The README's HTTP API section has the full SSE spec. These are the parts that shape code on both sides:

- The bus writes each event to disk before publishing it. `emit` is synchronous and must be called from the event loop, never from a worker thread.
- Each SSE frame carries `id: <seq>`, and a client resumes with `Last-Event-ID` or `?after=`. The server disconnects a slow subscriber, which then has to resume, and every reconnect resends events the client may already have. So the UI gets duplicates and batches of any size. The reducer skips events with `seq <= lastSeq`, and it must reach the same state whether events arrive one at a time or in a batch (`reducer.test.ts` checks this).
- `heartbeat` frames exist only on the SSE stream (`event: heartbeat`, no id). They are never written to the log and are not a `RunEvent`.
- The stream ends after the terminal event. A finished run whose client has every event answers 204, which stops `EventSource` from reconnecting.
- If another process (the CLI) owns the run, the server streams it by tailing the file. A live event has to make sense when read back from disk.
- The UI dispatches events once per animation frame (`createEventPump` in `web/src/state/store.ts`), so a fast replay or a reconnect backlog arrives as one large batch.
- The Vite dev proxy turns off timeouts and compression for `/api` so the stream isn't buffered. Keep both settings if you edit `vite.config.ts`.
- The reducer counts unknown event types in `RunModel.unknownTypes` instead of throwing, so an older UI build keeps working on a newer log.

Units that are easy to misread: `delta_pct` is negative for a win (`-37.2` means 37.2% less CO2 per call). Grams and kWh are per call, savings are per 1M calls, and `ts` is epoch milliseconds.

## Checking the other side

The two halves are coupled only through the log, so a change that looks local can still break the other side. Before you finish a change on either side, work out what it does to the other.

**When you change the backend**, check whether the change alters which events are emitted, their order, the shape of a payload, or what a field means or measures. If it does:

- Edit the contract, run `make types`, then fix what `make test-web` reports. A new event type fails `tsc` and the reducer test until it has a handler.
- Re-check the grammar. If a rule really changed, update `grammar.py`, because the reducer depends on those rules.
- Teach `FakePipeline` and `web/src/synth/` to emit the change. Then regenerate the bundled replay with `npm --prefix web run synth` (a vitest compares the committed file with the scenario) and validate it with `uv run netzero replay --check web/public/replays/synthetic.events.jsonl`.
- Copy any projection change into the reducer and selectors.
- Every model uses `extra="forbid"`. Renaming or removing a field means existing `runs/` logs and committed replays no longer parse. Add new fields with defaults where you can, and bump `SCHEMA_VERSION` when a break can't be avoided.

**When you change the frontend**, check what the change assumes about the stream:

- Does it still work on a partial log during a run, on a replay seeked to any seq, with duplicate events, and with a large batch at once?
- Does it depend on an order the grammar guarantees, or only on one the current pipeline happens to produce? If it needs a new guarantee, add the rule to `grammar.py` so the backend has to keep it.
- Does it need data that no event carries? Add that data to the contract on the backend. Don't reconstruct it from log lines or timing.
- Totals shown before `run.completed` must match that event's `summary` (`reducer.test.ts` checks this).

## Current state (October 2026)

- PR #17 replaced the old blocking `POST /optimize` pipeline (`src/`, `server.py`, LangGraph) with this event stream. Notes that mention those files are out of date.
- The UI only has the replay path so far: `/replay` plays bundled files through the reducer and into the timeline. `/runs/:runId` (`web/src/routes/RunPage.tsx`) is a placeholder. The live SSE view, the step drawer, motion and the run summary aren't built yet. For the live view, the store already supports the flow: `hydrate` from `GET /api/runs/{id}/events.jsonl`, then resume SSE from `hydratedThroughSeq`.
- `NETZERO_FAKE_PIPELINE=1 make dev` runs scripted live runs with no API key, git or uv. It's the quickest way to drive the UI with a real stream.
- The demo cassettes are generated from the hand-written `examples/demo-stories`. `make demo-cassettes` deletes any recorded cassettes.

## Keeping this file current

This file is a loose working map, not a spec. When a change affects the connection between the halves, update the matching lines here in the same change. That includes a new event, a new grammar rule, a new producer, a change to a fold rule, the live view landing, or anything above going stale. Keep it short, and point to code instead of copying it.
