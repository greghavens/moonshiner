"""Acceptance tests for the streaming stats accumulator. Run: python3 test_streamstats.py"""
import math


def approx(a, b, eps=1e-9):
    return abs(a - b) <= eps * max(1.0, abs(a), abs(b))


def expect_value_error(fn, what):
    try:
        fn()
        assert False, f"{what} should raise ValueError"
    except ValueError:
        pass


def main():
    from streamstats import RunningStats

    # -- empty accumulator --
    s = RunningStats()
    assert s.count == 0
    assert s.min is None and s.max is None
    expect_value_error(lambda: s.mean, "mean of no data")
    expect_value_error(lambda: s.variance, "variance of no data")
    expect_value_error(lambda: s.std, "std of no data")

    # -- one observation: mean yes, spread no --
    s = RunningStats()
    s.add(42.5)
    assert s.count == 1
    assert s.mean == 42.5
    assert s.min == 42.5 and s.max == 42.5
    expect_value_error(lambda: s.variance, "sample variance of one point")

    # -- add returns self so calls chain --
    s = RunningStats()
    assert s.add(1).add(2).add(3) is s
    assert s.count == 3
    assert approx(s.mean, 2.0)

    # -- textbook dataset: sample variance, n-1 denominator --
    s = RunningStats()
    for x in (2, 4, 4, 4, 5, 5, 7, 9):
        s.add(x)
    assert s.count == 8
    assert approx(s.mean, 5.0)
    assert approx(s.variance, 32.0 / 7.0), s.variance
    assert approx(s.std, math.sqrt(32.0 / 7.0))
    assert s.min == 2 and s.max == 9

    # -- negatives and floats --
    s = RunningStats()
    for x in (-1.5, 0.0, 1.5):
        s.add(x)
    assert approx(s.mean, 0.0)
    assert approx(s.variance, 2.25), s.variance
    assert s.min == -1.5 and s.max == 1.5

    # -- the reason this is Welford: huge common offset, tiny spread --
    s = RunningStats()
    for x in (4, 7, 13, 16):
        s.add(1e9 + x)
    assert approx(s.mean, 1e9 + 10.0)
    assert abs(s.variance - 30.0) < 1e-6, \
        f"catastrophic cancellation: got {s.variance}, want 30.0"

    # -- long stream cross-checked against an exact two-pass computation --
    data = [math.sin(i) * 100 + (i % 7) for i in range(10_000)]
    s = RunningStats()
    for x in data:
        s.add(x)
    exact_mean = math.fsum(data) / len(data)
    exact_var = math.fsum((x - exact_mean) ** 2 for x in data) / (len(data) - 1)
    assert approx(s.mean, exact_mean, 1e-12), (s.mean, exact_mean)
    assert approx(s.variance, exact_var, 1e-9), (s.variance, exact_var)
    assert s.count == 10_000

    # -- merge: sharded accumulation must equal single-stream accumulation --
    shard_a, shard_b = data[:137], data[137:]
    a = RunningStats()
    for x in shard_a:
        a.add(x)
    b = RunningStats()
    for x in shard_b:
        b.add(x)
    merged = a.merge(b)
    assert merged.count == s.count
    assert approx(merged.mean, s.mean, 1e-12)
    assert approx(merged.variance, s.variance, 1e-9), (merged.variance, s.variance)
    assert merged.min == s.min and merged.max == s.max

    # -- merge returns a NEW accumulator and mutates neither input --
    assert merged is not a and merged is not b
    assert a.count == 137 and b.count == len(data) - 137
    before_mean = a.mean
    a.merge(b)
    assert a.mean == before_mean and a.count == 137

    # -- merging with an empty accumulator is the identity, both ways --
    empty = RunningStats()
    left = empty.merge(s)
    right = s.merge(empty)
    for view in (left, right):
        assert view.count == s.count
        assert approx(view.mean, s.mean, 1e-12)
        assert approx(view.variance, s.variance, 1e-12)
        assert view.min == s.min and view.max == s.max
    both = RunningStats().merge(RunningStats())
    assert both.count == 0 and both.min is None

    # -- merge of two singletons has a defined sample variance --
    one = RunningStats().add(10.0)
    two = RunningStats().add(20.0)
    m = one.merge(two)
    assert m.count == 2
    assert approx(m.mean, 15.0)
    assert approx(m.variance, 50.0), m.variance

    print("all streamstats checks passed")


if __name__ == "__main__":
    main()
