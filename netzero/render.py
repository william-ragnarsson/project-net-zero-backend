"""Terminal rendering of run events (``netzero run`` and ``netzero replay``).

One line per meaningful event, indented by scope: run steps at the left,
function steps under ``▸ function``, candidate steps prefixed with their
letter. Colours follow the brand: neon for active/accepted, caution for
repairs, fail for rejections, muted for no-win and skips.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.table import Table
from rich.text import Text

from netzero.events import EventBase, PowerInfo, RunTotals

NEON = "#00ff88"
CAUTION = "#ffbd2e"
FAIL = "#ff5f57"
MUTED = "grey50"

OUTCOME_STYLE = {
    "accepted": NEON,
    "no_significant_win": MUTED,
    "all_rejected": FAIL,
    "reverted": FAIL,
    "skipped_untestable": MUTED,
    "skipped_capture": MUTED,
    "failed": FAIL,
    "cancelled": CAUTION,
}


def minus(x: float, fmt: str = "{:.1f}") -> str:
    """Signed number with a real minus sign (U+2212)."""
    s = fmt.format(abs(x))
    return f"−{s}" if x < 0 else f"+{s}" if x > 0 else s


def fmt_g(g: float) -> str:
    """Grams of CO2e with a readable unit."""
    a = abs(g)
    for unit, scale in (("kg", 1e3), ("g", 1.0), ("mg", 1e-3), ("µg", 1e-6), ("ng", 1e-9)):
        if a >= scale or unit == "ng":
            return f"{g / scale:.3g} {unit}"
    return f"{g:.3g} g"  # pragma: no cover


def fmt_ms(ms: int | float) -> str:
    return f"{ms / 1000:.1f} s" if ms >= 1000 else f"{ms:.0f} ms"


def power_badge(p: PowerInfo) -> Text:
    style = NEON if p.badge == "measured" else CAUTION if p.badge == "calibrated" else MUTED
    return Text.assemble(
        (f"[{p.badge}]", style),
        f" {p.power_source} · {p.p_core_w:.1f} W/core · grid {p.grid.country_iso} "
        f"{p.grid.kg_per_kwh * 1000:.0f} g/kWh",
    )


class EventRenderer:
    def __init__(self, console: Console, *, verbose: bool = False):
        self.console = console
        self.verbose = verbose
        self._test_repeat = 0

    def __call__(self, ev: EventBase) -> None:
        handler = getattr(self, "_" + ev.type.replace(".", "_"), None)  # type: ignore[attr-defined]
        if handler is not None:
            handler(ev, ev.data)  # type: ignore[attr-defined]

    # -- output helpers -----------------------------------------------------------

    def _line(self, indent: int, *parts: Any) -> None:
        self.console.print(Text.assemble("  " * indent, *parts), highlight=False, soft_wrap=True)

    def _fn(self, ev: EventBase, *parts: Any) -> None:
        self._line(2, *parts)

    def _cand(self, ev: EventBase, *parts: Any) -> None:
        self._line(2, (f"{ev.candidate_id} ", "bold"), *parts)

    @staticmethod
    def _err(d: Any) -> str:
        return f": {d.error.message}" if getattr(d, "error", None) else ""

    @staticmethod
    def _repair(ev: EventBase) -> str:
        return f" (repair {ev.attempt})" if ev.attempt else ""

    # -- run ------------------------------------------------------------------------

    def _run_created(self, ev, d) -> None:
        src = d.source.url or "bundled demo repo"
        self._line(
            0, ("net-zero ", f"bold {NEON}"), f"run {ev.run_id}", (f"  {d.mode} · {src}", MUTED)
        )

    def _run_state_changed(self, ev, d) -> None:
        if self.verbose:
            self._line(0, (f"state {d.from_state} → {d.to_state}", MUTED))

    def _run_power_detected(self, ev, d) -> None:
        self._line(0, "power ", power_badge(d.power))

    def _run_power_calibration_completed(self, ev, d) -> None:
        if d.ok and d.p_core_w is not None:
            self._line(0, ("✓ ", NEON), f"calibrated {d.p_core_w:.1f} W/core")

    def _run_clone_started(self, ev, d) -> None:
        self._line(0, ("… ", MUTED), f"cloning {d.url or 'demo repo'}")

    def _run_clone_completed(self, ev, d) -> None:
        if d.ok:
            sha = (d.commit_sha or "")[:7]
            self._line(
                0,
                ("✓ ", NEON),
                f"cloned {sha} · {d.n_py_files} .py files",
                (f"  {fmt_ms(d.duration_ms)}", MUTED),
            )
        else:
            self._line(0, ("✗ ", FAIL), f"clone failed{self._err(d)}")

    def _run_env_started(self, ev, d) -> None:
        self._line(0, ("… ", MUTED), f"installing python {d.python_request}")

    def _run_env_completed(self, ev, d) -> None:
        if d.ok:
            self._line(
                0,
                ("✓ ", NEON),
                f"python {d.python_version} · {len(d.packages)} packages",
                (f"  {fmt_ms(d.duration_ms)}", MUTED),
            )
        else:
            self._line(0, ("✗ ", FAIL), f"environment failed{self._err(d)}")

    def _run_discovery_completed(self, ev, d) -> None:
        if d.ok:
            self._line(
                0, ("✓ ", NEON), f"discovered {d.n_functions} functions in {d.n_files} files"
            )
        else:
            self._line(0, ("✗ ", FAIL), f"discovery failed{self._err(d)}")

    def _run_triage_completed(self, ev, d) -> None:
        if not d.ok:
            self._line(0, ("✗ ", FAIL), f"triage failed{self._err(d)}")
            return
        how = "heuristics + llm" if d.llm_used else "heuristics only"
        self._line(0, ("✓ ", NEON), f"ranked {len(d.items)} functions ({how})")
        table = Table(box=None, show_header=False, padding=(0, 1), pad_edge=False)
        for item in d.items[: 20 if self.verbose else 12]:
            mark = Text("●", NEON) if item.preselected else Text("○", MUTED)
            name = Text(item.function_id, MUTED if item.skip_reason else "")
            note = (
                Text(f"skip: {item.skip_reason}", MUTED)
                if item.skip_reason
                else Text(f"{item.score:.2f}")
            )
            table.add_row("   ", mark, name, note)
        self.console.print(table)

    def _run_selection_confirmed(self, ev, d) -> None:
        how = "auto" if d.auto else "selected"
        self._line(0, ("✓ ", NEON), f"{how}: {len(d.function_ids)} functions to optimize")

    def _run_artifacts_completed(self, ev, d) -> None:
        if d.ok and d.patch:
            self._line(
                0, ("✓ ", NEON), f"patch: {d.patch.files_changed} files changed · {d.patch.url}"
            )

    def _run_completed(self, ev, d) -> None:
        self.console.rule(style=MUTED)
        self._line(
            0, ("✓ run completed", f"bold {NEON}"), (f"  {fmt_ms(d.summary.duration_ms)}", MUTED)
        )
        self.totals(d.summary)

    def _run_failed(self, ev, d) -> None:
        self._line(0, ("✗ run failed", f"bold {FAIL}"), f" during {d.stage}: {d.error.message}")

    def _run_cancelled(self, ev, d) -> None:
        self._line(0, ("⊘ run cancelled", f"bold {CAUTION}"), f" during {d.at_state}")

    def _run_interrupted(self, ev, d) -> None:
        self._line(0, ("⊘ run interrupted", f"bold {CAUTION}"), f" during {d.previous_state}")

    def totals(self, t: RunTotals) -> None:
        done = f"{t.functions_done}/{t.functions_total} functions"
        parts = [f"  {done}"]
        if t.mean_reduction_pct is not None:
            parts.append(f"mean {minus(t.mean_reduction_pct)}% CO₂ per call")
        if t.g_saved_per_1m_calls:
            parts.append(f"{fmt_g(t.g_saved_per_1m_calls)} saved per 1M calls")
        parts.append(f"llm ${t.llm_cost_usd:.3f}")
        self._line(0, " · ".join(parts))
        counts = [
            Text(f"{k} {v}", OUTCOME_STYLE.get(k, "")) for k, v in t.counts_by_outcome.items() if v
        ]
        if counts:
            self._line(1, Text("  ").join(counts))

    # -- function ---------------------------------------------------------------------

    def _function_started(self, ev, d) -> None:
        self.console.print()
        self._line(
            1,
            ("▸ ", NEON),
            (f"{d.index + 1}/{d.total} ", MUTED),
            (d.info.function_id, "bold"),
            (f"  {d.info.file}:{d.info.line}", MUTED),
        )

    def _function_tests_write_completed(self, ev, d) -> None:
        if d.ok and d.test_file:
            verb = "repaired" if ev.attempt else "wrote"
            self._fn(
                ev,
                ("✎ ", NEON if not ev.attempt else CAUTION),
                f"{verb} {len(d.test_file.test_names)} tests",
            )
        else:
            self._fn(ev, ("✗ ", FAIL), f"writing tests failed{self._err(d)}")

    def _function_tests_run_started(self, ev, d) -> None:
        self._test_repeat = d.repeat

    def _function_tests_run_completed(self, ev, d) -> None:
        r = d.result
        if d.ok and r:
            if self._test_repeat == 0:
                self._fn(
                    ev,
                    ("✓ ", NEON),
                    f"{r.passed} tests pass on the original",
                    (f"  {r.duration_s:.1f} s", MUTED),
                )
            elif self.verbose:
                self._fn(ev, ("✓ ", NEON), ("stable on re-run", MUTED))
        elif r:
            why = "flaky" if d.flaky else f"{r.failed + r.errors} failing"
            self._fn(ev, ("↺ ", CAUTION), f"tests {why}{self._repair(ev)}")
        else:
            self._fn(ev, ("✗ ", FAIL), f"tests did not run{self._err(d)}")

    def _function_capture_completed(self, ev, d) -> None:
        if d.ok:
            extra = " · mutates args" if d.mutates_args else ""
            self._fn(ev, ("✓ ", NEON), f"captured {d.n_kept} inputs{extra}")
        else:
            self._fn(ev, ("✗ ", FAIL), f"capture failed{self._err(d)}")

    def _function_baseline_completed(self, ev, d) -> None:
        if d.ok and d.original:
            o = d.original
            self._fn(
                ev,
                ("◆ ", NEON),
                f"baseline {fmt_g(o.g_per_call.mean)}/call",
                (f"  {o.n_trials}×{o.calls_per_trial} calls · cv {d.cv_pct:.0f}%", MUTED),
            )
        else:
            self._fn(ev, ("✗ ", FAIL), f"baseline failed{self._err(d)}")

    def _function_decision(self, ev, d) -> None:
        if d.outcome == "winner":
            self._fn(ev, ("★ ", NEON), f"winner {d.winner}")
        elif d.outcome == "no_significant_win":
            self._fn(ev, ("· ", MUTED), ("no significant win", MUTED))
        else:
            self._fn(ev, ("⊘ ", FAIL), "every candidate was rejected")

    def _function_merge_completed(self, ev, d) -> None:
        if d.ok and not d.reverted:
            self._fn(ev, ("✓ ", NEON), f"merged {(d.commit_sha or '')[:7]}, full suite green")
        else:
            self._fn(ev, ("✗ ", FAIL), f"merge reverted{self._err(d)}")

    def _function_completed(self, ev, d) -> None:
        style = OUTCOME_STYLE.get(d.outcome, "")
        parts: list[Any] = [(d.outcome.replace("_", " "), f"bold {style}")]
        if d.delta_pct is not None and d.outcome == "accepted":
            parts.append(f"  {minus(d.delta_pct)}% CO₂/call")
            if d.delta_ci_pct:
                parts.append((f" [{minus(d.delta_ci_pct.lo)}, {minus(d.delta_ci_pct.hi)}]", MUTED))
        if d.reason and d.outcome != "accepted":
            parts.append((f"  {d.reason}", MUTED))
        parts.append((f"  {fmt_ms(d.duration_ms)}", MUTED))
        self._line(2, ("= ", style), *parts)

    # -- candidates -------------------------------------------------------------------

    def _candidate_write_completed(self, ev, d) -> None:
        if d.ok and d.candidate:
            verb = "repaired" if ev.attempt else "wrote"
            self._cand(
                ev,
                ("✎ ", CAUTION if ev.attempt else NEON),
                f"{verb} rewrite",
                (f"  {d.candidate.strategy}", MUTED),
            )
        else:
            self._cand(ev, ("✗ ", FAIL), f"rewrite failed{self._err(d)}")

    def _candidate_check_completed(self, ev, d) -> None:
        if d.ok:
            n = d.differential.n_samples if d.differential else 0
            self._cand(ev, ("✓ ", NEON), f"tests + differential on {n} inputs{self._repair(ev)}")

    def _candidate_rejected(self, ev, d) -> None:
        detail = f": {d.detail}" if d.detail else ""
        self._cand(ev, ("⊘ ", FAIL), (f"rejected ({d.reason.replace('_', ' ')}){detail}", FAIL))

    def _candidate_bench_completed(self, ev, d) -> None:
        s = d.stats
        if not (d.ok and s):
            self._cand(ev, ("✗ ", FAIL), f"bench failed{self._err(d)}")
            return
        style = NEON if s.significant else MUTED
        self._cand(
            ev,
            ("◆ ", style),
            (f"{minus(s.delta_pct)}%", f"bold {style}" if s.significant else style),
            f" [{minus(s.delta_ci_pct.lo)}, {minus(s.delta_ci_pct.hi)}] p={s.p_value:.3g}",
            ("" if s.significant else "  ns", MUTED),
        )

    # -- misc -------------------------------------------------------------------------

    def _log(self, ev, d) -> None:
        if self.verbose or d.level in ("warn", "error"):
            style = FAIL if d.level == "error" else CAUTION if d.level == "warn" else MUTED
            for line in d.lines[-5:]:
                self._line(2 if ev.function_id else 0, (f"{d.source}: {line}", style))
