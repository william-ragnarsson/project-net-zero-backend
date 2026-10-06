"""FakePipeline: grammar-valid scripted runs, without the orchestrator."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import types
import warnings
import zipfile
from pathlib import Path

import pytest

from netzero.config import Settings
from netzero.errors import EnvError
from netzero.events import (
    ErrorInfo,
    RunCancelledData,
    RunCompletedData,
    RunFailedData,
    RunSource,
)
from netzero.pipeline.discovery import parse_module
from netzero.pipeline.fake import (
    CATALOG,
    DEMO_SELECTION,
    SHORT_SELECTION,
    FakeFunction,
    FakePipeline,
    expected_outcome,
)
from netzero.pipeline.grammar import assert_grammar
from netzero.pipeline.run import RunContext
from netzero.pipeline.store import RunStore, read_complete_lines

LEV = "algos.strings:levenshtein"
MOVING = "datakit.series:moving_average"
NORMALIZE = "textkit.normalize:normalize_whitespace"
PARSE = "datakit.io_free:parse_records"
TOTALS = "datakit.aggregate:total_by_category"
RATES = "datakit.rates:fetch_exchange_rates"
UNKNOWN = "nope.missing:ghost"


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    import os

    for key in list(os.environ):
        if key.startswith("NETZERO_"):
            monkeypatch.delenv(key)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    # the scripted fates assume the defaults
    assert (s.preselect, s.test_repairs, s.candidate_repairs) == (8, 2, 1)
    assert (s.n_trials, s.alpha, s.min_effect_pct) == (16, 0.05, 5.0)
    return s


def make_ctx(root: Path, settings: Settings, *, auto_select: bool = True) -> RunContext:
    store = RunStore(root)
    run_id = store.new_run_id("demo")
    rp = store.paths(run_id)
    rp.mkdirs()
    ctx = RunContext(
        run_id=run_id,
        paths=rp,
        settings=settings,
        store=store,
        mode="demo",
        source=RunSource(kind="demo"),
        auto_select=auto_select,
    )
    ctx.emit_created()
    return ctx


def complete(ctx: RunContext) -> list[dict]:
    ctx.transition("completed")
    ctx.emit(
        "run.completed",
        RunCompletedData(summary=ctx.projection.totals(duration_ms=ctx.elapsed_ms())),
    )
    ctx.close()
    return events_of(ctx)


def events_of(ctx: RunContext) -> list[dict]:
    lines = read_complete_lines(ctx.paths.events)[0]
    assert_grammar(lines)
    return [json.loads(line) for line in lines]


async def run_fake(root: Path, settings: Settings, **kw) -> tuple[RunContext, list[dict]]:
    ctx = make_ctx(root, settings)
    await FakePipeline(speed=1000, **kw)(ctx)
    return ctx, complete(ctx)


def of_type(events: list[dict], t: str, **match) -> list[dict]:
    return [e for e in events if e["type"] == t and all(e.get(k) == v for k, v in match.items())]


def outcomes(events: list[dict]) -> dict[str, str]:
    return {e["function_id"]: e["data"]["outcome"] for e in of_type(events, "function.completed")}


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


async def test_demo_auto_run(tmp_path: Path, settings: Settings) -> None:
    ctx, events = await run_fake(tmp_path, settings)

    assert outcomes(events) == {fid: expected_outcome(fid) for fid in DEMO_SELECTION}
    d = ctx.detail
    assert d.functions_done == 8
    assert d.counts_by_outcome == {"accepted": 6, "all_rejected": 1, "no_significant_win": 1}
    assert d.g_saved_per_1m_calls > 0
    assert d.mean_reduction_pct is not None and d.mean_reduction_pct < -5
    assert d.state == "completed"

    [confirmed] = of_type(events, "run.selection.confirmed")
    assert confirmed["data"]["auto"] is True
    assert confirmed["data"]["function_ids"] == list(DEMO_SELECTION)

    # the triage re-ranks the heuristic order
    [disc] = of_type(events, "run.discovery.completed")
    [tri] = of_type(events, "run.triage.completed")
    heuristic = [i["function_id"] for i in disc["data"]["heuristic_ranked"]]
    llm = [i["function_id"] for i in tri["data"]["items"]]
    assert sorted(heuristic) == sorted(llm) == sorted(CATALOG)
    assert heuristic != llm
    assert all(i["llm_potential"] is None for i in disc["data"]["heuristic_ranked"])
    assert tri["data"]["preselected"] == list(DEMO_SELECTION)
    assert {i["function_id"] for i in tri["data"]["items"] if i["preselected"]} == set(
        DEMO_SELECTION
    )
    assert {i["function_id"] for i in tri["data"]["items"] if i["skip_reason"]} == {
        RATES,
        "algos.walk:random_walk",
    }


async def test_short_run(tmp_path: Path, settings: Settings) -> None:
    ctx, events = await run_fake(tmp_path, settings, scenario="short")
    assert list(outcomes(events)) == list(SHORT_SELECTION)
    assert ctx.detail.counts_by_outcome == {
        "accepted": 1,
        "all_rejected": 1,
        "no_significant_win": 1,
    }


async def test_preselect_setting_trims_demo(tmp_path: Path, settings: Settings) -> None:
    settings = settings.model_copy(update={"preselect": 2})
    _, events = await run_fake(tmp_path, settings)
    assert list(outcomes(events)) == list(DEMO_SELECTION[:2])


async def test_manual_selection(tmp_path: Path, settings: Settings) -> None:
    ctx = make_ctx(tmp_path, settings, auto_select=False)
    task = asyncio.create_task(FakePipeline(speed=1000)(ctx))
    for _ in range(1000):
        if ctx.awaiting_selection:
            break
        await asyncio.sleep(0)
    assert ctx.awaiting_selection and not task.done()
    picked = [LEV, MOVING, NORMALIZE, PARSE, UNKNOWN, RATES, TOTALS, LEV]
    ctx.select(picked)
    await task
    events = complete(ctx)

    [confirmed] = of_type(events, "run.selection.confirmed")
    assert confirmed["data"]["auto"] is False
    assert outcomes(events) == {
        LEV: "accepted",
        MOVING: "reverted",
        NORMALIZE: "skipped_untestable",
        PARSE: "skipped_capture",
        UNKNOWN: "failed",
        RATES: "failed",
        TOTALS: "accepted",
    }
    done = {e["function_id"]: e["data"] for e in of_type(events, "function.completed")}
    assert "not optimizable" in done[RATES]["reason"]
    assert ctx.detail.counts_by_outcome["accepted"] == 2

    # levenshtein: the first tests fail on the original, one repair fixes them
    runs = of_type(events, "function.tests.run.completed", function_id=LEV)
    assert [(e["attempt"], e["data"]["ok"]) for e in runs] == [(0, False), (1, True), (1, True)]
    [repair] = of_type(events, "function.tests.write.started", function_id=LEV, attempt=1)
    assert repair["data"]["kind"] == "repair"
    assert repair["data"]["failures_in"] == [
        "tests/netzero/test_algos_strings.py::test_classic_example"
    ]
    # normalize_whitespace: every repair fails -> 3 write/run attempts
    writes = of_type(events, "function.tests.write.started", function_id=NORMALIZE)
    assert [e["attempt"] for e in writes] == [0, 1, 2]
    assert not of_type(events, "function.capture.started", function_id=NORMALIZE)
    # parse_records: capture fails, no candidates
    [cap] = of_type(events, "function.capture.completed", function_id=PARSE)
    assert cap["data"]["ok"] is False and cap["data"]["deterministic"] is False
    assert not of_type(events, "candidate.write.started", function_id=PARSE)
    # moving_average: decided a winner, merge reverted
    [merge] = of_type(events, "function.merge.completed", function_id=MOVING)
    assert merge["data"]["ok"] is False and merge["data"]["reverted"] is True
    assert merge["data"]["differential"]["mismatches"]
    assert done[MOVING]["winner"] == "A" and done[MOVING]["g_saved_per_1m_calls"] is None


@pytest.mark.parametrize("seed", [0, 1, 2, 3])
async def test_every_catalog_function_meets_its_fate(
    tmp_path: Path, settings: Settings, seed: int
) -> None:
    """The noise is seeded per item, but no seed may flip a scripted outcome."""
    ctx = make_ctx(tmp_path, settings, auto_select=False)
    task = asyncio.create_task(FakePipeline(speed=1000, seed=seed)(ctx))
    while not ctx.awaiting_selection:
        await asyncio.sleep(0)
    ctx.select(list(CATALOG))
    await task
    events = complete(ctx)
    assert outcomes(events) == {fid: expected_outcome(fid) for fid in CATALOG}


async def test_selection_is_deduplicated(tmp_path: Path, settings: Settings) -> None:
    ctx = make_ctx(tmp_path, settings, auto_select=False)
    task = asyncio.create_task(FakePipeline(speed=1000)(ctx))
    while not ctx.awaiting_selection:
        await asyncio.sleep(0)
    ctx.select([LEV, LEV])
    await task
    events = complete(ctx)
    assert len(of_type(events, "function.started")) == 1


async def test_fail_env(tmp_path: Path, settings: Settings) -> None:
    ctx = make_ctx(tmp_path, settings)
    with pytest.raises(EnvError) as exc_info:
        await FakePipeline(speed=1000, scenario="fail_env")(ctx)
    [env] = of_type(events_of_partial(ctx), "run.env.completed")
    assert env["data"]["ok"] is False
    assert env["data"]["error"]["kind"] == "env_error"
    ctx.transition("failed")
    ctx.emit("run.failed", RunFailedData(stage="installing", error=exc_info.value.info()))
    ctx.close()
    events = events_of(ctx)
    assert events[-1]["type"] == "run.failed"
    assert not of_type(events, "run.discovery.started")


def events_of_partial(ctx: RunContext) -> list[dict]:
    lines = read_complete_lines(ctx.paths.events)[0]
    return [json.loads(line) for line in lines]


async def test_cancellation_mid_candidates(tmp_path: Path, settings: Settings) -> None:
    ctx = make_ctx(tmp_path, settings)
    sub = ctx.bus.subscribe()
    task = asyncio.create_task(FakePipeline(speed=1000)(ctx))
    while True:
        item = await asyncio.wait_for(sub.get(), 5)
        assert item is not None
        _, line = item
        if json.loads(line)["type"] == "candidate.write.started":
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    events = events_of_partial(ctx)
    [done] = of_type(events, "function.completed")
    assert done["data"]["outcome"] == "cancelled"
    seq0 = ctx.last_seq
    await asyncio.sleep(0.05)
    assert ctx.last_seq == seq0  # nothing emits after the pipeline raised

    ctx.bus.close_open_steps(ErrorInfo(kind="cancelled", message="cancelled by user"))
    ctx.transition("cancelled")
    ctx.emit("run.cancelled", RunCancelledData(at_state="optimizing"))
    ctx.close()
    events = events_of(ctx)
    assert events[-1]["type"] == "run.cancelled"


@pytest.mark.parametrize("cancel_after", [5, 40, 120, 300])
async def test_cancel_anywhere_is_grammar_valid(
    tmp_path: Path, settings: Settings, cancel_after: int
) -> None:
    ctx = make_ctx(tmp_path, settings)
    task = asyncio.create_task(FakePipeline(speed=1000)(ctx))
    while ctx.last_seq < cancel_after and not task.done():
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    at_state = ctx.detail.state
    seq0 = ctx.last_seq
    await asyncio.sleep(0.02)
    assert ctx.last_seq == seq0
    ctx.bus.close_open_steps(ErrorInfo(kind="cancelled", message="cancelled by user"))
    ctx.transition("cancelled")
    ctx.emit("run.cancelled", RunCancelledData(at_state=at_state))
    ctx.close()
    events_of(ctx)


# ---------------------------------------------------------------------------
# Determinism + numbers
# ---------------------------------------------------------------------------


def _normalized(events: list[dict], run_id: str) -> list[str]:
    out = []
    for e in events:
        e = dict(e)
        e.pop("ts", None)
        data = e.get("data")
        if isinstance(data, dict):
            data = dict(data)
            data.pop("duration_ms", None)
            if e["type"] == "run.artifacts.completed" and data.get("zip"):
                data["zip"] = {k: v for k, v in data["zip"].items() if k != "sha256"}
            if e["type"] == "run.completed":
                data["summary"] = {k: v for k, v in data["summary"].items() if k != "duration_ms"}
            if e["type"] == "run.created":
                data.pop("settings", None)
            e["data"] = data
        out.append(json.dumps(e, sort_keys=True).replace(run_id, "<run>"))
    return out


async def test_same_seed_same_events(tmp_path: Path, settings: Settings) -> None:
    c1, e1 = await run_fake(tmp_path / "a", settings, seed=7)
    c2, e2 = await run_fake(tmp_path / "b", settings, seed=7)
    c3, e3 = await run_fake(tmp_path / "c", settings, seed=8)
    n1, n2, n3 = (
        _normalized(e1, c1.run_id),
        _normalized(e2, c2.run_id),
        _normalized(e3, c3.run_id),
    )
    assert n1 == n2
    assert n1 != n3
    # the scripted fates do not depend on the seed
    assert outcomes(e1) == outcomes(e3)


async def test_numbers_are_consistent(tmp_path: Path, settings: Settings) -> None:
    ctx, events = await run_fake(tmp_path, settings)
    benches = {
        (e["function_id"], e["candidate_id"]): e["data"]["stats"]
        for e in of_type(events, "candidate.bench.completed")
    }
    assert benches
    kg = ctx.detail.power.grid.kg_per_kwh  # type: ignore[union-attr]
    for st in benches.values():
        assert st["delta_ci_pct"]["lo"] <= st["delta_pct"] <= st["delta_ci_pct"]["hi"]
        assert len(st["original"]["trials_g"]) == st["n_trials"] == 16
        assert len(st["candidate"]["trials_g"]) == 16
        o, c = st["original"], st["candidate"]
        assert st["g_saved_per_1m_calls"] == pytest.approx(
            (o["g_per_call"]["mean"] - c["g_per_call"]["mean"]) * 1e6
        )
        assert o["g_per_call"]["mean"] == pytest.approx(o["kwh_per_call"]["mean"] * kg * 1000)
        ratio = c["g_per_call"]["mean"] / o["g_per_call"]["mean"]
        assert st["delta_pct"] == pytest.approx((ratio - 1) * 100, abs=0.01)
        assert st["cpu_time_delta_pct"] == pytest.approx(st["delta_pct"], abs=3)
        assert st["sanity_ok"] is True and st["p_holm"] is None

    for dec in of_type(events, "function.decision"):
        fid = dec["function_id"]
        for entry in dec["data"]["ranking"]:
            if entry["status"] == "eligible":
                assert entry["p_holm"] >= entry["p_value"]
                sig = (
                    entry["p_holm"] < settings.alpha
                    and entry["delta_ci_pct"]["hi"] < 0
                    and entry["delta_pct"] <= -settings.min_effect_pct
                )
                assert entry["significant"] is sig
        winner = dec["data"]["winner"]
        [done] = of_type(events, "function.completed", function_id=fid)
        if winner and done["data"]["outcome"] == "accepted":
            st = benches[(fid, winner)]
            assert done["data"]["delta_pct"] == st["delta_pct"]
            assert done["data"]["delta_ci_pct"] == st["delta_ci_pct"]
            assert done["data"]["g_saved_per_1m_calls"] == st["g_saved_per_1m_calls"]
            assert done["data"]["kwh_saved_per_1m_calls"] == st["kwh_saved_per_1m_calls"]
            assert done["data"]["reason"].startswith(f"{winner}: ")
            eligible = [e for e in dec["data"]["ranking"] if e["significant"]]
            assert min(eligible, key=lambda e: e["delta_pct"])["candidate_id"] == winner

    usage = of_type(events, "llm.usage")
    assert usage[-1]["data"]["run_cost_usd"] == pytest.approx(
        sum(u["data"]["cost_usd"] for u in usage)
    )
    assert ctx.detail.llm_cost_usd == pytest.approx(usage[-1]["data"]["run_cost_usd"])
    assert {u["data"]["stage"] for u in usage} == {
        "triage",
        "tests",
        "tests_repair",
        "rewrite",
        "rewrite_repair",
    }
    assert all(u["data"]["model"] == "claude-haiku-4-5" for u in usage)


async def test_candidate_lifecycle(tmp_path: Path, settings: Settings) -> None:
    _, events = await run_fake(tmp_path, settings)
    primes = "algos.primes:primes_below"
    # B's tests fail, one repair passes; later events of B use attempt 1
    b = [e for e in events if e["function_id"] == primes and e.get("candidate_id") == "B"]
    assert [(e["type"], e["attempt"]) for e in b if e["type"].endswith(".started")] == [
        ("candidate.write.started", 0),
        ("candidate.check.started", 0),
        ("candidate.write.started", 1),
        ("candidate.check.started", 1),
        ("candidate.bench.started", 1),
    ]
    assert not of_type(events, "candidate.rejected", function_id=primes)
    [queued] = of_type(events, "candidate.bench.queued", function_id=primes, candidate_id="B")
    assert queued["attempt"] == 1
    # baseline overlaps the candidate writes
    fn_events = [e for e in events if e["function_id"] == primes]
    types_ = [e["type"] for e in fn_events]
    assert types_.index("function.baseline.started") < types_.index("candidate.write.completed")
    # benches never overlap
    open_bench = 0
    for e in events:
        if e["type"] in ("candidate.bench.started", "function.baseline.started"):
            open_bench += 1
            assert open_bench == 1
        elif e["type"] in ("candidate.bench.completed", "function.baseline.completed"):
            open_bench -= 1

    top_k = "datakit.rank:top_k_inplace"
    rejected = {
        e["candidate_id"]: (e["attempt"], e["data"]["reason"])
        for e in of_type(events, "candidate.rejected", function_id=top_k)
    }
    assert rejected == {
        "A": (1, "differential_mismatch"),
        "B": (0, "tests_failed"),
        "C": (0, "differential_mismatch"),
    }
    [dec] = of_type(events, "function.decision", function_id=top_k)
    assert dec["data"]["outcome"] == "all_rejected"
    assert {r["status"] for r in dec["data"]["ranking"]} == {"rejected"}

    freq = "textkit.freq:word_frequencies"
    [wc] = of_type(events, "candidate.write.completed", function_id=freq, candidate_id="C")
    assert wc["data"]["ok"] is False and wc["data"]["error"]["kind"] == "llm_error"
    [use] = of_type(events, "llm.usage", function_id=freq, candidate_id="C")
    assert use["data"]["stop_reason"] == "max_tokens"

    graph = "algos.graph:Graph.shortest_path_lengths"
    [chk] = of_type(events, "candidate.check.completed", function_id=graph, candidate_id="C")
    assert chk["data"]["ok"] is False and chk["data"]["error"]["kind"] == "timeout"


async def test_artifacts_written(tmp_path: Path, settings: Settings) -> None:
    ctx, events = await run_fake(tmp_path, settings)
    [art] = of_type(events, "run.artifacts.completed")
    data = art["data"]
    run_id = ctx.run_id
    patch = ctx.paths.out / f"{run_id}.patch"
    zpath = ctx.paths.out / f"{run_id}.zip"
    assert data["patch"]["bytes"] == patch.stat().st_size
    assert data["patch"]["sha256"] == hashlib.sha256(patch.read_bytes()).hexdigest()
    assert data["patch"]["url"] == f"/api/runs/{run_id}/artifacts/patch"
    assert data["zip"]["sha256"] == hashlib.sha256(zpath.read_bytes()).hexdigest()
    accepted = [fid for fid, o in outcomes(events).items() if o == "accepted"]
    assert data["patch"]["files_changed"] == len(accepted)
    assert [d["function_id"] for d in data["function_diffs"]] == accepted
    for ref in data["function_diffs"]:
        assert "%3A" in ref["url"]
        diff = (ctx.paths.fn(ref["function_id"]) / "merge.diff").read_text()
        [merge] = of_type(events, "function.merge.completed", function_id=ref["function_id"])
        assert diff == merge["data"]["diff"]
        assert diff in patch.read_text()
        assert len(merge["data"]["commit_sha"]) == 40
    with zipfile.ZipFile(zpath) as zf:
        assert set(zf.namelist()) == {"netzero.patch", "netzero-report.json"}
        report = json.loads(zf.read("netzero-report.json"))
    assert report["run_id"] == run_id
    assert len(report["functions"]) == 8


def test_speed_must_be_positive() -> None:
    with pytest.raises(ValueError):
        FakePipeline(speed=0)
    with pytest.raises(ValueError):
        FakePipeline(speed=-1)
    with pytest.raises(ValueError):
        FakePipeline(scenario="nope")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The catalog is a real repository
# ---------------------------------------------------------------------------


def _write_repo(root: Path, texts: dict[str, str]) -> None:
    for rel, text in texts.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        (path.parent / "__init__.py").touch()
        path.write_text(text, encoding="utf-8")


def test_catalog_matches_discovery(tmp_path: Path) -> None:
    _write_repo(tmp_path, {fn.file: fn.module_text for fn in CATALOG.values()})
    for fid, fn in CATALOG.items():
        found, err = parse_module(tmp_path, tmp_path / fn.file)
        assert err is None, err
        [d] = [d for d in found if d.function_id == fid]
        info = fn.info()
        assert (d.line, d.end_line, d.import_line, d.call_hint, d.kind, d.source) == (
            info.line,
            info.end_line,
            info.import_line,
            info.call_hint,
            info.kind,
            info.source,
        ), fid
        assert fn.loc == sum(
            1 for ln in d.source.splitlines() if ln.strip() and not ln.strip().startswith("#")
        )


def _load(fn: FakeFunction, text: str) -> None:
    """Install ``text`` as module ``fn.module`` (and its parent packages)."""
    parts = fn.module.split(".")
    for i in range(1, len(parts)):
        sys.modules.setdefault(".".join(parts[:i]), types.ModuleType(".".join(parts[:i])))
    mod = types.ModuleType(fn.module)
    exec(compile(text, fn.file, "exec"), mod.__dict__)
    sys.modules[fn.module] = mod


def _failing_tests(fn: FakeFunction, module_text: str, test_code: str) -> set[str]:
    _load(fn, module_text)
    ns: dict = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        exec(compile(test_code, fn.test_path, "exec"), ns)
    failing = set()
    for name, obj in ns.items():
        if name.startswith("test_") and callable(obj):
            try:
                obj()
            except Exception:
                failing.add(name)
    return failing


@pytest.fixture
def isolated_modules():
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        del sys.modules[name]


@pytest.mark.usefixtures("isolated_modules")
@pytest.mark.parametrize("fid", [f for f, fn in CATALOG.items() if fn.drafts])
def test_generated_tests_tell_the_truth(fid: str) -> None:
    """Each draft fails exactly as scripted on the original; each rewrite as planned."""
    fn = CATALOG[fid]
    final = 0
    for attempt, draft in enumerate(fn.drafts):
        code = fn.test_code(attempt)
        tf = fn.test_file(attempt)
        assert code.count("@pytest.mark.nz_workload") == 1
        assert fn.import_line in code
        assert len(tf.test_names) >= 3 and tf.workload_test
        want = {draft.failing} if draft.failing else set()
        assert _failing_tests(fn, fn.module_text, code) == want, (fid, attempt)
        if not draft.failing:
            final = attempt
            break
    if fn.fate == "skipped_untestable":
        return
    code = fn.test_code(final)
    for cid, plan in fn.plans.items():
        p = plan
        while p is not None:
            if p.rewrite is not None:
                text = fn.candidate_text(p.rewrite)
                if p.reject == "syntax":
                    with pytest.raises(SyntaxError):
                        compile(text, fn.file, "exec")
                elif p.reject == "timeout":
                    pass  # it loops forever: that is the point
                else:
                    want = {p.failing} if p.reject == "tests_failed" else set()
                    assert _failing_tests(fn, text, code) == want, (fid, cid, p.rewrite)
                    f = fn.file
                    head = f"diff --git a/{f} b/{f}\n--- a/{f}\n+++ b/{f}\n"
                    assert fn.diff(p.rewrite).startswith(head)
            p = p.repair
