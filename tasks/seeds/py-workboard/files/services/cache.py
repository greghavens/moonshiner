"""Tiny keyed cache for computed views.

Dashboard summaries hit every project row plus its items; recomputing them
on every page load made the ops dashboard noticeably slow once boards grew
past a few hundred items, hence this cache. Write paths are responsible
for invalidating the keys they touch.
"""


class Cache:
    def __init__(self):
        self._entries = {}
        self.hits = 0
        self.misses = 0

    def get_or(self, key, build):
        if key in self._entries:
            self.hits += 1
        else:
            self.misses += 1
            self._entries[key] = build()
        return self._entries[key]

    def invalidate(self, key):
        self._entries.pop(key, None)

    def clear(self):
        self._entries.clear()

    def __len__(self):
        return len(self._entries)
