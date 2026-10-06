"""Random walks, for quick simulations."""

import random


def random_walk(steps: int, *, step_size: float = 1.0, start: float = 0.0) -> list[float]:
    """Simulate a one-dimensional random walk and return every position visited.

    Each step moves ``step_size`` left or right with equal probability. The
    result starts with ``start`` and has ``steps + 1`` positions.
    """
    position = start
    path = [position]
    for _ in range(steps):
        position += random.choice((-step_size, step_size))
        path.append(position)
    return path
