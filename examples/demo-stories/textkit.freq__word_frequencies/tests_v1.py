import pytest
from textkit.freq import word_frequencies


def test_counts_words():
    assert word_frequencies("the cat and the hat") == {"the": 2, "cat": 1, "and": 1, "hat": 1}


def test_strips_punctuation_and_case():
    assert word_frequencies("Hello, world! HELLO.") == {"hello": 2, "world": 1}


def test_empty_text():
    assert word_frequencies("") == {}


def test_ties_keep_first_appearance():
    assert list(word_frequencies("pear apple pear apple fig")) == ["pear", "apple", "fig"]


def test_splits_hyphenated_words():
    assert word_frequencies("state-of-the-art design") == {
        "state": 1,
        "of": 1,
        "the": 1,
        "art": 1,
        "design": 1,
    }


@pytest.mark.nz_workload
def test_workload_long_document():
    words = []
    for i in range(4000):
        word = f"Term{(i * i) % 797}"
        if i % 17 == 0:
            word = word.upper() + "."
        elif i % 10 == 0:
            word += ","
        words.append(word)
    result = word_frequencies(" ".join(words))
    assert len(result) == 399
    assert sum(result.values()) == 4000
