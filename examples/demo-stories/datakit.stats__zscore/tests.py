import math

import pytest
from datakit.stats import zscore


def test_empty_values():
    assert zscore([]) == []


def test_constant_values_score_zero():
    assert zscore([2.5, 2.5, 2.5, 2.5]) == [0.0, 0.0, 0.0, 0.0]


def test_three_values():
    assert zscore([1.0, 2.0, 3.0]) == pytest.approx([-math.sqrt(1.5), 0.0, math.sqrt(1.5)])


def test_scores_have_mean_zero_and_unit_spread():
    scores = zscore([12.5, 3.0, 7.25, 19.0, 3.0, 8.5])
    assert sum(scores) == pytest.approx(0.0, abs=1e-12)
    assert sum(s * s for s in scores) / len(scores) == pytest.approx(1.0)


def test_large_offset_keeps_precision():
    base = [0.25, 0.5, 0.75, 1.5]
    shifted = [1e9 + b for b in base]
    assert zscore(shifted) == pytest.approx(zscore(base))


@pytest.mark.nz_workload
def test_workload_sensor_readings():
    values = [((i * 7919) % 10007) / 100 + (i % 13) * 0.25 for i in range(80_000)]
    scores = zscore(values)
    assert len(scores) == 80_000
    assert scores[:3] == pytest.approx(
        [-1.7829554826819813, 0.9656315482955723, 0.2518431488863714]
    )
    assert scores[-1] == pytest.approx(1.3963957544344263)
    assert sum(scores) == pytest.approx(0.0, abs=1e-6)
    assert sum(s * s for s in scores) / len(scores) == pytest.approx(1.0)
