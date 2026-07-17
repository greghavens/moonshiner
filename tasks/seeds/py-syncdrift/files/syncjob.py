"""Nightly incremental sync of the supplier catalog into our product store.

The supplier exposes a /changes feed: every product carries an updated_at
stamp, and `GET /changes?since=<stamp>` returns the products changed after
that point, paged with an opaque `next` token. We keep a small state dict
between runs (persisted by the caller) so each night only pulls what moved.
"""
import json
import urllib.parse
import urllib.request


class CatalogSync:
    """One supplier feed -> our local store, incrementally.

    store  -- dict of product id -> record, shared with the pricing code
    state  -- dict persisted between runs (cursor, bookkeeping counters)
    clock  -- injectable time source (callable returning a float)
    """

    def __init__(self, base_url, store, state, clock, page_size=2):
        self._base = base_url.rstrip("/")
        self._store = store
        self._state = state
        self._clock = clock
        self._page_size = page_size

    def _fetch(self, params):
        url = self._base + "/changes?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def run(self):
        """Pull one night's worth of changes. Returns records upserted."""
        since = self._state.get("cursor", 0.0)
        params = {"since": since, "limit": self._page_size}
        upserted = 0
        while True:
            page = self._fetch(params)
            for rec in page["records"]:
                self._store[rec["id"]] = rec
                upserted += 1
            token = page.get("next")
            if not token:
                break
            params = {"since": since, "limit": self._page_size, "token": token}
        finished = self._clock()
        self._state["cursor"] = finished
        self._state["last_run_at"] = finished
        self._state["runs"] = self._state.get("runs", 0) + 1
        return upserted
