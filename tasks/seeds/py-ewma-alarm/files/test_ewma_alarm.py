"""Acceptance checks for ewma_alarm.py. Run: python3 test_ewma_alarm.py"""
import math

from ewma_alarm import AlarmMonitor


def test_ewma_math():
    m = AlarmMonitor(alpha=0.5, high=1000, low=-1000)
    assert m.value is None
    assert m.samples == 0
    m.observe(100)
    assert m.value == 100.0          # first sample seeds the average
    m.observe(20)
    assert m.value == 60.0
    m.observe(20)
    assert m.value == 40.0
    assert m.samples == 3


def test_raise_and_clear_with_hysteresis():
    m = AlarmMonitor(alpha=0.5, high=70, low=40)
    assert m.observe(100) == "alarm"          # ewma 100 >= high
    assert m.observe(20) == "alarm"           # ewma 60: in the band, holds
    assert m.observe(20) == "ok"              # ewma 40 <= low, clears
    assert m.observe(100) == "alarm"          # ewma 70 >= high again
    assert m.events == [(0, "raised"), (2, "cleared"), (3, "raised")]


def test_band_holds_ok_state_too():
    m = AlarmMonitor(alpha=1.0, high=70, low=40)
    assert m.observe(60) == "ok"              # between thresholds from ok
    assert m.observe(69.9) == "ok"
    assert m.observe(70) == "alarm"           # boundary is inclusive
    assert m.observe(40.1) == "alarm"
    assert m.observe(40) == "ok"              # low boundary inclusive too
    assert m.events == [(2, "raised"), (4, "cleared")]


def test_warmup_suppresses_transitions():
    m = AlarmMonitor(alpha=1.0, high=10, low=5, warmup=3)
    assert m.observe(50) == "ok"
    assert m.observe(50) == "ok"
    assert m.observe(50) == "ok"
    assert m.value == 50.0                    # ewma updated the whole time
    assert m.events == []
    assert m.observe(50) == "alarm"           # observation index 3
    assert m.events == [(3, "raised")]


def test_reset_wipes_state_not_config():
    m = AlarmMonitor(alpha=1.0, high=10, low=5, warmup=1)
    m.observe(50)
    m.observe(50)
    assert m.state == "alarm"
    m.reset()
    assert m.state == "ok"
    assert m.value is None
    assert m.samples == 0
    assert m.events == []
    assert m.observe(99) == "ok"              # warmup applies afresh
    assert m.observe(99) == "alarm"


def test_bad_samples_rejected_without_side_effects():
    m = AlarmMonitor(alpha=0.5, high=70, low=40)
    m.observe(50)
    for bad in [float("nan"), float("inf"), float("-inf"), None, "95", [1]]:
        try:
            m.observe(bad)
            assert False, "observe(%r) accepted" % (bad,)
        except ValueError:
            pass
    assert m.value == 50.0, "a rejected sample leaked into the average"
    assert m.samples == 1
    assert m.state == "ok"
    assert m.events == []


def test_constructor_validation():
    bad = [
        {"alpha": 0, "high": 10, "low": 5},
        {"alpha": 1.5, "high": 10, "low": 5},
        {"alpha": -0.2, "high": 10, "low": 5},
        {"alpha": 0.5, "high": 5, "low": 5},          # low must be < high
        {"alpha": 0.5, "high": 5, "low": 9},
        {"alpha": 0.5, "high": 10, "low": 5, "warmup": -1},
        {"alpha": 0.5, "high": 10, "low": 5, "warmup": 2.5},
    ]
    for kwargs in bad:
        try:
            AlarmMonitor(**kwargs)
            assert False, "accepted %r" % (kwargs,)
        except ValueError:
            pass
    AlarmMonitor(alpha=1, high=10, low=5, warmup=0)   # boundaries are legal


def test_long_flapping_sequence():
    m = AlarmMonitor(alpha=1.0, high=70, low=40)
    states = [m.observe(v) for v in [80, 50, 30, 50, 80, 30, 80]]
    assert states == ["alarm", "alarm", "ok", "ok", "alarm", "ok", "alarm"]
    assert m.events == [(0, "raised"), (2, "cleared"), (4, "raised"),
                        (5, "cleared"), (6, "raised")]
    assert math.isclose(m.value, 80.0)


CHECKS = [
    test_ewma_math,
    test_raise_and_clear_with_hysteresis,
    test_band_holds_ok_state_too,
    test_warmup_suppresses_transitions,
    test_reset_wipes_state_not_config,
    test_bad_samples_rejected_without_side_effects,
    test_constructor_validation,
    test_long_flapping_sequence,
]


def main():
    failures = 0
    checks = CHECKS
    for t in checks:
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
    print("\nall %d checks passed" % len(checks))


if __name__ == "__main__":
    main()
