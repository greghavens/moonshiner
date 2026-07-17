"""Behavior checks for the metrics registry. Run: python3 test_stats_registry.py"""
import threading

from stats_registry import StatsRegistry


def test_single_thread_basics():
    reg = StatsRegistry()
    reg.record("db.query_ms", 12)
    reg.record("db.query_ms", 30)
    reg.record("cache.hit_ms", 1)
    assert reg.samples("db.query_ms") == [12, 30]
    assert reg.samples("unknown.metric") == []
    assert reg.names() == ["cache.hit_ms", "db.query_ms"]
    s = reg.summary("db.query_ms")
    assert s == {"count": 2, "min": 12, "max": 30, "mean": 21.0}, s
    assert reg.summary("unknown.metric")["count"] == 0


def test_first_samples_from_two_workers():
    # Two request handlers report the very first samples for a metric that
    # was just deployed. Bucket creation is deliberately slowed down (as a
    # real deque/mmap-backed factory would be) so both workers are inside
    # record() for a brand-new name at the same time.
    barrier = threading.Barrier(2)

    def slow_factory():
        try:
            barrier.wait(timeout=2.0)
        except threading.BrokenBarrierError:
            pass
        return []

    reg = StatsRegistry(bucket_factory=slow_factory)
    t1 = threading.Thread(target=reg.record, args=("api.latency_ms", 7))
    t2 = threading.Thread(target=reg.record, args=("api.latency_ms", 9))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)
    assert not t1.is_alive() and not t2.is_alive(), "record() never returned"

    got = sorted(reg.samples("api.latency_ms"))
    assert got == [7, 9], f"expected both first samples to survive, got {got}"
    assert reg.summary("api.latency_ms")["count"] == 2


def main():
    test_single_thread_basics()
    test_first_samples_from_two_workers()
    print("all checks passed")


if __name__ == "__main__":
    main()
