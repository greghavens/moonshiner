"""Acceptance tests for the client-credentials token manager.

Every scenario runs against a scripted mock of the platform API bound to
127.0.0.1 on an ephemeral port: a token endpoint (POST /oauth/token) and a
resource endpoint (GET /api/...). Responses are consumed strictly in script
order and every request is recorded, so each assertion below is exact.

Run: python3 test_tokenflow.py
"""
import http.server
import json
import threading
import time


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = float(start)

    def __call__(self):
        return self.now

    def set(self, t):
        self.now = float(t)

    def advance(self, seconds):
        self.now += seconds


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep test output clean
        pass

    def _send(self, spec):
        body = json.dumps(spec.get("body", {})).encode("utf-8")
        self.send_response(spec.get("status", 200))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        api = self.server.api
        if self.path != "/oauth/token":
            self._send({"status": 404, "body": {"error": "not_found"}})
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            parsed = {"_undecodable": raw.decode("utf-8", "replace")}
        with api.lock:
            api.token_requests.append({
                "json": parsed,
                "content_type": self.headers.get("Content-Type"),
            })
            if api.token_script:
                spec = api.token_script.pop(0)
            else:
                spec = {"status": 599, "body": {"error": "token script exhausted"}}
        if spec.get("gate"):
            # Rendezvous for the concurrency scenario: tell the test the
            # fetch is in flight, then hold the response until released.
            api.token_arrived.set()
            api.token_release.wait(timeout=10)
        self._send(spec)

    def do_GET(self):
        api = self.server.api
        with api.lock:
            api.resource_requests.append((self.path, self.headers.get("Authorization")))
            if api.resource_script:
                spec = api.resource_script.pop(0)
            else:
                spec = {"status": 599, "body": {"error": "resource script exhausted"}}
        self._send(spec)


class MockApi:
    """A scripted platform API on a local ephemeral port."""

    def __init__(self):
        self.lock = threading.Lock()
        self.token_script = []
        self.resource_script = []
        self.token_requests = []
        self.resource_requests = []
        self.token_arrived = threading.Event()
        self.token_release = threading.Event()
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.daemon_threads = True
        self.server.api = self
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self):
        host, port = self.server.server_address[:2]
        return "http://%s:%d" % (host, port)

    @property
    def token_url(self):
        return self.base_url + "/oauth/token"

    def token_count(self):
        with self.lock:
            return len(self.token_requests)

    def close(self):
        self.token_release.set()  # never leave a gated handler hanging
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()


def tok(token, expires_in=3600):
    return {"status": 200, "body": {"access_token": token, "expires_in": expires_in}}


def test_fetch_once_and_cache(mod):
    api = MockApi()
    try:
        api.token_script[:] = [tok("tok-1", 3600)]
        mgr = mod.TokenManager(api.token_url, "svc-reports", "s3cret-1", clock=FakeClock())
        assert mgr.get_token() == "tok-1"
        assert mgr.get_token() == "tok-1"
        assert mgr.get_token() == "tok-1"
        assert api.token_count() == 1, api.token_requests
        req = api.token_requests[0]
        assert req["json"] == {
            "grant_type": "client_credentials",
            "client_id": "svc-reports",
            "client_secret": "s3cret-1",
        }, req["json"]
        assert (req["content_type"] or "").startswith("application/json"), req["content_type"]
    finally:
        api.close()


def test_refresh_at_default_skew_boundary(mod):
    api = MockApi()
    try:
        api.token_script[:] = [tok("tok-1", 100), tok("tok-2", 100)]
        clock = FakeClock(1000.0)
        mgr = mod.TokenManager(api.token_url, "svc", "sec", clock=clock)
        assert mgr.get_token() == "tok-1"
        # default skew is 30s: a 100s token fetched at t=1000 refreshes at t=1070
        clock.set(1069.999)
        assert mgr.get_token() == "tok-1", "still inside the skew window"
        assert api.token_count() == 1
        clock.set(1070.0)
        assert mgr.get_token() == "tok-2", "the boundary instant itself must refresh"
        assert api.token_count() == 2
    finally:
        api.close()


def test_zero_skew_uses_full_lifetime(mod):
    api = MockApi()
    try:
        api.token_script[:] = [tok("tok-1", 100), tok("tok-2", 100)]
        clock = FakeClock(0.0)
        mgr = mod.TokenManager(api.token_url, "svc", "sec", clock=clock, skew=0)
        assert mgr.get_token() == "tok-1"
        clock.set(99.5)
        assert mgr.get_token() == "tok-1"
        clock.set(100.0)
        assert mgr.get_token() == "tok-2"
        assert api.token_count() == 2
    finally:
        api.close()


def test_invalidate_is_compare_and_clear(mod):
    api = MockApi()
    try:
        api.token_script[:] = [tok("tok-1"), tok("tok-2")]
        mgr = mod.TokenManager(api.token_url, "svc", "sec", clock=FakeClock())
        assert mgr.get_token() == "tok-1"
        mgr.invalidate("tok-1")
        assert mgr.get_token() == "tok-2"
        assert api.token_count() == 2
        # a stale invalidate (an old token value) must NOT wipe the newer token
        mgr.invalidate("tok-1")
        assert mgr.get_token() == "tok-2", "stale invalidate must be a no-op"
        assert api.token_count() == 2, "stale invalidate must not trigger a fetch"
    finally:
        api.close()


def test_token_endpoint_errors_are_typed_and_not_sticky(mod):
    api = MockApi()
    try:
        api.token_script[:] = [
            {"status": 400, "body": {"error": "invalid_client"}},
            tok("tok-9"),
        ]
        mgr = mod.TokenManager(api.token_url, "svc", "wrong", clock=FakeClock())
        try:
            mgr.get_token()
        except mod.TokenError as e:
            assert "invalid_client" in str(e), str(e)
        else:
            raise AssertionError("a 400 from the token endpoint must raise TokenError")
        # the failure must not poison the manager: the next call fetches fine
        assert mgr.get_token() == "tok-9"
        assert api.token_count() == 2
    finally:
        api.close()


def test_token_response_without_access_token_is_an_error(mod):
    api = MockApi()
    try:
        api.token_script[:] = [{"status": 200, "body": {"token_type": "Bearer"}}]
        mgr = mod.TokenManager(api.token_url, "svc", "sec", clock=FakeClock())
        try:
            mgr.get_token()
        except mod.TokenError:
            pass
        else:
            raise AssertionError("a 200 without access_token must raise TokenError")
    finally:
        api.close()


def test_concurrent_callers_share_one_fetch(mod):
    api = MockApi()
    try:
        api.token_script[:] = [dict(tok("tok-1"), gate=True)]
        mgr = mod.TokenManager(api.token_url, "svc", "sec", clock=FakeClock())

        results = {}

        def worker(name):
            try:
                results[name] = mgr.get_token()
            except Exception as e:  # recorded, asserted below
                results[name] = e

        first = threading.Thread(target=worker, args=("t0",))
        first.start()
        assert api.token_arrived.wait(timeout=5), "first fetch never reached the token endpoint"

        rest = [threading.Thread(target=worker, args=("t%d" % i,)) for i in range(1, 5)]
        for t in rest:
            t.start()
        # Give unguarded implementations every chance to fire their own
        # token requests while the first fetch is still in flight.
        deadline = time.time() + 0.5
        while time.time() < deadline and api.token_count() == 1:
            time.sleep(0.01)
        assert api.token_count() == 1, (
            "%d token requests while one fetch was already in flight -- "
            "callers must wait for the in-flight fetch" % api.token_count()
        )

        api.token_release.set()
        for t in [first] + rest:
            t.join(timeout=10)
            assert not t.is_alive(), "a caller never returned"
        assert sorted(results) == ["t0", "t1", "t2", "t3", "t4"]
        for name, value in results.items():
            assert value == "tok-1", (name, value)
        assert api.token_count() == 1, "exactly one fetch must serve all five callers"
    finally:
        api.close()


def test_resource_call_retries_once_after_401_with_fresh_token(mod):
    api = MockApi()
    try:
        api.token_script[:] = [tok("tok-1"), tok("tok-2")]
        api.resource_script[:] = [
            {"status": 401, "body": {"error": "token_revoked"}},
            {"status": 200, "body": {"report": "all good"}},
            {"status": 200, "body": {"report": "second call"}},
        ]
        mgr = mod.TokenManager(api.token_url, "svc", "sec", clock=FakeClock())
        client = mod.ApiClient(api.base_url, mgr)
        assert client.get_json("/api/report") == {"report": "all good"}
        assert api.resource_requests == [
            ("/api/report", "Bearer tok-1"),
            ("/api/report", "Bearer tok-2"),
        ], api.resource_requests
        assert api.token_count() == 2
        # the fresh token is cached: the next call reuses tok-2, no new fetch
        assert client.get_json("/api/report") == {"report": "second call"}
        assert api.resource_requests[-1] == ("/api/report", "Bearer tok-2")
        assert api.token_count() == 2
    finally:
        api.close()


def test_second_401_raises_auth_error_without_hammering(mod):
    api = MockApi()
    try:
        api.token_script[:] = [tok("tok-1"), tok("tok-2")]
        api.resource_script[:] = [
            {"status": 401, "body": {"error": "token_revoked"}},
            {"status": 401, "body": {"error": "subject_disabled"}},
        ]
        mgr = mod.TokenManager(api.token_url, "svc", "sec", clock=FakeClock())
        client = mod.ApiClient(api.base_url, mgr)
        try:
            client.get_json("/api/report")
        except mod.AuthError as e:
            assert getattr(e, "status", None) == 401, getattr(e, "status", None)
        else:
            raise AssertionError("a second 401 must raise AuthError")
        assert len(api.resource_requests) == 2, "exactly one retry, never a third attempt"
        assert api.token_count() == 2
    finally:
        api.close()


def test_non_401_failures_do_not_retry(mod):
    api = MockApi()
    try:
        api.token_script[:] = [tok("tok-1")]
        api.resource_script[:] = [{"status": 503, "body": {"error": "maintenance"}}]
        mgr = mod.TokenManager(api.token_url, "svc", "sec", clock=FakeClock())
        client = mod.ApiClient(api.base_url, mgr)
        try:
            client.get_json("/api/report")
        except mod.ApiError as e:
            assert getattr(e, "status", None) == 503, getattr(e, "status", None)
        else:
            raise AssertionError("a 503 must raise ApiError")
        assert len(api.resource_requests) == 1, "non-401 failures are not retried here"
        assert api.token_count() == 1, "a 503 must not burn a token fetch"
    finally:
        api.close()


def test_error_hierarchy(mod):
    assert issubclass(mod.AuthError, mod.ApiError), "AuthError must be catchable as ApiError"
    assert not issubclass(mod.TokenError, mod.ApiError)


def main():
    import tokenflow as mod

    test_fetch_once_and_cache(mod)
    test_refresh_at_default_skew_boundary(mod)
    test_zero_skew_uses_full_lifetime(mod)
    test_invalidate_is_compare_and_clear(mod)
    test_token_endpoint_errors_are_typed_and_not_sticky(mod)
    test_token_response_without_access_token_is_an_error(mod)
    test_concurrent_callers_share_one_fetch(mod)
    test_resource_call_retries_once_after_401_with_fresh_token(mod)
    test_second_401_raises_auth_error_without_hammering(mod)
    test_non_401_failures_do_not_retry(mod)
    test_error_hierarchy(mod)
    print("ok")


if __name__ == "__main__":
    main()
