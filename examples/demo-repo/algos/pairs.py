"""Pair queries over lists of numbers."""


def has_pair_with_sum(numbers: list[int], target: int) -> bool:
    """Tell whether two different positions in ``numbers`` add up to ``target``.

    The same position may not be used twice, but two equal numbers at
    different positions count as a pair.
    """
    for i in range(len(numbers)):
        for j in range(i + 1, len(numbers)):
            if numbers[i] + numbers[j] == target:
                return True
    return False
