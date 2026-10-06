def total_by_category(rows: list[dict[str, Any]], *, amount_key: str = "amount") -> dict[str, int]:
    """Sum ``row[amount_key]`` per ``row["category"]``.

    Amounts are integers (for example cents). Categories appear in the order
    they are first seen in ``rows``; an empty input gives an empty dict.
    """
    get_category = itemgetter("category")
    get_amount = itemgetter(amount_key)
    categories = []
    for row in rows:
        if get_category(row) not in categories:
            categories.append(get_category(row))
    totals = {}
    for category in categories:
        totals[category] = sum(get_amount(row) for row in rows if get_category(row) == category)
    return totals
