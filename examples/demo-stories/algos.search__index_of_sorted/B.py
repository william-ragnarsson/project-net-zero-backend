def index_of_sorted(items: list[int], targets: list[int]) -> list[int]:
    """Find each of ``targets`` in the sorted list ``items`` by binary search.

    Returns, for every target in order, the index of its first occurrence in
    ``items``, or -1 when it is absent. ``items`` must be sorted ascending.
    """
    n = len(items)
    return [
        i if (i := bisect_left(items, target)) < n and items[i] == target else -1
        for target in targets
    ]
