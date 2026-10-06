"""Word counts for short documents."""

import string


def word_frequencies(text: str, *, min_length: int = 1) -> dict[str, int]:
    """Count how often each word occurs in ``text``.

    Words are split on whitespace, stripped of surrounding punctuation and
    lower-cased; words shorter than ``min_length`` are ignored. The result is
    ordered by count, highest first, with ties in alphabetical order.
    """
    words = []
    for raw in text.split():
        word = raw.strip(string.punctuation).lower()
        if word and len(word) >= min_length:
            words.append(word)
    counts = {}
    for word in words:
        if word not in counts:
            counts[word] = words.count(word)
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return dict(ranked)
