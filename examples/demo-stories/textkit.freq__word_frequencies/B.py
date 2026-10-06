def word_frequencies(text: str, *, min_length: int = 1) -> dict[str, int]:
    """Count how often each word occurs in ``text``.

    Words are split on whitespace, stripped of surrounding punctuation and
    lower-cased; words shorter than ``min_length`` are ignored. The result is
    ordered by count, highest first, with ties in alphabetical order.
    """
    punctuation = string.punctuation
    counts = {}
    for raw in text.split():
        word = raw.strip(punctuation).lower()
        if word and len(word) >= min_length:
            counts[word] = counts.get(word, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return dict(ranked)
