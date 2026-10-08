"""``netzero`` command line."""

from __future__ import annotations

import json
import os
from pathlib import Path

import typer
from rich.console import Console

from netzero import paths

app = typer.Typer(
    name="netzero",
    help="Greener Python, function by function: tests, CO2 benchmarks, Claude rewrites.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
err = Console(stderr=True)


@app.command()
def serve(
    host: str | None = typer.Option(None, help="Bind address (default 127.0.0.1)."),
    port: int | None = typer.Option(None, help="Port (default 8000)."),
    reload: bool = typer.Option(False, "--reload", help="Restart on changes under netzero/."),
) -> None:
    """Serve the API and the web UI on one port."""
    import uvicorn

    from netzero.config import get_settings

    settings = get_settings()
    host = host or settings.host
    port = port or settings.port
    if host not in ("127.0.0.1", "localhost", "::1"):
        err.print(
            f"[yellow]warning:[/] binding {host}. net-zero has no auth and runs repository "
            "code on this machine; keep it on localhost."
        )
    console.print(f"[bold #00ff88]net-zero[/] on http://{host}:{port}")
    uvicorn.run(
        "netzero.api.app:app_factory",
        factory=True,
        host=host,
        port=port,
        reload=reload,
        reload_dirs=[str(paths.PACKAGE_DIR)] if reload else None,
        timeout_graceful_shutdown=2,
        log_level="info",
    )


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(paths.ROOT))
    except ValueError:
        return str(path)


@app.command("export-schema")
def export_schema(
    out: Path = typer.Option(paths.SCHEMA_OUT, help="Where to write the JSON schema."),
    check: bool = typer.Option(False, "--check", help="Fail if the file is out of date."),
) -> None:
    """Write the event/API contract as JSON schema (input to ``make types``)."""
    from netzero.api.schemas import contract_schema

    text = json.dumps(contract_schema(), indent=2) + "\n"
    if check:
        current = out.read_text("utf-8") if out.exists() else ""
        if current != text:
            err.print(f"[red]{rel(out)} is out of date[/]; run `uv run netzero export-schema`")
            raise typer.Exit(1)
        console.print(f"{rel(out)} is up to date")
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, "utf-8")
    console.print(f"wrote {rel(out)} ({len(text) // 1024} KB)")


@app.command()
def replay(
    path: Path = typer.Argument(..., exists=True, dir_okay=False, help="An events.jsonl file."),
    speed: float = typer.Option(
        4.0, help="Playback speed (gaps are capped at 1.5 s); 0 = instant."
    ),
    check: bool = typer.Option(
        False, "--check", help="Only validate the file against the event grammar."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show state changes and all logs."),
) -> None:
    """Play back a recorded run in the terminal."""
    import time

    from netzero.events import parse_event
    from netzero.pipeline.grammar import check_grammar
    from netzero.pipeline.store import read_complete_lines
    from netzero.render import EventRenderer

    lines, _ = read_complete_lines(path)
    if check:
        problems = check_grammar(lines)
        for problem in problems[:20]:
            err.print(f"[red]✗[/] {problem}")
        if problems:
            raise typer.Exit(1)
        console.print(f"{rel(path)}: {len(lines)} events, grammar ok")
        return
    render = EventRenderer(console, verbose=verbose)
    prev_ts: int | None = None
    try:
        for line in lines:
            ev = parse_event(line)
            if speed > 0 and prev_ts is not None:
                time.sleep(min(max(0, ev.ts - prev_ts) / 1000, REPLAY_GAP_CAP_S) / speed)
            prev_ts = ev.ts
            render(ev)
    except KeyboardInterrupt:
        raise typer.Exit(130) from None


REPLAY_GAP_CAP_S = 1.5
EXIT_CODES = {"completed": 0, "failed": 1, "cancelled": 130, "interrupted": 130}


@app.command()
def fake(
    out: Path | None = typer.Option(
        None, "--out", "-o", dir_okay=False, help="Write the events.jsonl here."
    ),
    scenario: str = typer.Option("demo", help="demo | short | fail_env"),
    seed: int = typer.Option(0, help="Seeds the scripted measurements."),
    speed: float = typer.Option(
        1.0, help="Compress the run's timeline (ts and durations); 1 = as scripted."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show state changes and all logs."),
) -> None:
    """Generate a scripted run (no git, uv or LLM) in virtual time and check its grammar.

    The run is the FakePipeline the web UI develops against: every outcome the
    UI has to show, with realistic timestamps, produced in about a second.
    """
    import asyncio
    import tempfile

    from netzero.events import parse_event
    from netzero.pipeline.fake import SCENARIOS
    from netzero.pipeline.grammar import check_grammar
    from netzero.pipeline.vclock import VirtualTimeLoop
    from netzero.render import EventRenderer

    if scenario not in SCENARIOS:
        err.print(f"unknown scenario {scenario!r}; expected one of {', '.join(SCENARIOS)}")
        raise typer.Exit(2)
    if not speed > 0:
        err.print("--speed must be > 0")
        raise typer.Exit(2)
    with tempfile.TemporaryDirectory(prefix="netzero-fake-") as tmp:
        with asyncio.Runner(loop_factory=VirtualTimeLoop) as runner:
            lines = runner.run(_fake_run(Path(tmp), scenario, seed, speed))
    problems = check_grammar(lines)
    for problem in problems[:20]:
        err.print(f"[red]✗[/] {problem}")
    if problems:
        raise typer.Exit(1)
    last = parse_event(lines[-1])
    if out is None:
        render = EventRenderer(console, verbose=verbose)
        for line in lines:
            render(parse_event(line))
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("".join(line + "\n" for line in lines), "utf-8")
        minutes, seconds = divmod((last.ts - parse_event(lines[0]).ts) // 1000, 60)
        console.print(
            f"wrote {rel(out)}: {len(lines)} events, {last.type}, "
            f"{minutes}m{seconds:02d}s of run time, grammar ok"
        )


async def _fake_run(runs_dir: Path, scenario: str, seed: int, speed: float) -> list[str]:
    import asyncio
    import time

    from netzero.api.schemas import CreateRunRequest
    from netzero.config import Settings
    from netzero.pipeline.fake import FakePipeline
    from netzero.pipeline.orchestrator import RunManager
    from netzero.pipeline.store import read_complete_lines

    loop = asyncio.get_running_loop()
    epoch_ms = int(time.time() * 1000)
    settings = Settings(_env_file=None, runs_dir=runs_dir, fake_pipeline=True)  # type: ignore[call-arg]
    manager = RunManager(
        settings,
        pipeline=FakePipeline(speed, scenario=scenario, seed=seed),  # type: ignore[arg-type]
        power=lambda: None,  # the fake detects its own power source
        clock=lambda: epoch_ms + int(loop.time() * 1000),
    )
    manager.start()
    try:
        summary = await manager.create(CreateRunRequest(demo=True, auto_select=True))
        await manager.wait(summary.id)
    finally:
        await manager.shutdown(timeout=2)
    lines, _ = read_complete_lines(manager.store.paths(summary.id).events)
    return lines


@app.command("run")
def run_cmd(
    url: str | None = typer.Argument(None, help="https://github.com/<owner>/<repo>"),
    demo: bool = typer.Option(False, "--demo", help="Optimize the bundled demo repo."),
    ref: str | None = typer.Option(None, help="Branch or tag to clone."),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Optimize the preselected functions without asking."
    ),
    select: list[str] | None = typer.Option(
        None, "--select", help="A function id to optimize (repeatable)."
    ),
    json_lines: bool = typer.Option(False, "--json", help="Print raw events as JSON lines."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show state changes and all logs."),
) -> None:
    """Optimize a repo from the terminal. The web UI can follow the same run."""
    import asyncio

    from netzero.api.schemas import CreateRunRequest

    if (url is None) == (not demo):
        err.print("give a GitHub URL or --demo (not both)")
        raise typer.Exit(2)
    req = CreateRunRequest(github_url=url, demo=demo, ref=ref, auto_select=yes)
    detail = asyncio.run(
        _run_in_terminal(req, select or [], json_lines=json_lines, verbose=verbose)
    )
    raise typer.Exit(EXIT_CODES.get(detail.state if detail else "failed", 1))


async def _run_in_terminal(
    req, select: list[str], *, json_lines: bool, verbose: bool, mode: str | None = None
):
    """Run in this process with a live terminal view; returns the final ``RunDetail``."""
    import asyncio
    import signal
    import sys

    from netzero.config import get_settings
    from netzero.events import TERMINAL_EVENT_TYPES, line_type, parse_event
    from netzero.pipeline.follow import follow
    from netzero.pipeline.orchestrator import RunManager, RunRejected
    from netzero.render import EventRenderer

    manager = RunManager(get_settings())
    manager.start()
    try:
        summary = await manager.create(req, mode=mode)  # type: ignore[arg-type]
    except RunRejected as exc:
        err.print(f"[red]{exc.code}[/]: {exc.detail}")
        if exc.active_run:
            err.print(f"active run: {exc.active_run.id} ({exc.active_run.state})")
        return None
    run_id = summary.id
    ctx = manager.live(run_id)
    assert ctx is not None
    render = EventRenderer(console, verbose=verbose)
    loop = asyncio.get_running_loop()
    main = asyncio.current_task()
    presses = 0

    def on_sigint() -> None:
        nonlocal presses
        presses += 1
        if presses == 1:
            err.print("[yellow]cancelling… (ctrl+c again to stop now)[/]")
            loop.create_task(manager.cancel(run_id))
        elif main is not None:
            main.cancel()

    loop.add_signal_handler(signal.SIGINT, on_sigint)
    last, ended = 0, False
    try:
        while not ended:
            async for item in follow(ctx, last, idle_s=3600):
                if item is None:
                    continue
                last, line = item
                if json_lines:
                    print(line, flush=True)
                else:
                    render(parse_event(line))
                if '"to_state":"awaiting_selection"' in line and not req.auto_select:
                    interactive = not json_lines and sys.stdin.isatty()
                    await _choose(manager, run_id, select, interactive=interactive)
                ended = line_type(line) in TERMINAL_EVENT_TYPES
            # follow() also returns early when this subscriber overflowed:
            # resume from `last` while the run is still going
            ended = ended or manager.live(run_id) is None
    except asyncio.CancelledError:
        pass
    finally:
        loop.remove_signal_handler(signal.SIGINT)
        await manager.shutdown(timeout=3)
    detail = manager.detail(run_id)
    if detail is not None and not json_lines:
        err.print(f"[grey50]run {run_id} · {detail.state} · runs/{run_id}/[/]")
    return detail


async def _choose(manager, run_id: str, select: list[str], *, interactive: bool) -> None:
    """Answer ``awaiting_selection``: ``--select`` ids, a prompt, or the preselection."""
    import asyncio

    from netzero.pipeline.orchestrator import RunRejected

    ctx = manager.live(run_id)
    if ctx is None:
        return
    pre = [t.function_id for t in ctx.detail.triage if t.preselected]
    ids = select or pre
    if not select and interactive:
        # race the prompt against the run ending (ctrl+c cancels it meanwhile)
        ask = asyncio.ensure_future(_ask(f"optimize the {len(pre)} preselected functions? [Y/n] "))
        ended = asyncio.ensure_future(manager.wait(run_id))
        await asyncio.wait({ask, ended}, return_when=asyncio.FIRST_COMPLETED)
        ended.cancel()
        if not ask.done():
            ask.cancel()
            console.print()
            return
        if ask.result().strip().lower() in ("n", "no"):
            err.print("nothing selected; cancelling (use --select to choose functions)")
            await manager.cancel(run_id)
            return
    if not ids:
        err.print("no function was preselected; cancelling (use --select)")
        await manager.cancel(run_id)
        return
    try:
        manager.select(run_id, ids)
    except RunRejected as exc:
        err.print(f"[red]{exc.code}[/]: {exc.detail}")
        await manager.cancel(run_id)


# every outcome the demo can show: wins, repairs, a repaired test file, a
# syntax error, a static reject, all rejected and no significant win
REPLAY_SELECTION = (
    "algos.graph:Graph.shortest_path_lengths",
    "algos.pairs:has_pair_with_sum",
    "algos.primes:primes_below",
    "algos.strings:levenshtein",
    "textkit.freq:word_frequencies",
    "datakit.aggregate:total_by_category",
    "datakit.rank:top_k_inplace",
    "textkit.fields:join_fields",
)
REPLAY_DESCRIPTION = (
    "A real run on the bundled demo repository: tests, input capture, differential "
    "checks and CO2 benchmarks ran on a laptop. The model replies are hand-written "
    "demo cassettes, not recorded from Claude."
)


@app.command()
def record(
    cassettes: bool = typer.Option(
        False,
        "--cassettes",
        help="Call Claude and re-record the demo cassettes (needs ANTHROPIC_API_KEY).",
    ),
    select: list[str] | None = typer.Option(
        None, "--select", help="A function id to optimize (repeatable); default: a mix."
    ),
    out: Path = typer.Option(
        paths.WEB_PUBLIC_REPLAYS / "demo.events.jsonl", "--out", "-o", dir_okay=False
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show state changes and all logs."),
) -> None:
    """Run the demo and save it as the web UI's bundled replay."""
    import asyncio
    import shutil

    from netzero.api.schemas import CreateRunRequest
    from netzero.events import parse_event
    from netzero.pipeline.grammar import check_grammar
    from netzero.pipeline.store import read_complete_lines

    req = CreateRunRequest(demo=True)
    mode = "record" if cassettes else "demo"
    ids = select or list(REPLAY_SELECTION)
    detail = asyncio.run(_run_in_terminal(req, ids, json_lines=False, verbose=verbose, mode=mode))
    if detail is None or detail.state != "completed":
        err.print("the run did not complete; the replay was not written")
        raise typer.Exit(1)
    from netzero.config import get_settings

    events = get_settings().runs_dir / detail.id / "events.jsonl"
    lines, _ = read_complete_lines(events)
    if problems := check_grammar(lines):
        err.print(f"[red]✗[/] {problems[0]}")
        raise typer.Exit(1)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(events, out)
    first, last = parse_event(lines[0]), parse_event(lines[-1])
    n_functions = sum(1 for line in lines if '"type":"function.started"' in line)
    _add_replay(
        out,
        {
            "id": out.name.removesuffix(".events.jsonl"),
            "title": "Demo repository",
            "description": REPLAY_DESCRIPTION,
            "src": f"/replays/{out.name}",
            "functions": n_functions,
            "duration_ms": last.ts - first.ts,
        },
    )
    console.print(f"wrote {rel(out)}: {len(lines)} events, {n_functions} functions")


def _add_replay(out: Path, entry: dict) -> None:
    """Add or replace ``entry`` in the ``index.json`` next to ``out``, first in the list."""
    from netzero.api.schemas import ReplayRef

    index = out.parent / "index.json"
    try:
        current = json.loads(index.read_text("utf-8"))
    except (OSError, ValueError):
        current = []
    entry = ReplayRef.model_validate(entry).model_dump(mode="json")
    rest = [r for r in current if isinstance(r, dict) and r.get("id") != entry["id"]]
    index.write_text(json.dumps([entry, *rest], indent=2, ensure_ascii=False) + "\n", "utf-8")


@app.command("demo-cassettes")
def demo_cassettes() -> None:
    """Rebuild the demo cassettes from the hand-written stories in examples/demo-stories."""
    from netzero.llm import stories

    try:
        written = stories.build()
    except stories.StoryError as exc:
        err.print(f"[red]✗[/] {exc}")
        raise typer.Exit(1) from None
    console.print(f"wrote {len(written)} cassettes to {rel(paths.CASSETTES_DIR)}")


@app.command()
def calibrate(
    force: bool = typer.Option(False, "--force", help="Measure again even if calibrated."),
) -> None:
    """Measure this CPU's per-core power with powermetrics (macOS, passwordless sudo)."""
    import asyncio

    from netzero.bench.power import run_calibration
    from netzero.config import get_settings

    console.print("calibrating: idle, then one busy thread (about 10 s if powermetrics works)…")
    result = asyncio.run(run_calibration(get_settings(), force=force))
    p = result.power
    if result.p_core_w is None:
        for note in p.notes[-1:]:
            err.print(f"[yellow]{note}[/]")
        err.print(f"power stays {p.badge}: P_core {p.p_core_w:.2f} W ({p.power_source})")
        err.print(SUDOERS_HINT, soft_wrap=True)
        raise typer.Exit(1)
    if result.cached:
        console.print(f"already calibrated: P_core {p.p_core_w:.2f} W (use --force to redo)")
        return
    console.print(
        f"[#00ff88]calibrated[/]: {result.p_idle_w:.2f} W idle, {result.p_busy_w:.2f} W busy "
        f"→ P_core {p.p_core_w:.2f} W; saved to {rel(paths.POWER_PROFILE)}"
    )


SUDOERS_HINT = (
    "optional, macOS: let netzero read powermetrics without a password:\n"
    '  echo "$USER ALL=(root) NOPASSWD: /usr/bin/powermetrics" '
    "| sudo tee /etc/sudoers.d/netzero && sudo chmod 440 /etc/sudoers.d/netzero"
)
RAPL_ENERGY = Path("/sys/class/powercap/intel-rapl:0/energy_uj")


@app.command()
def doctor(
    refresh: bool = typer.Option(False, "--refresh", help="Re-probe the power source."),
) -> None:
    """Check git, uv, Python 3.12, node, the API key and how CO2 will be measured."""
    import asyncio
    import shutil
    import subprocess
    import sys

    from netzero.bench.probe import powermetrics_ok, probe
    from netzero.config import get_settings

    settings = get_settings()
    failed = False

    def line(ok: bool | None, name: str, detail: str, *, required: bool = False) -> None:
        nonlocal failed
        mark = {True: "[#00ff88]✓[/]", False: "[#ff5f57]✗[/]", None: "[#ffbd2e]![/]"}[ok]
        console.print(f"{mark} {name:<12} [grey62]{detail}[/]", soft_wrap=True)
        failed = failed or (required and not ok)

    def version(*argv: str) -> str | None:
        if shutil.which(argv[0]) is None:
            return None
        try:
            out = subprocess.run(
                argv, capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return (out.stdout or out.stderr).strip().splitlines()[0] if out.returncode == 0 else None

    git = version("git", "--version")
    line(git is not None, "git", git or "not found: install git", required=True)
    uv = version("uv", "--version")
    line(uv is not None, "uv", uv or "not found: brew install uv", required=True)
    found = uv and version("uv", "python", "find", "3.12")
    py = found and version(found, "--version")
    line(bool(py), "python", py or "3.12 missing: uv python install 3.12", required=True)
    node = version("node", "--version")
    major = int(node.lstrip("v").split(".")[0]) if node else 0
    line(major >= 20 or None, "node", node or "not found (only needed to build the web ui)")
    built = (paths.WEB_DIST / "index.html").is_file()
    line(built or None, "web ui", "built" if built else "not built: make web")
    key = settings.has_api_key
    line(key or None, "api key", "set" if key else "not set: demo and replay modes only")
    cassettes = paths.CASSETTES_DIR.is_dir()
    line(cassettes or None, "demo", "cassettes found" if cassettes else "no cassettes")
    if settings.database_url:
        ok, detail = _db_check(settings.database_url)
        line(ok, "postgres", detail)
    else:
        line(None, "postgres", "off: set NETZERO_DATABASE_URL to keep run history (make db)")

    power = asyncio.run(probe(settings, refresh=refresh))
    grid = power.grid
    line(
        power.badge != "estimated" or None,
        "power",
        f"{power.badge} via {power.power_source}: P_core {power.p_core_w:.2f} W, "
        f"RAM {power.p_ram_w:.2f} W, grid {grid.country_iso} {grid.kg_per_kwh * 1000:.0f} g/kWh",
    )
    if sys.platform.startswith("linux") and power.power_source != "rapl":
        readable = os.access(RAPL_ENERGY, os.R_OK)
        line(
            readable or None,
            "rapl",
            "readable" if readable else f"{RAPL_ENERGY} not readable: sudo chmod a+r it",
        )
    if sys.platform == "darwin" and power.badge != "calibrated":
        if powermetrics_ok():
            line(None, "powermetrics", "works: run `uv run netzero calibrate`")
        else:
            line(None, "powermetrics", "needs a password; energy is estimated from CPU time")
            console.print(f"[grey62]{SUDOERS_HINT}[/]", soft_wrap=True)
    if failed:
        raise typer.Exit(1)


def _db_check(url: str) -> tuple[bool, str]:
    import psycopg

    from netzero.history import db

    try:
        with db.connect(url, timeout_s=3) as conn:
            version = conn.execute("show server_version").fetchone()[0]  # type: ignore[index]
            applied = db.applied_migrations(conn)
    except psycopg.Error as exc:
        return False, f"{db.describe(url)}: {str(exc).strip().splitlines()[0]}"
    pending = len(db.migrations()) - len(applied)
    tables = f"{pending} migration(s) pending" if pending else "migrated"
    return True, f"{db.describe(url)}, Postgres {version}, {tables}"


# -- netzero db --------------------------------------------------------------------------

db_app = typer.Typer(
    name="db",
    help="The Postgres run history (NETZERO_DATABASE_URL): status, sync, import, rebuild.",
    no_args_is_help=True,
)
app.add_typer(db_app)


def _db_conn():
    import psycopg

    from netzero.config import get_settings
    from netzero.history import db

    url = get_settings().database_url
    if not url:
        err.print("NETZERO_DATABASE_URL is not set; `make db` starts a local Postgres")
        raise typer.Exit(2)
    try:
        conn = db.connect(url)
        applied = db.migrate(conn)
    except psycopg.Error as exc:
        err.print(f"[red]✗[/] {db.describe(url)}: {str(exc).strip().splitlines()[0]}")
        raise typer.Exit(1) from None
    if applied:
        console.print(f"applied migrations {', '.join(applied)}")
    return conn


@db_app.command("status")
def db_status() -> None:
    """What the database holds, table by table."""
    from rich.table import Table

    from netzero.config import get_settings
    from netzero.history import db, queries

    with _db_conn() as conn:
        tables = queries.tables(conn)
        head, projected = queries.positions(conn)
        migrations = db.applied_migrations(conn)
    url = get_settings().database_url or ""
    console.print(f"[bold]{db.describe(url)}[/]  migrations {', '.join(migrations)}")
    grid = Table(box=None, pad_edge=False)
    grid.add_column("table")
    grid.add_column("rows", justify="right")
    grid.add_column("size", justify="right")
    for t in tables:
        grid.add_row(t.name, f"{t.rows:,}", f"{t.bytes / 1024:,.0f} kB")
    console.print(grid)
    behind = head - projected
    console.print(
        f"events through position {head:,}; read models through {projected:,}"
        + (f" [#ffbd2e]({behind:,} to fold: netzero db sync)[/]" if behind else "")
    )


@db_app.command("sync")
def db_sync() -> None:
    """Store every run under runs/ that isn't stored yet, and fold it into the read models.

    A running server does this every couple of seconds; this is for CLI-only use.
    """
    from netzero.config import get_settings
    from netzero.history import projector
    from netzero.history.ingest import Shipper
    from netzero.pipeline.store import RunStore

    with _db_conn() as conn:
        shipped = Shipper(RunStore(get_settings().runs_dir)).ship(conn)
        projected = projector.project_pending(conn)
    for problem in shipped.problems:
        err.print(f"[#ffbd2e]![/] {problem}")
    console.print(
        f"stored {shipped.events:,} events from {len(shipped.runs)} run(s); "
        f"folded {projected.runs} run(s)"
        + (f", skipped {projected.skipped}" if projected.skipped else "")
    )


@db_app.command("import")
def db_import(
    files: list[Path] = typer.Argument(
        ..., exists=True, dir_okay=False, help="events.jsonl files (runs, replays)."
    ),
) -> None:
    """Store event logs from anywhere, e.g. the bundled replays or another machine's runs."""
    import psycopg

    from netzero.history import projector
    from netzero.history.ingest import import_file

    failed = False
    with _db_conn() as conn:
        for path in files:
            try:
                run_id, n = import_file(conn, path)
            except (ValueError, psycopg.Error) as exc:
                failed = True
                err.print(f"[red]✗[/] {rel(path)}: {str(exc).splitlines()[0]}")
                continue
            console.print(f"{rel(path)}: run {run_id}, {n} new event(s)")
        projected = projector.project_pending(conn)
    console.print(f"folded {projected.runs} run(s)")
    if failed:
        raise typer.Exit(1)


@db_app.command("rebuild")
def db_rebuild() -> None:
    """Throw away the read models and fold every stored event again. The events stay."""
    from netzero.history import projector

    with _db_conn() as conn:
        result = projector.rebuild_all(conn)
    console.print(
        f"folded {result.runs} run(s) through position {result.position:,}"
        + (f", skipped {result.skipped}" if result.skipped else "")
    )


async def _ask(prompt: str) -> str:
    """Read one line from a TTY without blocking the event loop (or a thread)."""
    import asyncio
    import sys

    loop = asyncio.get_running_loop()
    done: asyncio.Future[str] = loop.create_future()
    console.print(prompt, end="")

    def on_ready() -> None:
        if not done.done():
            done.set_result(sys.stdin.readline())

    loop.add_reader(sys.stdin.fileno(), on_ready)
    try:
        return await done
    finally:
        loop.remove_reader(sys.stdin.fileno())


if __name__ == "__main__":
    app()
