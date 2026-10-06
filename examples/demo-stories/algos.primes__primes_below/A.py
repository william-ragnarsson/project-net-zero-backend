def primes_below(limit: int) -> list[int]:
    """Return every prime number smaller than ``limit``, in increasing order.

    Sieve of Eratosthenes: cross out the multiples of each prime up to the
    square root of ``limit``; what is left is prime.
    """
    if limit < 3:
        return []
    is_prime = bytearray([1]) * limit
    is_prime[0] = is_prime[1] = 0
    for n in range(2, int((limit - 1) ** 0.5) + 1):
        if is_prime[n]:
            is_prime[n * n :: n] = bytes(len(range(n * n, limit, n)))
    return [n for n in range(limit) if is_prime[n]]
