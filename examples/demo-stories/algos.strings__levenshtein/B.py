def levenshtein(source: str, target: str) -> int:
    """Return the edit distance between ``source`` and ``target``.

    The distance is the smallest number of single-character insertions,
    deletions and substitutions that turn ``source`` into ``target``.
    """
    rows = len(source) + 1
    cols = len(target) + 1
    table = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        table[i][0] = i
    for j in range(cols):
        table[0][j] = j
    for i in range(1, rows):
        above = table[i - 1]
        row = table[i]
        s_char = source[i - 1]
        for j in range(1, cols):
            best = above[j - 1] + (s_char != target[j - 1])
            if above[j] + 1 < best:
                best = above[j] + 1
            if row[j - 1] + 1 < best:
                best = row[j - 1] + 1
            row[j] = best
    return table[rows - 1][cols - 1]
