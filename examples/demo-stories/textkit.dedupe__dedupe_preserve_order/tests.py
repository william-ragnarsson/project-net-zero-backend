import pytest
from textkit.dedupe import dedupe_preserve_order


def test_empty_list():
    assert dedupe_preserve_order([]) == []


def test_keeps_first_occurrence_in_order():
    words = ["b", "a", "b", "c", "a", "d"]
    assert dedupe_preserve_order(words) == ["b", "a", "c", "d"]


def test_does_not_modify_input():
    words = ["x", "y", "x"]
    dedupe_preserve_order(words)
    assert words == ["x", "y", "x"]


def test_case_sensitive_by_default():
    assert dedupe_preserve_order(["Apple", "apple", "APPLE"]) == ["Apple", "apple", "APPLE"]


def test_ignore_case_keeps_first_spelling():
    words = ["Apple", "banana", "apple", "BANANA", "cherry"]
    assert dedupe_preserve_order(words, ignore_case=True) == ["Apple", "banana", "cherry"]


@pytest.mark.nz_workload
def test_workload_many_repeats():
    words = [("Word" if i % 3 == 0 else "word") + str((i * 37) % 900) for i in range(3000)]
    result = dedupe_preserve_order(words, ignore_case=True)
    assert len(result) == 900
    assert result[:4] == ["Word0", "word37", "word74", "Word111"]
