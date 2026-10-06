def moving_average(values: list[float], window: int) -> list[float]:
    """Return the mean of every run of ``window`` consecutive values.

    The result has ``len(values) - window + 1`` entries (none when the series
    is shorter than the window). Raises ``ValueError`` if ``window`` is not
    positive.
    """
    if window <= 0:
        raise ValueError("window must be positive")
    return [sum(values[end - window : end]) / window for end in range(window, len(values) + 1)]
