import pytest
from algos.search import index_of_sorted


def test_finds_present_targets():
    assert index_of_sorted([2, 4, 6, 8], [6, 2, 8]) == [2, 0, 3]


def test_absent_targets_give_minus_one():
    assert index_of_sorted([2, 4, 6, 8], [1, 5, 9]) == [-1, -1, -1]


def test_first_occurrence_of_duplicates():
    assert index_of_sorted([1, 3, 3, 3, 7], [3, 7, 1]) == [1, 4, 0]


def test_negative_numbers():
    assert index_of_sorted([-5, -2, 0, 4], [0, -5, 3]) == [2, 0, -1]


def test_empty_inputs():
    assert index_of_sorted([], [1, 2]) == [-1, -1]
    assert index_of_sorted([1, 2], []) == []


@pytest.mark.nz_workload
def test_workload_lookup_batch():
    items = sorted((i * 7919) % 50021 for i in range(100_000))
    targets = [
        items[(j * 37) % 100_000] if j % 2 == 0 else (j * 7919 + 13) % 60_000 for j in range(20_000)
    ]
    positions = index_of_sorted(items, targets)
    assert len(positions) == 20_000
    assert sum(p >= 0 for p in positions) == 18337
    assert positions[:6] == [0, 15858, 74, 47521, 148, 79183]
    assert sum(positions) == 900733464
