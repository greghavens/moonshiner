"""Acceptance checks for ratewindow.py. Run: python3 test_ratewindow.py"""
from ratewindow import SlidingWindow


def test_window_boundaries():
    sw = SlidingWindow(60)
    sw.hit("alice", 100)
    assert sw.count("alice", 100) == 1        # exactly-now is in
    assert sw.count("alice", 159) == 1
    assert sw.count("alice", 160) == 0        # exactly window-old is out
    assert sw.count("alice", 161) == 0


def test_hit_returns_running_total():
    sw = SlidingWindow(10)
    assert sw.hit("bob", 1) == 1
    assert sw.hit("bob", 2) == 2
    assert sw.hit("bob", 11) == 2             # the t=1 hit just aged out
    assert sw.hit("bob", 30) == 1


def test_keys_are_independent():
    sw = SlidingWindow(60)
    sw.hit("alice", 10)
    sw.hit("alice", 20)
    sw.hit("bob", 20)
    assert sw.count("alice", 20) == 2
    assert sw.count("bob", 20) == 1
    assert sw.count("carol", 20) == 0


def test_out_of_order_delivery():
    sw = SlidingWindow(60)
    sw.hit("alice", 100)
    sw.hit("alice", 70)                       # late arrival, still in window
    assert sw.count("alice", 100) == 2
    sw.hit("alice", 30)                       # late AND already stale at 100
    assert sw.count("alice", 100) == 2
    assert sw.count("alice", 80) == 2         # ...but visible as of t=80


def test_future_hits_wait_their_turn():
    sw = SlidingWindow(60)
    sw.hit("alice", 500)
    assert sw.count("alice", 100) == 0
    assert sw.count("alice", 500) == 1


def test_weighted_hits():
    sw = SlidingWindow(60)
    sw.hit("batchy", 10, count=5)
    assert sw.hit("batchy", 20, count=2) == 7
    assert sw.count("batchy", 75) == 2        # the 5-pack aged out at 70


def test_prune_reports_and_forgets():
    sw = SlidingWindow(60)
    sw.hit("alice", 10, count=2)
    sw.hit("alice", 20)
    sw.hit("bob", 100)
    assert sorted(sw.tracked()) == ["alice", "bob"]
    dropped = sw.prune(100)                   # cutoff 40: both alice hits go
    assert dropped == 3
    assert sw.tracked() == ["bob"]
    assert sw.count("alice", 100) == 0
    assert sw.prune(100) == 0                 # idempotent
    assert sw.prune(161) == 1                 # bob's t=100 is out at 161
    assert sw.tracked() == []


def test_prune_boundary_matches_count():
    sw = SlidingWindow(60)
    sw.hit("edge", 40)
    sw.hit("edge", 41)
    assert sw.prune(100) == 1                 # t=40 is exactly window-old
    assert sw.count("edge", 100) == 1


def test_top_ranking():
    sw = SlidingWindow(60)
    sw.hit("alice", 10, count=3)
    sw.hit("bob", 10, count=5)
    sw.hit("carol", 10, count=3)
    sw.hit("dave", 10)
    assert sw.top(3, 20) == [("bob", 5), ("alice", 3), ("carol", 3)]
    assert sw.top(10, 20) == [("bob", 5), ("alice", 3), ("carol", 3),
                              ("dave", 1)]
    sw.hit("dave", 100)
    assert sw.top(10, 100) == [("dave", 1)]   # everyone else aged out
    assert sw.top(10, 500) == []


def test_validation():
    for w in [0, -5, "60", None]:
        try:
            SlidingWindow(w)
            assert False, "accepted window %r" % (w,)
        except ValueError:
            pass
    sw = SlidingWindow(60)
    for key in ["", None, 7]:
        try:
            sw.hit(key, 10)
            assert False, "accepted key %r" % (key,)
        except ValueError:
            pass
    for c in [0, -1, 1.5, "2"]:
        try:
            sw.hit("alice", 10, count=c)
            assert False, "accepted count %r" % (c,)
        except ValueError:
            pass
    assert sw.tracked() == [], "rejected hits must not be recorded"


CHECKS = [
    test_window_boundaries,
    test_hit_returns_running_total,
    test_keys_are_independent,
    test_out_of_order_delivery,
    test_future_hits_wait_their_turn,
    test_weighted_hits,
    test_prune_reports_and_forgets,
    test_top_ranking,
    test_validation,
]


def main():
    failures = 0
    for t in CHECKS:
        try:
            t()
        except Exception as e:
            failures += 1
            print("FAIL %s: %s: %s" % (t.__name__, type(e).__name__, e))
        else:
            print("ok   %s" % t.__name__)
    if failures:
        print("\n%d check(s) failed" % failures)
        raise SystemExit(1)
    print("\nall %d checks passed" % len(CHECKS))


if __name__ == "__main__":
    main()
