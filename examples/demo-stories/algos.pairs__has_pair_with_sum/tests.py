import pytest
from algos.pairs import has_pair_with_sum


def test_empty_and_single():
    assert has_pair_with_sum([], 0) is False
    assert has_pair_with_sum([7], 7) is False


def test_finds_a_pair():
    assert has_pair_with_sum([3, 8, 1, 4], 12) is True
    assert has_pair_with_sum([3, 8, 1, 4], 2) is False


def test_same_position_not_used_twice():
    assert has_pair_with_sum([5], 10) is False
    assert has_pair_with_sum([1, 5, 2], 10) is False


def test_equal_numbers_at_different_positions():
    assert has_pair_with_sum([5, 3, 5], 10) is True


def test_negative_numbers():
    assert has_pair_with_sum([-4, 9, 1], 5) is True
    assert has_pair_with_sum([-4, -9, 1], 0) is False


@pytest.mark.nz_workload
def test_workload_long_list():
    numbers = [(i * 7919) % 100003 for i in range(800)]
    assert has_pair_with_sum(numbers, 250001) is False
    assert has_pair_with_sum(numbers, numbers[-1] + numbers[-2]) is True
