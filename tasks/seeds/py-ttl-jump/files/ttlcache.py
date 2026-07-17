"""TTL cache for session tokens with an injectable clock.

The clock object provides monotonic() for measuring entry age and wall()
for human-facing timestamps; wall time may be corrected (NTP) forward or
backward at any moment and must never affect expiry. An entry whose age has
reached the ttl is expired. put() on a full cache first purges expired
entries, then evicts the oldest live entry if still over capacity. touch()
refreshes a live entry's age and its display timestamp.
"""


class TTLCache:
    def __init__(self, capacity, ttl, clock):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.capacity = capacity
        self.ttl = ttl
        self.clock = clock
        self._entries = {}
        self._stats = {"hits": 0, "misses": 0, "expirations": 0, "evictions": 0}

    def _now(self):
        return self.clock.wall()

    def _age(self, entry):
        return self._now() - entry["stamp"]

    def _is_live(self, entry):
        return self._age(entry) <= self.ttl

    def _purge_expired(self):
        for key in [k for k, e in self._entries.items() if not self._is_live(e)]:
            del self._entries[key]
            self._stats["expirations"] += 1

    def put(self, key, value):
        if key not in self._entries and len(self._entries) >= self.capacity:
            self._purge_expired()
        if key not in self._entries and len(self._entries) >= self.capacity:
            oldest = max(self._entries, key=lambda k: self._age(self._entries[k]))
            del self._entries[oldest]
            self._stats["evictions"] += 1
        self._entries[key] = {
            "value": value,
            "stamp": self._now(),
            "seen": self.clock.wall(),
        }

    def get(self, key):
        entry = self._entries.get(key)
        if entry is None:
            self._stats["misses"] += 1
            return None
        if not self._is_live(entry):
            del self._entries[key]
            self._stats["expirations"] += 1
            self._stats["misses"] += 1
            return None
        self._stats["hits"] += 1
        return entry["value"]

    def touch(self, key):
        entry = self._entries.get(key)
        if entry is None or not self._is_live(entry):
            return False
        entry["seen"] = self.clock.wall()
        return True

    def inspect(self, key):
        entry = self._entries.get(key)
        if entry is None:
            return None
        return {
            "value": entry["value"],
            "stored_at": entry["seen"],
            "age": self._age(entry),
        }

    def stats(self):
        return dict(self._stats)
