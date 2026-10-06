"""Prime numbers."""


def primes_below(limit: int) -> list[int]:
    """Return every prime number smaller than ``limit``, in increasing order.

    A number is prime when no smaller prime divides it, so each candidate is
    tested against the primes found so far.
    """
    primes = []
    for n in range(2, limit):
        if all(n % p for p in primes):
            primes.append(n)
    return primes
