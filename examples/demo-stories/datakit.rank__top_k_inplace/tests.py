import pytest
from datakit.rank import top_k_inplace


def test_returns_largest_first():
    assert top_k_inplace([3.5, 9.0, 1.25, 7.0], 2) == [9.0, 7.0]


def test_zero_or_negative_k_gives_empty():
    assert top_k_inplace([2.0, 9.0, 4.0], 0) == []
    assert top_k_inplace([2.0, 9.0, 4.0], -3) == []


def test_k_larger_than_the_list():
    assert top_k_inplace([1.0, 3.0, 2.0], 10) == [3.0, 2.0, 1.0]


def test_keeps_duplicates():
    assert top_k_inplace([5.0, 1.0, 5.0, 3.0, 5.0], 4) == [5.0, 5.0, 5.0, 3.0]


def test_empty_scores():
    assert top_k_inplace([], 3) == []


@pytest.mark.nz_workload
def test_workload_leaderboard():
    scores = [((i * 7919) % 100003) / 1000 for i in range(100000)]
    top = top_k_inplace(scores, 10)
    assert len(top) == 10
    assert top[:5] == [100.002, 100.001, 100.0, 99.999, 99.998]
    assert top[-1] == 99.993
