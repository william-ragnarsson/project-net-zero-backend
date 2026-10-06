def zscore(values: list[float]) -> list[float]:
    """Standardise ``values`` to mean 0 and population standard deviation 1.

    An empty input gives an empty list. When every value is the same the
    spread is zero, and every score is 0.0.
    """
    n = len(values)
    if n == 0:
        return []
    mean = sum(values) / n
    deviations = [v - mean for v in values]
    variance = math.sumprod(deviations, deviations) / n
    if variance == 0:
        return [0.0] * n
    scale = 1 / math.sqrt(variance)
    return list(map(scale.__mul__, deviations))
