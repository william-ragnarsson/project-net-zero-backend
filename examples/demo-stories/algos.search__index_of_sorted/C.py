def index_of_sorted(items: list[int], targets: list[int]) -> list[int]:
    """Find each of ``targets`` in the sorted list ``items`` by binary search.

    Returns, for every target in order, the index of its first occurrence in
    ``items``, or -1 when it is absent. ``items`` must be sorted ascending.
    """
    n = len(items)
    positions = []
    append = positions.append
    search = bisect_left
    for target in targets:
        i = search(items, target)
        append(i if i < n and items[i] == target else -1)
    return positions
