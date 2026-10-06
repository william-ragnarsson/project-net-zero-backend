"""Group-by style aggregation over lists of records."""

from typing import Any


def total_by_category(rows: list[dict[str, Any]], *, amount_key: str = "amount") -> dict[str, int]:
    """Sum ``row[amount_key]`` per ``row["category"]``.

    Amounts are integers (for example cents). Categories appear in the order
    they are first seen in ``rows``; an empty input gives an empty dict.
    """
    categories = []
    for row in rows:
        if row["category"] not in categories:
            categories.append(row["category"])
    totals = {}
    for category in categories:
        totals[category] = sum(row[amount_key] for row in rows if row["category"] == category)
    return totals
