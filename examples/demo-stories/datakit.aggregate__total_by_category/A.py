def total_by_category(rows: list[dict[str, Any]], *, amount_key: str = "amount") -> dict[str, int]:
    """Sum ``row[amount_key]`` per ``row["category"]``.

    Amounts are integers (for example cents). Categories appear in the order
    they are first seen in ``rows``; an empty input gives an empty dict.
    """
    totals = {}
    for row in rows:
        category = row["category"]
        totals[category] = totals.get(category, 0) + row[amount_key]
    return totals
