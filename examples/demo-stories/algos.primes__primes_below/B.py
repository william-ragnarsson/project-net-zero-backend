def primes_below(limit: int) -> list[int]:
    """Return every prime number smaller than ``limit``, in increasing order.

    A number is prime when no prime up to its square root divides it, so each
    candidate is tested only against those primes.
    """
    primes = []
    for n in range(2, limit):
        root = math.isqrt(n)
        for p in primes:
            if p > root:
                primes.append(n)
                break
            if n % p == 0:
                break
        else:
            primes.append(n)
    return primes
