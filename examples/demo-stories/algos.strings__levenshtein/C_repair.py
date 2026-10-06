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
            best = diag if s_char == t_char else diag + 1
            if up + 1 < best:
                best = up + 1
            if left + 1 < best:
                best = left + 1
            left = best
            current.append(best)
        previous = current
    return previous[-1]
