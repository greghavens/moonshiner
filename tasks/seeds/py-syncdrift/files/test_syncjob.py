"""Tests for the nightly supplier-catalog sync.

The mock feed runs on 127.0.0.1 (ephemeral port) and behaves like the real
supplier API: `since` filters on updated_at, pages are served from a
consistent snapshot taken at the first page request (their pagination is
snapshot-stable), and the catalog can change between page fetches -- the
feed applies scripted catalog edits after serving each page, which is
exactly what supplier staff editing products during our sync window looks
like. Time is a logical clock injected into the sync job; nothing here
sleeps or reads the wall clock.

Run: python3 test_syncjob.py
"""
import http.server
import json
import threading
import urllib.parse

from syncjob import CatalogSync


class FakeClock:
    def __init__(self, start=0.0):
        self.now = float(start)

    def __call__(self):
        return self.now

    def set(self, t):
        self.now = float(t)


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        feed = self.server.feed
        parsed = urllib.parse.urlparse(self.path)
        params = dict(urllib.parse.parse_qsl(parsed.query))
        with feed.lock:
            feed.requests.append(params)
            if "token" in params:
                start = int(params["token"])
            else:
                since = float(params.get("since", 0.0))
                feed.snapshot = [
                    dict(r) for r in sorted(
                        feed.records.values(),
                        key=lambda r: (r["updated_at"], r["id"]),
                    ) if r["updated_at"] > since
                ]
                start = 0
            limit = int(params.get("limit", 2))
            chunk = feed.snapshot[start:start + limit]
            end = start + limit
            body = {
                "records": chunk,
                "next": str(end) if end < len(feed.snapshot) else None,
            }
            # A page just went out; apply the next scripted catalog edit,
            # the way supplier staff keep editing while we page through.
            if feed.scripted_edits:
                feed.apply(feed.scripted_edits.pop(0))
        payload = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class MockFeed:
    """The supplier's /changes feed with a scripted, mutable catalog."""

    def __init__(self, records, scripted_edits=()):
        self.lock = threading.Lock()
        self.records = {r["id"]: dict(r) for r in records}
        self.scripted_edits = list(scripted_edits)
        self.snapshot = []
        self.requests = []
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.daemon_threads = True
        self.server.feed = self
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self):
        host, port = self.server.server_address[:2]
        return "http://%s:%d" % (host, port)

    def apply(self, change):
        rec = self.records.setdefault(change["id"], {"id": change["id"]})
        rec.update(change)

    def edit(self, product_id, **fields):
        """A catalog edit made between runs (e.g. during the day)."""
        with self.lock:
            self.apply(dict(fields, id=product_id))

    def close(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()


CATALOG = [
    {"id": "p1", "name": "Anvil 5kg", "price_cents": 18900, "updated_at": 5},
    {"id": "p2", "name": "Hex bolts M8 (100)", "price_cents": 1250, "updated_at": 8},
    {"id": "p3", "name": "Steel plate 3mm", "price_cents": 3900, "updated_at": 11},
    {"id": "p4", "name": "Rivet gun", "price_cents": 7400, "updated_at": 14},
]


def new_sync(feed, clock, store=None, state=None):
    store = {} if store is None else store
    state = {} if state is None else state
    return CatalogSync(feed.base_url, store, state, clock), store, state


def test_first_run_imports_the_whole_catalog():
    feed = MockFeed(CATALOG)
    try:
        clock = FakeClock(20)
        sync, store, state = new_sync(feed, clock)
        assert sync.run() == 4
        assert sorted(store) == ["p1", "p2", "p3", "p4"], sorted(store)
        assert store["p4"]["name"] == "Rivet gun"
        assert state["runs"] == 1
        # wire shape: first page asks since=0, later pages echo the token
        assert float(feed.requests[0]["since"]) == 0.0, feed.requests[0]
        assert feed.requests[1]["token"] == "2", feed.requests[1]
    finally:
        feed.close()


def test_edit_made_between_runs_comes_across():
    feed = MockFeed(CATALOG)
    try:
        clock = FakeClock(20)
        sync, store, state = new_sync(feed, clock)
        assert sync.run() == 4
        # next day: someone fixes a price, well before the next window
        feed.edit("p1", name="Anvil 5kg (rustproof)", price_cents=19900, updated_at=25)
        clock.set(30)
        assert sync.run() == 1, "one changed record means one upsert"
        assert store["p1"]["name"] == "Anvil 5kg (rustproof)"
        assert store["p1"]["price_cents"] == 19900
        assert len(store) == 4, "an update must not duplicate records"
    finally:
        feed.close()


def test_edit_during_the_sync_window_lands_by_the_next_run():
    feed = MockFeed(CATALOG, scripted_edits=[
        # applied right after page 1 of the first run is served
        {"id": "p2", "name": "Hex bolts M8 (100) restocked", "updated_at": 16},
    ])
    try:
        clock = FakeClock(20)
        sync, store, state = new_sync(feed, clock)
        assert sync.run() == 4
        # The run pages a consistent snapshot, so tonight still holds the
        # pre-edit row -- that part is expected and fine.
        assert store["p2"]["name"] == "Hex bolts M8 (100)", store["p2"]
        clock.set(30)
        got = sync.run()
        assert got == 1, (
            "the record edited during last night's window must be delivered "
            "by the following run, got %r upserts" % got)
        assert store["p2"]["name"] == "Hex bolts M8 (100) restocked", store["p2"]
        clock.set(40)
        assert sync.run() == 0, "nothing changed since; the third run is a no-op"
        assert store["p2"]["name"] == "Hex bolts M8 (100) restocked"
    finally:
        feed.close()


def test_record_edited_twice_mid_run_arrives_with_its_final_content():
    feed = MockFeed(CATALOG, scripted_edits=[
        {"id": "p3", "name": "Steel plate 3mm (new mill)", "updated_at": 16},
        {"id": "p3", "price_cents": 4200, "updated_at": 18},
    ])
    try:
        clock = FakeClock(20)
        sync, store, state = new_sync(feed, clock)
        assert sync.run() == 4
        clock.set(30)
        got = sync.run()
        assert got == 1, (
            "the mid-window edits must reach us on the next run, got %r" % got)
        assert store["p3"]["name"] == "Steel plate 3mm (new mill)", store["p3"]
        assert store["p3"]["price_cents"] == 4200, store["p3"]
    finally:
        feed.close()


def test_quiet_nights_are_noops_and_bookkeeping_still_ticks():
    feed = MockFeed(CATALOG)
    try:
        clock = FakeClock(20)
        sync, store, state = new_sync(feed, clock)
        assert sync.run() == 4
        clock.set(30)
        assert sync.run() == 0
        clock.set(40)
        assert sync.run() == 0
        assert state["runs"] == 3
        assert state["last_run_at"] == 40.0, state
        assert sorted(store) == ["p1", "p2", "p3", "p4"]
    finally:
        feed.close()


def main():
    test_first_run_imports_the_whole_catalog()
    test_edit_made_between_runs_comes_across()
    test_edit_during_the_sync_window_lands_by_the_next_run()
    test_record_edited_twice_mid_run_arrives_with_its_final_content()
    test_quiet_nights_are_noops_and_bookkeeping_still_ticks()
    print("ok")


if __name__ == "__main__":
    main()
