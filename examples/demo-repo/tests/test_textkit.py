from textkit.dedupe import dedupe_preserve_order
from textkit.fields import join_fields
from textkit.freq import word_frequencies


def test_dedupe_keeps_first_spelling():
    assert dedupe_preserve_order(["Tea", "tea", "milk"], ignore_case=True) == ["Tea", "milk"]


def test_word_frequencies_ranks_by_count():
    assert word_frequencies("the cat and the hat.") == {"the": 2, "and": 1, "cat": 1, "hat": 1}


def test_join_fields_quotes_separators():
    assert join_fields(["a", "b,c", 'say "hi"']) == 'a,"b,c","say ""hi"""'
