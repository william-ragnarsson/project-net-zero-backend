def has_pair_with_sum(numbers: list[int], target: int) -> bool:
    """Tell whether two different positions in ``numbers`` add up to ``target``.

    The same position may not be used twice, but two equal numbers at
    different positions count as a pair.
    """
    n = len(numbers)
    for i in range(n):
        need = target - numbers[i]
        for j in range(i + 1, n):
            if numbers[j] == need:
                return True
    return False
