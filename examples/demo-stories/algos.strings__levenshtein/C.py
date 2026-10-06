def levenshtein(source: str, target: str) -> int:
    """Return the edit distance between ``source`` and ``target``.

    The distance is the smallest number of single-character insertions,
    deletions and substitutions that turn ``source`` into ``target``.
    """
    previous = list(range(len(target) + 1))
    for i, s_char in enumerate(source, 1):
        current = [i]
        left = i
        for t_char, diag, up in zip(target, previous, previous[1:], strict=False):
            left = min(left + 1, up + 1, diag + (s_char != t_char)
            current.append(left)
        previous = current
    return previous[-1]
