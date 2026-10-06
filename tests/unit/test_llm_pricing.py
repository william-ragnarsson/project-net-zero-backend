"""Per-call cost from token counts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from netzero.llm import pricing

M = 1_000_000


def usage(i: int = 0, o: int = 0, cw: int | None = 0, cr: int | None = 0) -> dict[str, int | None]:
    return {
        "input_tokens": i,
        "output_tokens": o,
        "cache_creation_input_tokens": cw,
        "cache_read_input_tokens": cr,
    }


@pytest.mark.parametrize(
    ("model", "u", "expected"),
    [
        ("claude-haiku-4-5", usage(i=M), 1.00),
        ("claude-haiku-4-5", usage(o=M), 5.00),
        ("claude-haiku-4-5", usage(cw=M), 1.25),
        ("claude-haiku-4-5", usage(cr=M), 0.10),
        ("claude-sonnet-5-5", usage(i=M, o=M), 12.00),
        ("claude-sonnet-5-5", usage(cr=M), 0.20),
        ("claude-opus-5-5", usage(cw=M, cr=M), 5.20),
        ("claude-fable-5-1", usage(i=M, o=M, cw=M, cr=M), 72.75),
    ],
)
def test_cost_per_million_tokens(model: str, u: dict, expected: float) -> None:
    assert pricing.cost_usd(model, u) == pytest.approx(expected)


def test_cache_write_is_one_and_a_quarter_input() -> None:
    for price in pricing.PRICES.values():
        assert price.cache_write == pytest.approx(1.25 * price.input)


def test_small_call_cost() -> None:
    # 3k input, 1.2k output, 4k cache read on Haiku
    cost = pricing.cost_usd("claude-haiku-4-5", usage(i=3000, o=1200, cr=4000))
    assert cost == pytest.approx((3000 * 1.0 + 1200 * 5.0 + 4000 * 0.1) / M)


def test_dated_model_id_prices_like_its_alias() -> None:
    assert pricing.price_for("claude-haiku-4-5-20251001") == pricing.PRICES["claude-haiku-4-5"]
    assert pricing.base_model("claude-haiku-4-5-20251001") == "claude-haiku-4-5"
    assert pricing.base_model("claude-opus-5-5") == "claude-opus-5-5"


def test_unknown_model_costs_nothing_and_warns() -> None:
    warnings: list[str] = []
    assert pricing.cost_usd("claude-mystery-9", usage(i=M), warn=warnings.append) == 0.0
    assert len(warnings) == 1 and "claude-mystery-9" in warnings[0]


def test_token_counts_accepts_objects_and_missing_cache_fields() -> None:
    sdk_usage = SimpleNamespace(
        input_tokens=10,
        output_tokens=5,
        cache_creation_input_tokens=None,
        cache_read_input_tokens=7,
    )
    assert pricing.token_counts(sdk_usage) == {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 7,
    }
    assert pricing.token_counts({"input_tokens": 3}) == {
        "input_tokens": 3,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
