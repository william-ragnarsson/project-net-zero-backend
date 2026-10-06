"""What each LLM call cost, in USD, for the running total shown next to the run.

Prices are USD per million tokens from the claude-api skill's model table. A
5-minute cache write costs 1.25x input; the cache-read rate differs by model.
An unknown model costs 0.0 and says so, rather than failing a run over a
missing price.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)
_DATE_SUFFIX = re.compile(r"-\d{8}$")


@dataclass(frozen=True)
class Price:
    """USD per million tokens."""

    input: float
    output: float
    cache_write: float  # 5-minute TTL
    cache_read: float


PRICES: dict[str, Price] = {
    "claude-haiku-4-5": Price(input=1.00, output=5.00, cache_write=1.25, cache_read=0.10),
    "claude-sonnet-5-5": Price(input=2.00, output=10.00, cache_write=2.50, cache_read=0.20),
    "claude-opus-5-5": Price(input=4.00, output=20.00, cache_write=5.00, cache_read=0.20),
    "claude-fable-5-1": Price(input=10.00, output=50.00, cache_write=12.50, cache_read=0.25),
}


def base_model(model: str) -> str:
    """``claude-haiku-4-5-20251001`` -> ``claude-haiku-4-5``: a dated id prices like its alias."""
    return _DATE_SUFFIX.sub("", model)


def price_for(model: str) -> Price | None:
    return PRICES.get(model) or PRICES.get(base_model(model))


def token_counts(usage: object) -> dict[str, int]:
    """The four billed token counts of an SDK ``Usage``, a mapping or ``LlmUsageData``."""
    if isinstance(usage, dict):
        return {name: int(usage.get(name) or 0) for name in TOKEN_FIELDS}
    return {name: int(getattr(usage, name, 0) or 0) for name in TOKEN_FIELDS}


def cost_usd(model: str, usage: object, *, warn: Callable[[str], None] | None = None) -> float:
    price = price_for(model)
    if price is None:
        (warn or log.warning)(f"no price for model {model!r}: its calls count as $0")
        return 0.0
    t = token_counts(usage)
    total = (
        t["input_tokens"] * price.input
        + t["output_tokens"] * price.output
        + t["cache_creation_input_tokens"] * price.cache_write
        + t["cache_read_input_tokens"] * price.cache_read
    )
    return total / 1e6
