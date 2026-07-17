"""Acceptance tests for the token-bucket rate limiter. Run: python3 test_ratelimit.py"""


class FakeClock:
    def __init__(self, start=0.0):
        self.now = float(start)

    def __call__(self):
        return self.now

    def advance(self, dt):
        self.now += dt


def approx(a, b, eps=1e-9):
    return abs(a - b) < eps


def main():
    from ratelimit import TokenBucket, KeyedLimiter

    # -- a fresh bucket is full --
    clock = FakeClock()
    b = TokenBucket(capacity=5, refill_rate=1.0, clock=clock)
    assert b.try_acquire(5) is True
    assert b.try_acquire(1) is False

    # -- continuous refill at refill_rate tokens per second --
    clock.advance(2.0)
    assert b.try_acquire(2) is True
    assert b.try_acquire(1) is False

    # -- fractional accumulation --
    clock = FakeClock()
    b = TokenBucket(capacity=2, refill_rate=0.5, clock=clock)
    assert b.try_acquire(2)
    clock.advance(1.0)              # 0.5 tokens banked
    assert b.try_acquire(1) is False
    clock.advance(1.0)              # 1.0 token banked
    assert b.try_acquire(1) is True

    # -- refill never exceeds capacity --
    clock.advance(10_000)
    assert approx(b.available, 2.0), b.available
    assert b.try_acquire(2) is True
    assert b.try_acquire(1) is False

    # -- a failed acquire consumes nothing --
    clock = FakeClock()
    b = TokenBucket(capacity=3, refill_rate=1.0, clock=clock)
    assert b.try_acquire(2)
    assert b.try_acquire(2) is False
    assert b.try_acquire(1) is True

    # -- available reports without consuming --
    clock = FakeClock()
    b = TokenBucket(capacity=4, refill_rate=2.0, clock=clock)
    assert approx(b.available, 4.0)
    assert approx(b.available, 4.0)
    assert b.try_acquire(3)
    assert approx(b.available, 1.0)
    clock.advance(0.5)
    assert approx(b.available, 2.0)

    # -- wait_time: seconds until n tokens exist; never consumes --
    clock = FakeClock()
    b = TokenBucket(capacity=4, refill_rate=2.0, clock=clock)
    assert approx(b.wait_time(1), 0.0)
    assert b.try_acquire(4)
    assert approx(b.wait_time(1), 0.5), b.wait_time(1)
    assert approx(b.wait_time(4), 2.0), b.wait_time(4)
    clock.advance(0.25)
    assert approx(b.wait_time(1), 0.25), b.wait_time(1)
    assert approx(b.available, 0.5)      # wait_time must not have spent anything
    clock.advance(0.25)
    assert b.try_acquire(1) is True

    # -- a clock that jumps backwards must not mint or destroy tokens --
    clock = FakeClock(start=10.0)
    b = TokenBucket(capacity=3, refill_rate=1.0, clock=clock)
    assert b.try_acquire(3)
    clock.now = 5.0                      # regression (ntp step, VM resume...)
    assert approx(b.available, 0.0), b.available
    assert b.try_acquire(1) is False
    clock.now = 10.0                     # back to where we were: still nothing earned
    assert approx(b.available, 0.0), b.available
    clock.now = 12.0                     # 2 real seconds past the high-water mark
    assert approx(b.available, 2.0), b.available

    # -- argument validation --
    clock = FakeClock()
    b = TokenBucket(capacity=2, refill_rate=1.0, clock=clock)
    for bad in (0, -1):
        for method in (b.try_acquire, b.wait_time):
            try:
                method(bad)
                assert False, f"{method.__name__}({bad}) should raise ValueError"
            except ValueError:
                pass
    for method in (b.try_acquire, b.wait_time):
        try:
            method(3)                    # more than capacity: can never succeed
            assert False, "requesting beyond capacity should raise ValueError"
        except ValueError:
            pass
    for cap, rate in ((0, 1.0), (-1, 1.0), (2, 0.0), (2, -0.5)):
        try:
            TokenBucket(capacity=cap, refill_rate=rate, clock=clock)
            assert False, f"TokenBucket({cap}, {rate}) should raise ValueError"
        except ValueError:
            pass

    # -- default acquire size is one token --
    clock = FakeClock()
    b = TokenBucket(capacity=1, refill_rate=1.0, clock=clock)
    assert b.try_acquire() is True
    assert b.try_acquire() is False

    # -- keyed limiter: one lazy bucket per key, same parameters --
    clock = FakeClock()
    lim = KeyedLimiter(capacity=2, refill_rate=1.0, clock=clock)
    assert lim.allow("alice") is True
    assert lim.allow("alice") is True
    assert lim.allow("alice") is False
    assert lim.allow("bob") is True      # bob has his own bucket
    clock.advance(1.0)
    assert lim.allow("alice") is True
    assert lim.allow("alice") is False
    assert lim.allow("bob", 2) is True   # bob spent 1 of 2, then earned 1 back
    assert lim.allow("bob") is False

    print("all ratelimit checks passed")


if __name__ == "__main__":
    main()
