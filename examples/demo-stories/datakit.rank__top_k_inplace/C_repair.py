def top_k_inplace(scores: list[float], k: int) -> list[float]:
    """Return the ``k`` largest scores, highest first.

    ``scores`` is sorted in place, highest first, so a caller can keep using
    the full ranking afterwards. A ``k`` of zero or less gives an empty list.
    """
    scores.sort()
    scores.reverse()
    return scores[:k] if k > 0 else []
