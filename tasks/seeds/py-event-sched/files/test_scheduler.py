"""Acceptance tests for the logical-time event scheduler. Run: python3 test_scheduler.py"""


def main():
    from scheduler import Scheduler

    # -- events fire in time order, clock lands on the horizon --
    s = Scheduler()
    fired = []
    s.schedule(5, fired.append, "late")
    s.schedule(1, fired.append, "early")
    s.schedule(3, fired.append, "middle")
    assert s.now == 0
    s.run_until(10)
    assert fired == ["early", "middle", "late"], fired
    assert s.now == 10
    assert s.pending == 0

    # -- same timestamp: priority first (lower wins), then scheduling order --
    s = Scheduler()
    fired = []
    s.schedule(2, fired.append, "a")                 # default priority 0
    s.schedule(2, fired.append, "b")
    s.schedule(2, fired.append, "urgent", priority=-1)
    s.schedule(2, fired.append, "cleanup", priority=5)
    s.run_until(2)
    assert fired == ["urgent", "a", "b", "cleanup"], fired

    # -- priority never trumps time --
    s = Scheduler()
    fired = []
    s.schedule(1, fired.append, "sooner", priority=99)
    s.schedule(2, fired.append, "later", priority=-99)
    s.run_until(5)
    assert fired == ["sooner", "later"], fired

    # -- events beyond the horizon stay pending --
    s = Scheduler()
    fired = []
    s.schedule(3, fired.append, "in")
    s.schedule(7, fired.append, "out")
    s.run_until(5)
    assert fired == ["in"], fired
    assert s.pending == 1
    s.run_until(7)
    assert fired == ["in", "out"], fired

    # -- cancellation --
    s = Scheduler()
    fired = []
    keep = s.schedule(1, fired.append, "keep")
    drop = s.schedule(1, fired.append, "drop")
    assert s.pending == 2
    assert s.cancel(drop) is True
    assert s.pending == 1, "cancelled events must not count as pending"
    assert s.cancel(drop) is False, "second cancel reports False"
    s.run_until(2)
    assert fired == ["keep"], fired
    assert s.cancel(keep) is False, "cancelling a fired event reports False"

    # -- a callback may cancel a later event --
    s = Scheduler()
    fired = []
    victim = s.schedule(4, fired.append, "victim")
    s.schedule(2, lambda: s.cancel(victim))
    s.run_until(10)
    assert fired == [], fired

    # -- a callback may schedule more work; due-in-window work runs in-window --
    s = Scheduler()
    fired = []

    def chain():
        fired.append("first")
        s.schedule(2, fired.append, "chained")       # due at 3, inside horizon
        s.schedule(9, fired.append, "future")        # due at 10, outside

    s.schedule(1, chain)
    s.schedule(4, fired.append, "fourth")
    s.run_until(5)
    assert fired == ["first", "chained", "fourth"], fired
    assert s.pending == 1
    s.run_until(10)
    assert fired == ["first", "chained", "fourth", "future"], fired

    # -- zero-delay reschedule fires at the same timestamp, after peers --
    s = Scheduler()
    fired = []
    s.schedule(2, lambda: (fired.append("x"), s.schedule(0, fired.append, "x-again")))
    s.schedule(2, fired.append, "y")
    s.run_until(2)
    assert fired == ["x", "y", "x-again"], fired

    # -- periodic work via self-rescheduling --
    s = Scheduler()
    ticks = []

    def tick():
        ticks.append(s.now)
        s.schedule(2, tick)

    s.schedule(2, tick)
    s.run_until(7)
    assert ticks == [2, 4, 6], ticks
    assert s.pending == 1                            # the tick at 8 is armed

    # -- run_next: single step, returns the fire time, None when idle --
    s = Scheduler()
    fired = []
    s.schedule(3, fired.append, "only")
    s.schedule(8, fired.append, "next")
    t = s.run_next()
    assert t == 3 and s.now == 3 and fired == ["only"], (t, s.now, fired)
    t = s.run_next()
    assert t == 8 and s.now == 8, (t, s.now)
    assert s.run_next() is None
    assert s.now == 8, "an idle run_next must not move the clock"

    # -- time is monotonic; bad arguments rejected --
    s = Scheduler()
    s.run_until(5)
    try:
        s.run_until(4)
        assert False, "run_until into the past should raise ValueError"
    except ValueError:
        pass
    assert s.now == 5
    try:
        s.schedule(-1, lambda: None)
        assert False, "negative delay should raise ValueError"
    except ValueError:
        pass
    s.run_until(5)                                   # same-time horizon is allowed

    # -- callback args are forwarded --
    s = Scheduler()
    got = []
    s.schedule(1, lambda a, b: got.append((a, b)), "x", 42)
    s.run_until(1)
    assert got == [("x", 42)], got

    # -- a raising callback: clock is on the event, the rest survives --
    s = Scheduler()
    fired = []

    def boom():
        raise RuntimeError("worker exploded")

    s.schedule(2, boom)
    s.schedule(4, fired.append, "survivor")
    try:
        s.run_until(10)
        assert False, "callback exception must propagate"
    except RuntimeError:
        pass
    assert s.now == 2, s.now
    assert s.pending == 1
    s.run_until(10)
    assert fired == ["survivor"], fired
    assert s.now == 10

    print("all scheduler checks passed")


if __name__ == "__main__":
    main()
