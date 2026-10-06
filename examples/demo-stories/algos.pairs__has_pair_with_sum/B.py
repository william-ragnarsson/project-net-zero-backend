def has_pair_with_sum(numbers: list[int], target: int) -> bool:
    """Tell whether two different positions in ``numbers`` add up to ``target``.

    The same position may not be used twice, but two equal numbers at
    different positions count as a pair.
    """
    numbers.sort()
    lo, hi = 0, len(numbers) - 1
    while lo < hi:
        total = numbers[lo] + numbers[hi]
        if total == target:
            return True
        if total < target:
            lo += 1
        else:
            hi -= 1
    return False
