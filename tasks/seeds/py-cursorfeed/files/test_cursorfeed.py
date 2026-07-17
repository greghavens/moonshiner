"""Acceptance tests for the server-cursor lifecycle client.

The server exposes a cursor API with three endpoints:
  POST /cursors                 → 201 { "cursor_id": "...", "key": null }
  GET  /cursors/<id>?limit=N   → 200 { "rows": [...], "next_key": "..." | null }
                                  or 410 (cursor expired)
  DELETE /cursors/<id>          → 204

Rows: list of {"key": <str>, "value": <any>}.
The client must:
  - Open a cursor, iterate pages, handle 410 mid-iteration by reopening and
    seeking past the last delivered key (open cursor with seek_key= param,
    so the server skips already-delivered rows), at-least-once with client
    dedupe on the key field, and close the cursor when done.
  - Explicit cursor states: OPEN, EXPIRED, CLOSED.

Run: python3 test_cursorfeed.py
"""
import http.server
import json
import threading


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status, body=None, content_type="application/json"):
        raw = b"" if body is None else json.dumps(body).encode("utf-8")
        self.send_response(status)
        if raw:
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
        else:
            self.send_header("Content-Length", "0")
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        api = self.server.api
        with api.lock:
            api.requests.append(("POST", self.path, None))
            spec = api.open_script.pop(0) if api.open_script else {"cursor_id": "cx-0", "key": None}
        self._send(201, spec)

    def do_GET(self):
        import urllib.parse
        api = self.server.api
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        with api.lock:
            api.requests.append(("GET", parsed.path, qs))
            if api.page_script:
                spec = api.page_script.pop(0)
            else:
                spec = {"_status": 599}
        status = spec.pop("_status", 200)
        if status == 410:
            self._send(410, {"error": "cursor_expired"})
        else:
            self._send(200, spec)

    def do_DELETE(self):
        api = self.server.api
        with api.lock:
            api.requests.append(("DELETE", self.path, None))
            status = api.delete_script.pop(0) if api.delete_script else 204
        self._send(status)


class MockCursorServer:
    def __init__(self):
        self.lock = threading.Lock()
        self.open_script = []    # dicts returned for POST /cursors
        self.page_script = []    # dicts returned for GET /cursors/<id>; use {"_status": 410} for expiry
        self.delete_script = []  # ints returned for DELETE /cursors/<id>
        self.requests = []       # recorded (method, path, qs) tuples
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.daemon_threads = True
        self.server.api = self
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self):
        host, port = self.server.server_address[:2]
        return "http://%s:%d" % (host, port)

    def recorded(self):
        with self.lock:
            return list(self.requests)

    def closes(self):
        return [r for r in self.recorded() if r[0] == "DELETE"]

    def opens(self):
        return [r for r in self.recorded() if r[0] == "POST"]

    def pages(self):
        return [r for r in self.recorded() if r[0] == "GET"]

    def close(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()


def row(key, value="x"):
    return {"key": key, "value": value}


def page(rows, next_key=None):
    return {"rows": rows, "next_key": next_key}


def make(mod, srv, page_size=10):
    return mod.CursorFeed(srv.base_url, page_size=page_size)


def test_initial_state_is_closed_no_traffic(mod):
    srv = MockCursorServer()
    try:
        feed = make(mod, srv)
        assert feed.state is mod.CursorState.CLOSED, feed.state
        assert srv.recorded() == []
    finally:
        srv.close()


def test_open_returns_all_rows_from_single_page(mod):
    srv = MockCursorServer()
    try:
        srv.open_script[:] = [{"cursor_id": "c1", "key": None}]
        srv.page_script[:] = [page([row("k1"), row("k2"), row("k3")])]
        srv.delete_script[:] = [204]
        feed = make(mod, srv)
        result = list(feed.iterate())
        assert [r["key"] for r in result] == ["k1", "k2", "k3"], result
        assert feed.state is mod.CursorState.CLOSED
        assert len(srv.opens()) == 1
        assert len(srv.pages()) == 1
        assert len(srv.closes()) == 1
    finally:
        srv.close()


def test_multi_page_iteration_follows_next_key(mod):
    srv = MockCursorServer()
    try:
        srv.open_script[:] = [{"cursor_id": "c1", "key": None}]
        srv.page_script[:] = [
            page([row("k1"), row("k2")], next_key="k2"),
            page([row("k3"), row("k4")], next_key="k4"),
            page([row("k5")]),
        ]
        srv.delete_script[:] = [204]
        feed = make(mod, srv, page_size=2)
        result = list(feed.iterate())
        assert [r["key"] for r in result] == ["k1", "k2", "k3", "k4", "k5"], result
        assert feed.state is mod.CursorState.CLOSED
        # Verify limit param is sent
        gets = srv.pages()
        for g in gets:
            qs = g[2]
            assert qs.get("limit") == ["2"], "limit must be sent as a query param: %r" % qs
    finally:
        srv.close()


def test_state_is_open_during_iteration(mod):
    srv = MockCursorServer()
    try:
        srv.open_script[:] = [{"cursor_id": "c1", "key": None}]
        srv.page_script[:] = [
            page([row("k1")], next_key="k1"),
            page([row("k2")]),
        ]
        srv.delete_script[:] = [204]
        feed = make(mod, srv)
        states_seen = []
        for _ in feed.iterate():
            states_seen.append(feed.state)
        assert all(s is mod.CursorState.OPEN for s in states_seen), states_seen
        assert feed.state is mod.CursorState.CLOSED
    finally:
        srv.close()


def test_cursor_expiry_mid_iteration_triggers_reopen_and_seek(mod):
    srv = MockCursorServer()
    try:
        # First cursor delivers k1,k2 then expires; second cursor starts after k2
        srv.open_script[:] = [
            {"cursor_id": "c1", "key": None},     # first open
            {"cursor_id": "c2", "key": None},     # reopen after expiry
        ]
        srv.page_script[:] = [
            page([row("k1"), row("k2")], next_key="k2"),  # c1 page 1
            {"_status": 410},                              # c1 expired
            page([row("k3"), row("k4")]),                  # c2 page (started after k2)
        ]
        srv.delete_script[:] = [204, 204]
        feed = make(mod, srv, page_size=2)
        result = list(feed.iterate())
        assert [r["key"] for r in result] == ["k1", "k2", "k3", "k4"], result
        assert len(srv.opens()) == 2, "must reopen once after expiry"
        # The first GET on c2 must include seek_after=k2 so the server resumes past k2
        c2_pages = [g for g in srv.pages() if "/c2" in g[1]]
        assert c2_pages, "expected at least one page fetch on cursor c2"
        first_c2_get = c2_pages[0]
        qs = first_c2_get[2]
        assert qs.get("seek_after") == ["k2"], (
            "reopen must seek past the last delivered key; got %r" % qs
        )
    finally:
        srv.close()


def test_reopen_with_seek_deduplicates_overlapping_rows(mod):
    srv = MockCursorServer()
    try:
        # Server might re-deliver k2 at the boundary even after seek — client must dedupe
        srv.open_script[:] = [
            {"cursor_id": "c1", "key": None},
            {"cursor_id": "c2", "key": None},
        ]
        srv.page_script[:] = [
            page([row("k1"), row("k2")], next_key="k2"),
            {"_status": 410},
            page([row("k2"), row("k3"), row("k4")]),  # k2 is a repeat (boundary overlap)
        ]
        srv.delete_script[:] = [204, 204]
        feed = make(mod, srv, page_size=2)
        result = list(feed.iterate())
        assert [r["key"] for r in result] == ["k1", "k2", "k3", "k4"], (
            "boundary overlap must be deduped; got %r" % [r["key"] for r in result]
        )
    finally:
        srv.close()


def test_close_sends_delete_and_transitions_to_closed(mod):
    srv = MockCursorServer()
    try:
        srv.open_script[:] = [{"cursor_id": "c1", "key": None}]
        srv.delete_script[:] = [204]
        feed = make(mod, srv)
        feed.open()
        assert feed.state is mod.CursorState.OPEN
        feed.close()
        assert feed.state is mod.CursorState.CLOSED
        assert len(srv.closes()) == 1
        assert srv.closes()[0][1] == "/cursors/c1"
    finally:
        srv.close()


def test_close_while_already_closed_is_a_noop(mod):
    srv = MockCursorServer()
    try:
        feed = make(mod, srv)
        feed.close()  # no cursor open yet
        assert srv.recorded() == [], "close when CLOSED must not touch the server"
    finally:
        srv.close()


def test_double_expiry_raises_cursor_error(mod):
    srv = MockCursorServer()
    try:
        srv.open_script[:] = [
            {"cursor_id": "c1", "key": None},
            {"cursor_id": "c2", "key": None},
        ]
        srv.page_script[:] = [
            page([row("k1")], next_key="k1"),
            {"_status": 410},  # first expiry → reopen
            page([row("k2")], next_key="k2"),
            {"_status": 410},  # second expiry on the resumed cursor
        ]
        srv.delete_script[:] = [204, 204]
        feed = make(mod, srv)
        try:
            list(feed.iterate())
        except mod.CursorError:
            pass
        else:
            raise AssertionError("two consecutive expiries must raise CursorError")
    finally:
        srv.close()


def test_iterate_closes_cursor_on_completion(mod):
    srv = MockCursorServer()
    try:
        srv.open_script[:] = [{"cursor_id": "c1", "key": None}]
        srv.page_script[:] = [page([row("k1")])]
        srv.delete_script[:] = [204]
        feed = make(mod, srv)
        list(feed.iterate())
        assert len(srv.closes()) == 1, "iterate must close the cursor after exhausting rows"
        assert feed.state is mod.CursorState.CLOSED
    finally:
        srv.close()


def test_page_request_includes_cursor_id_in_path(mod):
    srv = MockCursorServer()
    try:
        srv.open_script[:] = [{"cursor_id": "my-cursor-77", "key": None}]
        srv.page_script[:] = [page([row("k1")])]
        srv.delete_script[:] = [204]
        feed = make(mod, srv)
        list(feed.iterate())
        gets = srv.pages()
        assert all("/my-cursor-77" in g[1] for g in gets), (
            "page request path must include cursor id: %r" % gets
        )
    finally:
        srv.close()


def main():
    import cursorfeed as mod

    test_initial_state_is_closed_no_traffic(mod)
    test_open_returns_all_rows_from_single_page(mod)
    test_multi_page_iteration_follows_next_key(mod)
    test_state_is_open_during_iteration(mod)
    test_cursor_expiry_mid_iteration_triggers_reopen_and_seek(mod)
    test_reopen_with_seek_deduplicates_overlapping_rows(mod)
    test_close_sends_delete_and_transitions_to_closed(mod)
    test_close_while_already_closed_is_a_noop(mod)
    test_double_expiry_raises_cursor_error(mod)
    test_iterate_closes_cursor_on_completion(mod)
    test_page_request_includes_cursor_id_in_path(mod)
    print("ok")


if __name__ == "__main__":
    main()
