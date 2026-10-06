"""Bench statistics: Welch on log g, the CI, Holm, the gate and the decision."""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import stats as sps

from netzero.bench import stats
from netzero.events import (
    CI,
    BenchStats,
    DecisionRule,
    GridInfo,
    Interval,
    MeasureStats,
    PowerInfo,
)

RULE = DecisionRule(alpha=0.05, min_effect_pct=5.0)

POWER = PowerInfo(
    power_source="tdp_estimate",
    badge="estimated",
    method="codecarbon_model",
    cpu_model="test",
    tdp_w=10.0,
    cpu_count=8,
    p_core_w=1.25,
    p_ram_w=3.0,
    grid=GridInfo(country_iso="WORLD", kg_per_kwh=0.475, source="codecarbon world average"),
)


def measure(g: float = 1e-6) -> MeasureStats:
    iv = Interval(mean=g, ci_low=g, ci_high=g, std=0.0)
    return MeasureStats(
        g_per_call=iv,
        kwh_per_call=iv,
        kwh_cpu_per_call=g,
        kwh_ram_per_call=0.0,
        cpu_s_per_call=iv,
        wall_s_per_call=iv,
        n_trials=2,
        calls_per_trial=1,
        trials_g=[g, g],
    )


def bench(
    delta: float,
    p: float,
    ci: tuple[float, float] | None = None,
    *,
    cpu_delta: float | None = None,
    sane: bool = True,
) -> BenchStats:
    lo, hi = ci if ci is not None else (delta - 5.0, delta + 5.0)
    return BenchStats(
        original=measure(),
        candidate=measure(),
        delta_pct=delta,
        delta_ci_pct=CI(lo=lo, hi=hi),
        p_value=p,
        significant=False,
        n_trials=2,
        calls_per_trial=1,
        cpu_time_delta_pct=delta if cpu_delta is None else cpu_delta,
        sanity_ok=sane,
        power=POWER,
        g_saved_per_1m_calls=0.0,
        kwh_saved_per_1m_calls=0.0,
    )


# -- Welch and the CI ---------------------------------------------------------------------


def test_welch_less_matches_scipy():
    rng = np.random.default_rng(1)
    c, o = rng.normal(-0.2, 0.05, 12), rng.normal(0.0, 0.08, 10)
    want = sps.ttest_ind(c, o, equal_var=False, alternative="less").pvalue
    assert stats.welch_less(c, o) == pytest.approx(want)
    assert stats.welch_less(c, o) < 1e-4
    assert stats.welch_less(o, c) > 0.999  # the other direction is one-sided too


def test_welch_less_constant_arms():
    assert stats.welch_less([1.0, 1.0], [2.0, 2.0]) == 0.0
    assert stats.welch_less([2.0, 2.0], [1.0, 1.0]) == 1.0
    assert stats.welch_less([1.0, 1.0], [1.0, 1.0]) == 1.0


def test_welch_needs_two_trials_per_arm():
    with pytest.raises(ValueError):
        stats.welch_less([1.0], [1.0, 2.0])
    with pytest.raises(ValueError):
        stats.delta_ci([], [1.0, 2.0])


def test_delta_ci_by_hand():
    c, o = [0.0, 0.2], [0.4, 0.8]
    delta, ci = stats.delta_ci(c, o)
    diff = 0.1 - 0.6
    vc, vo = 0.02 / 2, 0.08 / 2  # sample variances / n
    se = math.sqrt(vc + vo)
    df = (vc + vo) ** 2 / (vc**2 + vo**2)  # n - 1 == 1 for both arms
    half = sps.t.ppf(0.975, df) * se
    assert delta == pytest.approx(math.expm1(diff) * 100)
    assert ci.lo == pytest.approx(math.expm1(diff - half) * 100)
    assert ci.hi == pytest.approx(math.expm1(diff + half) * 100)
    assert ci.lo < delta < ci.hi


def test_delta_ci_degenerate():
    delta, ci = stats.delta_ci([math.log(0.5)] * 3, [0.0] * 3)
    assert delta == pytest.approx(-50.0)
    assert ci.lo == ci.hi == pytest.approx(-50.0)


def test_compare_logs_halved():
    rng = np.random.default_rng(7)
    o = rng.normal(0.0, 0.02, 16)
    c = rng.normal(math.log(0.5), 0.02, 16)
    cmp = stats.compare_logs(c, o)
    assert cmp.delta_pct == pytest.approx(-50.0, abs=2.0)
    assert cmp.ci.lo < cmp.delta_pct < cmp.ci.hi < 0
    assert cmp.p_value < 1e-10


# -- intervals, CV, Holm ------------------------------------------------------------------


def test_interval():
    iv = stats.interval([1.0, 2.0, 3.0])
    half = sps.t.ppf(0.975, 2) * 1.0 / math.sqrt(3)
    assert iv.mean == pytest.approx(2.0)
    assert iv.std == pytest.approx(1.0)
    assert (iv.ci_low, iv.ci_high) == pytest.approx((2.0 - half, 2.0 + half))
    one = stats.interval([4.0])
    assert (one.mean, one.ci_low, one.ci_high, one.std) == (4.0, 4.0, 4.0, 0.0)
    with pytest.raises(ValueError):
        stats.interval([])


def test_cv_pct():
    assert stats.cv_pct([1.0, 2.0, 3.0]) == pytest.approx(50.0)
    assert stats.cv_pct([5.0]) == 0.0
    assert stats.cv_pct([0.0, 0.0]) == 0.0


def test_holm_by_hand():
    # sorted: 0.01*3=0.03, 0.03*2=0.06, 0.04*1=0.04 -> running max 0.06
    assert stats.holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert stats.holm([0.5, 0.6]) == pytest.approx([1.0, 1.0])  # capped
    assert stats.holm([0.02]) == pytest.approx([0.02])
    assert stats.holm([]) == []


# -- sanity and the gate ------------------------------------------------------------------


def test_sanity_ok():
    assert stats.sanity_ok(-30.0, -28.0, 5.0)
    assert not stats.sanity_ok(-30.0, 10.0, 5.0)  # CO2 down, CPU up
    assert not stats.sanity_ok(20.0, -1.0, 5.0)
    assert stats.sanity_ok(-4.9, 10.0, 5.0)  # too small to judge
    assert stats.sanity_ok(-30.0, 0.0, 5.0)


def test_gate_edges():
    assert stats.passes_gate(0.01, -1.0, -5.0, RULE)  # exactly the minimum effect
    assert not stats.passes_gate(0.01, -1.0, -4.99, RULE)
    assert not stats.passes_gate(0.01, 0.0, -20.0, RULE)  # the CI touches 0
    assert not stats.passes_gate(0.05, -1.0, -20.0, RULE)  # p must be below alpha
    assert stats.passes_gate(0.0499, -1.0, -20.0, RULE)


# -- the decision -------------------------------------------------------------------------


def test_decide_winner_and_ranking():
    rows = [
        ("A", bench(-20.0, 0.001, (-25.0, -15.0)), None, 40),
        ("B", bench(-40.0, 0.002, (-45.0, -35.0)), None, 60),
        ("C", bench(-2.0, 0.3, (-8.0, 4.0)), None, 10),
    ]
    d = stats.decide(rows, RULE)
    assert d.outcome == "winner" and d.winner == "B"
    assert [e.candidate_id for e in d.ranking] == ["B", "A", "C"]
    b, a, c = d.ranking
    # Holm by p: A 0.001*3, B 0.002*2, C 0.3*1
    assert b.p_holm == pytest.approx(0.004) and b.significant
    assert b.reason == "largest significant reduction"
    assert a.p_holm == pytest.approx(0.003) and a.reason == "significant; B saves more"
    assert c.p_holm == pytest.approx(0.3) and not c.significant
    assert c.reason == "below the 5% minimum effect"
    assert all(e.status == "eligible" for e in d.ranking)
    assert d.rule == RULE


def test_decide_holm_can_remove_the_win():
    # raw p 0.03 passes alone; Holm over two candidates makes it 0.06
    rows = [
        ("A", bench(-20.0, 0.03, (-30.0, -10.0)), None, 10),
        ("B", bench(-10.0, 0.04, (-15.0, -5.0)), None, 10),
    ]
    d = stats.decide(rows, RULE)
    assert d.outcome == "no_significant_win" and d.winner is None
    assert d.ranking[0].reason == "p_holm=0.06 ≥ α=0.05"
    assert stats.decide(rows[:1], RULE).winner == "A"


def test_decide_ci_reason():
    d = stats.decide([("A", bench(-20.0, 0.001, (-30.0, 0.0)), None, 1)], RULE)
    assert d.outcome == "no_significant_win"
    assert d.ranking[0].reason == "95% CI includes 0"


def test_decide_tie_breaks():
    same = (-30.0, 0.001, (-35.0, -25.0))
    rows = [
        ("C", bench(*same), None, 50),
        ("A", bench(*same), None, 50),
        ("B", bench(*same), None, 20),
    ]
    d = stats.decide(rows, RULE)
    assert d.winner == "B"  # smaller diff first, then A < C
    assert [e.candidate_id for e in d.ranking] == ["B", "A", "C"]


def test_decide_rejections():
    rows = [
        ("A", None, "tests_failed: 2 failed", 30),
        ("B", bench(-30.0, 0.001, cpu_delta=12.0, sane=False), None, 30),
        ("C", None, None, 30),
    ]
    d = stats.decide(rows, RULE)
    assert d.outcome == "all_rejected" and d.winner is None
    assert [e.candidate_id for e in d.ranking] == ["B", "A", "C"]  # benched first
    b, a, c = d.ranking
    assert all(e.status == "rejected" for e in d.ranking)
    assert b.reason == (
        "measurement_inconsistent: CO₂ and CPU time moved in opposite directions "
        "(-30.0% vs CPU +12.0%)"
    )
    assert b.delta_pct == -30.0 and b.p_holm is None and not b.significant
    assert a.reason == "tests_failed: 2 failed"
    assert c.reason == "bench_failed"


def test_decide_rejected_after_bench_keeps_stats():
    rows = [("A", bench(-30.0, 0.001), "flaky: failed on rerun", 30)]
    d = stats.decide(rows, RULE)
    assert d.outcome == "all_rejected"
    assert d.ranking[0].reason == "flaky: failed on rerun"
    assert d.ranking[0].delta_pct == -30.0


def test_with_holm():
    rows = [
        ("A", bench(-20.0, 0.01, (-25.0, -15.0)), None, 10),
        ("B", bench(-30.0, 0.02, cpu_delta=5.0, sane=False), None, 10),
        ("C", None, "tests_failed", 10),
    ]
    d = stats.decide(rows, RULE)
    out = stats.with_holm(rows, d)
    assert set(out) == {"A", "B"}
    assert out["A"].p_holm == pytest.approx(0.01)  # one eligible: no correction
    assert out["B"].p_holm is None
    assert rows[0][1] is not None and rows[0][1].p_holm is None  # copies
