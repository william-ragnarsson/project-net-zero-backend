"""Unit conversions for the bench: joules, kWh, grams CO2e and per-1M-call figures.

The EUR prices that turn these into money live in ``Settings.assumptions()``;
the client does that projection.
"""

from __future__ import annotations

J_PER_KWH = 3.6e6
PER_MILLION = 1_000_000


def kwh(joules: float) -> float:
    return joules / J_PER_KWH


def grams(kwh_: float, kg_per_kwh: float) -> float:
    """Grams CO2e for ``kwh_`` at a grid intensity in kg/kWh."""
    return kwh_ * kg_per_kwh * 1000.0


def per_million(per_call: float) -> float:
    return per_call * PER_MILLION


def saved_per_million(original_per_call: float, candidate_per_call: float) -> float:
    """What 1M calls of the candidate save over the original (positive == saved)."""
    return per_million(original_per_call - candidate_per_call)
