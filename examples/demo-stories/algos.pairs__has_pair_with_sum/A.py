def has_pair_with_sum(numbers: list[int], target: int) -> bool:
    """Tell whether two different positions in ``numbers`` add up to ``target``.

    The same position may not be used twice, but two equal numbers at
    different positions count as a pair.
    """
    seen = set()
    for x in numbers:
        seen.add(x)
        if target - x in seen:
            return True
    return False
