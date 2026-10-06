@functools.lru_cache(maxsize=None)
def primes_below(limit: int) -> list[int]:
    """Return every prime number smaller than ``limit``, in increasing order."""
    if limit < 3:
        return []
    sieve = [True] * limit
    sieve[0] = sieve[1] = False
    for n in range(2, math.isqrt(limit - 1) + 1):
        if sieve[n]:
            for multiple in range(n * n, limit, n):
                sieve[multiple] = False
    return [n for n, prime in enumerate(sieve) if prime]
