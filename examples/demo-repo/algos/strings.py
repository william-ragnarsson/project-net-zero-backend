"""String distances."""


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
        for j in range(1, cols):
            cost = 0 if source[i - 1] == target[j - 1] else 1
            table[i][j] = min(
                [table[i - 1][j] + 1, table[i][j - 1] + 1, table[i - 1][j - 1] + cost]
            )
    return table[rows - 1][cols - 1]
