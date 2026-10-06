def dedupe_preserve_order(words: list[str], *, ignore_case: bool = False) -> list[str]:
    """Return ``words`` without repeats, keeping each word's first occurrence.

    With ``ignore_case`` two words that differ only in case count as the same
    word, and the spelling seen first is the one kept. The input list is not
    modified.
    """
    if not ignore_case:
        return list(dict.fromkeys(words))
    first = {}
    for word in words:
        first.setdefault(word.lower(), word)
    return list(first.values())
