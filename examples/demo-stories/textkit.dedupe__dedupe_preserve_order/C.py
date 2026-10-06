def dedupe_preserve_order(words: list[str], *, ignore_case: bool = False) -> list[str]:
    """Return ``words`` without repeats, keeping each word's first occurrence.

    With ``ignore_case`` two words that differ only in case count as the same
    word, and the spelling seen first is the one kept. The input list is not
    modified.
    """
    seen = []
    result = []
    remember = seen.append
    keep = result.append
    for word in words:
        key = word.lower() if ignore_case else word
        if key not in seen:
            remember(key)
            keep(word)
    return result
