import pytest
from algos.primes import primes_below


def test_no_primes_below_two():
    assert primes_below(0) == []
    assert primes_below(1) == []
    assert primes_below(2) == []
    assert primes_below(-5) == []


def test_limit_is_exclusive():
    assert primes_below(3) == [2]
    assert primes_below(7) == [2, 3, 5]
    assert primes_below(8) == [2, 3, 5, 7]


def test_small_primes():
    assert primes_below(30) == [2, 3, 5, 7, 11, 13, 17, 19, 23, 29]


def test_skips_squares_of_primes():
    primes = primes_below(130)
    assert 25 not in primes
    assert 49 not in primes
    assert 121 not in primes
    assert primes[-1] == 127
    assert len(primes) == 31


@pytest.mark.nz_workload
def test_workload_primes_below_seven_thousand():
    primes = primes_below(7000)
    assert len(primes) == 900
    assert primes[:5] == [2, 3, 5, 7, 11]
    assert primes[-3:] == [6983, 6991, 6997]
