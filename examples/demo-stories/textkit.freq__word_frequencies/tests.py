import pytest
from textkit.freq import word_frequencies


def test_counts_words():
    assert word_frequencies("the cat and the hat") == {"the": 2, "cat": 1, "and": 1, "hat": 1}


def test_strips_punctuation_and_case():
    assert word_frequencies("Hello, world! HELLO.") == {"hello": 2, "world": 1}


def test_empty_text():
    assert word_frequencies("") == {}
    assert word_frequencies("... -- !!") == {}


def test_ties_are_alphabetical():
    result = word_frequencies("pear apple pear apple fig")
    assert list(result.items()) == [("apple", 2), ("pear", 2), ("fig", 1)]


def test_hyphenated_words_stay_whole():
    assert list(word_frequencies("state-of-the-art design").items()) == [
        ("design", 1),
        ("state-of-the-art", 1),
    ]


def test_min_length_drops_short_words():
    result = word_frequencies("a an the a cat a", min_length=2)
    assert list(result.items()) == [("an", 1), ("cat", 1), ("the", 1)]


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
    assert list(result.items())[:3] == [("term1", 11), ("term100", 11), ("term121", 11)]
    assert list(result.items())[-1] == ("term0", 6)
