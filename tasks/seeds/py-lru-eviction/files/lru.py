class LRUCache:
    """Least-recently-used cache backed by an insertion-ordered dict."""

    def __init__(self, capacity):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._store = {}

    def get(self, key, default=None):
        if key not in self._store:
            return default
        return self._store[key]

    def put(self, key, value):
        if key in self._store:
            del self._store[key]
        self._store[key] = value
        if len(self._store) > self.capacity:
            self._store.popitem()

    def __len__(self):
        return len(self._store)
