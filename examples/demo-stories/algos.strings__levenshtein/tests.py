import pytest
from algos.strings import levenshtein


def test_identical_strings():
    assert levenshtein("carbon", "carbon") == 0


def test_empty_strings():
    assert levenshtein("", "") == 0
    assert levenshtein("", "abc") == 3
    assert levenshtein("abc", "") == 3


def test_classic_examples():
    assert levenshtein("kitten", "sitting") == 3
    assert levenshtein("flaw", "lawn") == 2
    assert levenshtein("ab", "ba") == 2


def test_symmetric():
    assert levenshtein("intention", "execution") == levenshtein("execution", "intention") == 5


def test_unicode_characters():
    assert levenshtein("café", "cafe") == 1
    assert levenshtein("naïve", "naive") == 1


@pytest.mark.nz_workload
def test_workload_long_sentences():
    first = ("kitten sitting on the warm mat " * 6)[:160]
    second = ("sitting kitten at the cold mall " * 6)[:150]
    assert levenshtein(first, second) == 76
    third = ("carbon-aware scheduling of batch jobs " * 4)[:140]
    fourth = ("carbon aware schedules for batch work " * 4)[:145]
    assert levenshtein(third, fourth) == 38
