"""Statistics for the bench: Welch's t on log CO2e per call, Holm, and the decision gate.

Per-call grams are positive and their noise is multiplicative (a slower clock or
a busier core scales every call), so the tests run on ``log(g)``: a difference
of log means is a ratio, and ``delta_pct = (exp(mean log diff) - 1) * 100`` is
the change of the geometric mean.

CI level: ``delta_ci_pct`` is a two-sided 95% Welch-Satterthwaite t interval
(``BenchStats`` documents "95% CI" and the UI prints it). That is stricter than
the one-sided test at ``alpha = 0.05`` (a one-sided 90% bound), so the gate's
"CI upper end below 0" adds a margin on top of the p-value rather than repeating it.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy import stats as sps

from netzero.events import (
    CI,
    BenchStats,
    CandidateId,
    DecisionRule,
    FunctionDecisionData,
    Interval,
    RankingEntry,
)
from netzero.pipeline.common import fmt_p

CI_LEVEL = 0.95

# (candidate_id, stats or None when not benched, reject reason or None, diff length)
Row = tuple[CandidateId, BenchStats | None, str | None, int]


@dataclass(frozen=True)
class Comparison:
    delta_pct: float
    ci: CI
    p_value: float


def _arr(values: Sequence[float]) -> np.ndarray:
    x = np.asarray(values, dtype=float)
    if x.size == 0:
        raise ValueError("no values")
    return x


def _two_arms(log_cand: Sequence[float], log_orig: Sequence[float]) -> tuple[np.ndarray, ...]:
    c, o = _arr(log_cand), _arr(log_orig)
    if c.size < 2 or o.size < 2:
        raise ValueError("Welch's t needs at least 2 trials per arm")
    return c, o


def _pct(log_ratio: float) -> float:
    return math.expm1(log_ratio) * 100.0


def _t(level: float, df: float) -> float:
    return float(sps.t.ppf(0.5 + level / 2, df))


def welch_less(log_cand: Sequence[float], log_orig: Sequence[float]) -> float:
    """One-sided p that the candidate's mean log g is below the original's."""
    c, o = _two_arms(log_cand, log_orig)
    exact = 0.0 if c.mean() < o.mean() else 1.0
    if np.ptp(c) == 0 and np.ptp(o) == 0:  # both arms constant: the difference is exact
        return exact
    p = float(sps.ttest_ind(c, o, equal_var=False, alternative="less").pvalue)
    return exact if math.isnan(p) else p


def delta_ci(
    log_cand: Sequence[float], log_orig: Sequence[float], level: float = CI_LEVEL
) -> tuple[float, CI]:
    """``delta_pct`` and its Welch-Satterthwaite t interval, mapped from log space to %."""
    c, o = _two_arms(log_cand, log_orig)
    diff = float(c.mean() - o.mean())
    vc, vo = c.var(ddof=1) / c.size, o.var(ddof=1) / o.size
    se = math.sqrt(vc + vo)
    if se == 0:
        return _pct(diff), CI(lo=_pct(diff), hi=_pct(diff))
    df = (vc + vo) ** 2 / (vc**2 / (c.size - 1) + vo**2 / (o.size - 1))
    half = _t(level, df) * se
    return _pct(diff), CI(lo=_pct(diff - half), hi=_pct(diff + half))


def compare_logs(log_cand: Sequence[float], log_orig: Sequence[float]) -> Comparison:
    delta, ci = delta_ci(log_cand, log_orig)
    return Comparison(delta_pct=delta, ci=ci, p_value=welch_less(log_cand, log_orig))


def interval(values: Sequence[float], level: float = CI_LEVEL) -> Interval:
    """Mean with a t interval and the sample std (one value: a zero-width interval)."""
    x = _arr(values)
    m = float(x.mean())
    if x.size < 2:
        return Interval(mean=m, ci_low=m, ci_high=m, std=0.0)
    s = float(x.std(ddof=1))
    half = _t(level, x.size - 1) * s / math.sqrt(x.size)
    return Interval(mean=m, ci_low=m - half, ci_high=m + half, std=s)


def cv_pct(values: Sequence[float]) -> float:
    """Coefficient of variation in %; 0 when it is undefined."""
    x = _arr(values)
    m = float(x.mean())
    if x.size < 2 or m == 0:
        return 0.0
    return float(x.std(ddof=1)) / abs(m) * 100.0


def holm(pvals: Sequence[float]) -> list[float]:
    """Holm step-down adjusted p-values, in input order: monotone and capped at 1."""
    m = len(pvals)
    out = [0.0] * m
    running = 0.0
    for rank, i in enumerate(sorted(range(m), key=lambda i: pvals[i])):
        running = max(running, min(1.0, (m - rank) * pvals[i]))
        out[i] = running
    return out


def sanity_ok(delta_pct: float, cpu_delta_pct: float, min_effect: float) -> bool:
    """False when CO2e moved by at least ``min_effect`` but CPU time moved the other
    way: the energy model ties the two, so the measurement is not to be trusted."""
    if abs(delta_pct) < min_effect:
        return True
    return delta_pct * cpu_delta_pct >= 0


def passes_gate(p: float, ci_hi: float, delta_pct: float, rule: DecisionRule) -> bool:
    return p < rule.alpha and ci_hi < 0 and delta_pct <= -rule.min_effect_pct


def why_not(p_holm: float, ci: CI, delta_pct: float, rule: DecisionRule) -> str:
    if delta_pct > -rule.min_effect_pct:
        return f"below the {rule.min_effect_pct:g}% minimum effect"
    if ci.hi >= 0:
        return "95% CI includes 0"
    if p_holm >= rule.alpha:
        return f"{fmt_p(p_holm, 'p_holm')} ≥ α={rule.alpha:g}"
    return ""


def _rejected(cid: CandidateId, st: BenchStats | None, reject: str | None) -> RankingEntry:
    if st is None:
        return RankingEntry(candidate_id=cid, status="rejected", reason=reject or "bench_failed")
    reason = reject or (
        "measurement_inconsistent: CO₂ and CPU time moved in opposite directions "
        f"({st.delta_pct:+.1f}% vs CPU {st.cpu_time_delta_pct:+.1f}%)"
    )
    return RankingEntry(
        candidate_id=cid,
        status="rejected",
        delta_pct=st.delta_pct,
        delta_ci_pct=st.delta_ci_pct,
        p_value=st.p_value,
        reason=reason,
    )


def decide(rows: Sequence[Row], rule: DecisionRule) -> FunctionDecisionData:
    """Holm over the eligible candidates (benched, sanity_ok, not rejected); the
    winner passes the gate on ``p_holm`` and has the lowest delta (ties: smaller
    diff, then A < B < C). The ranking lists every candidate, eligible first."""
    eligible = [r for r in rows if r[1] is not None and r[2] is None and r[1].sanity_ok]
    eligible.sort(key=lambda r: (r[1].delta_pct, r[3], r[0]))  # type: ignore[union-attr]
    adjusted = holm([r[1].p_value for r in eligible])  # type: ignore[union-attr]
    ranking: list[RankingEntry] = []
    winner: CandidateId | None = None
    for (cid, st, _, _), ph in zip(eligible, adjusted, strict=True):
        assert st is not None
        sig = passes_gate(ph, st.delta_ci_pct.hi, st.delta_pct, rule)
        if sig and winner is None:
            winner, reason = cid, "largest significant reduction"
        elif sig:
            reason = f"significant; {winner} saves more"
        else:
            reason = why_not(ph, st.delta_ci_pct, st.delta_pct, rule)
        ranking.append(
            RankingEntry(
                candidate_id=cid,
                status="eligible",
                delta_pct=st.delta_pct,
                delta_ci_pct=st.delta_ci_pct,
                p_value=st.p_value,
                p_holm=ph,
                significant=sig,
                reason=reason,
            )
        )
    taken = {e.candidate_id for e in ranking}
    rest = [r for r in rows if r[0] not in taken]
    rest.sort(key=lambda r: (r[1] is None, r[1].delta_pct if r[1] else 0.0, r[0]))
    ranking += [_rejected(cid, st, reject) for cid, st, reject, _ in rest]
    if winner is not None:
        outcome = "winner"
    elif eligible:
        outcome = "no_significant_win"
    else:
        outcome = "all_rejected"
    return FunctionDecisionData(outcome=outcome, winner=winner, ranking=ranking, rule=rule)


def with_holm(rows: Sequence[Row], decision: FunctionDecisionData) -> dict[CandidateId, BenchStats]:
    """Copies of the benched candidates' stats with ``p_holm`` filled in from the
    decision (``BenchStats.significant`` stays the raw-p gate it documents)."""
    p_holm = {e.candidate_id: e.p_holm for e in decision.ranking}
    return {
        cid: st.model_copy(update={"p_holm": p_holm.get(cid)})
        for cid, st, _, _ in rows
        if st is not None
    }
