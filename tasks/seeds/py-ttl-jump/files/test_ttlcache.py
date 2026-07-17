"""Behavior contract for the session-token TTL cache."""

from ttlcache import TTLCache


class FakeClock:
    def __init__(self, mono=100.0, wall=1_000_000.0):
        self.mono = mono
        self.wall_time = wall

    def monotonic(self):
        return self.mono

    def wall(self):
        return self.wall_time

    def tick(self, seconds):
        """Ordinary passage of time: both clocks advance."""
        self.mono += seconds
        self.wall_time += seconds

    def jump_wall(self, seconds):
        """NTP-style correction: only the wall clock moves."""
        self.wall_time += seconds


def test_hit_then_expiry_at_exact_boundary():
    clock = FakeClock()
    cache = TTLCache(capacity=4, ttl=10, clock=clock)
    cache.put("tok", "alpha")
    clock.tick(9)
    assert cache.get("tok") == "alpha"
    clock.tick(1)
    assert cache.get("tok") is None, "an entry is expired once its age reaches ttl"


def test_backward_wall_jump_does_not_revive_entries():
    clock = FakeClock()
    cache = TTLCache(capacity=4, ttl=10, clock=clock)
    cache.put("tok", "alpha")
    clock.jump_wall(-3600)
    clock.tick(11)
    assert cache.get("tok") is None, "entry older than ttl must expire despite wall correction"


def test_forward_wall_jump_does_not_expire_live_entries():
    clock = FakeClock()
    cache = TTLCache(capacity=4, ttl=10, clock=clock)
    cache.put("tok", "alpha")
    clock.jump_wall(3600)
    clock.tick(1)
    assert cache.get("tok") == "alpha", "a 1-second-old entry is live despite wall correction"


def test_touch_refreshes_age():
    clock = FakeClock()
    cache = TTLCache(capacity=4, ttl=10, clock=clock)
    cache.put("tok", "alpha")
    clock.tick(8)
    assert cache.touch("tok") is True
    clock.tick(8)
    assert cache.get("tok") == "alpha", "refreshed 8 seconds ago, ttl 10: must be live"


def test_touch_missing_or_expired_returns_false():
    clock = FakeClock()
    cache = TTLCache(capacity=4, ttl=10, clock=clock)
    assert cache.touch("ghost") is False
    cache.put("tok", "alpha")
    clock.tick(10)
    assert cache.touch("tok") is False, "an entry at exactly ttl age cannot be refreshed"


def test_display_timestamp_and_monotonic_age_are_separate():
    clock = FakeClock(wall=1_000_000.0)
    cache = TTLCache(capacity=4, ttl=100, clock=clock)
    cache.put("tok", "alpha")
    clock.tick(4)
    clock.jump_wall(-500)
    info = cache.inspect("tok")
    assert info["value"] == "alpha"
    assert info["stored_at"] == 1_000_000.0, "stored_at is the wall time at store"
    assert info["age"] == 4, "age is monotonic and immune to wall corrections"


def test_eviction_order_survives_wall_jump():
    clock = FakeClock()
    cache = TTLCache(capacity=2, ttl=100, clock=clock)
    cache.put("a", 1)
    clock.tick(1)
    clock.jump_wall(-50)
    cache.put("b", 2)
    clock.tick(1)
    cache.put("c", 3)
    assert cache.get("a") is None, "the oldest live entry (by real age) is evicted"
    assert cache.get("b") == 2
    assert cache.get("c") == 3
    assert cache.stats()["evictions"] == 1


def test_expired_entries_purged_before_evicting():
    clock = FakeClock()
    cache = TTLCache(capacity=2, ttl=10, clock=clock)
    cache.put("a", 1)
    clock.tick(11)
    cache.put("b", 2)
    cache.put("c", 3)
    assert cache.get("b") == 2
    assert cache.get("c") == 3
    stats = cache.stats()
    assert stats["evictions"] == 0, "expired entries make room; no live entry is evicted"
    assert stats["expirations"] == 1


def test_overwrite_resets_age_without_eviction():
    clock = FakeClock()
    cache = TTLCache(capacity=1, ttl=10, clock=clock)
    cache.put("a", 1)
    clock.tick(6)
    cache.put("a", 2)
    clock.tick(6)
    assert cache.get("a") == 2
    assert cache.stats()["evictions"] == 0


def test_stats_exact_and_copied():
    clock = FakeClock()
    cache = TTLCache(capacity=2, ttl=10, clock=clock)
    assert cache.get("x") is None
    cache.put("a", 1)
    assert cache.get("a") == 1
    clock.tick(10)
    assert cache.get("a") is None
    cache.put("b", 2)
    clock.tick(1)
    cache.put("c", 3)
    clock.tick(1)
    cache.put("d", 4)
    assert cache.get("c") == 3
    assert cache.get("d") == 4
    assert cache.get("b") is None
    expected = {"hits": 3, "misses": 3, "expirations": 1, "evictions": 1}
    assert cache.stats() == expected, cache.stats()
    leaked = cache.stats()
    leaked["hits"] = 99
    assert cache.stats() == expected, "stats() must return a copy"


def main():
    tests = [fn for name, fn in sorted(list(globals().items())) if name.startswith("test_")]
    for fn in tests:
        fn()
        print(f"ok {fn.__name__}")
    print(f"{len(tests)} checks passed")


if __name__ == "__main__":
    main()
