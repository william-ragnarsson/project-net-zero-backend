def levenshtein(source: str, target: str) -> int:
    """Return the edit distance between ``source`` and ``target``.

    The distance is the smallest number of single-character insertions,
    deletions and substitutions that turn ``source`` into ``target``.
    """
    previous = list(range(len(target) + 1))
    for i, s_char in enumerate(source, 1):
        current = [i]
        left = i
        for j, t_char in enumerate(target):
            diag = previous[j] + (s_char != t_char)
            up = previous[j + 1] + 1
            left = left + 1
            if up < left:
                left = up
            if diag < left:
                left = diag
            current.append(left)
        previous = current
    return previous[-1]
