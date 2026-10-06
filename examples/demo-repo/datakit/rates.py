"""Currency conversion backed by a public reference-rate API."""

import json
import urllib.request

RATES_URL = "https://api.frankfurter.app/latest?from={base}"


def fetch_exchange_rates(base: str = "EUR") -> dict[str, float]:
    """Download the latest reference exchange rates for the ``base`` currency.

    Returns ``currency code -> units of that currency per one base unit``.
    Needs network access.
    """
    url = RATES_URL.format(base=base)
    with urllib.request.urlopen(url, timeout=10) as response:
        payload = json.load(response)
    return {code: float(rate) for code, rate in payload["rates"].items()}
