def has_pair_with_sum(numbers: list[int], target: int) -> bool:
    """Tell whether two different positions in ``numbers`` add up to ``target``.

    The same position may not be used twice, but two equal numbers at
    different positions count as a pair.
    """
    ordered = sorted(numbers)
    lo, hi = 0, len(ordered) - 1
    while lo < hi:
        total = ordered[lo] + ordered[hi]
        if total == target:
            return True
        if total < target:
            lo += 1
        else:
            hi -= 1
    return False
