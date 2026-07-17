"""Acceptance tests for the session-cookie portal client.

Every scenario runs against a scripted mock of the maintenance portal bound
to 127.0.0.1 on an ephemeral port: a session endpoint (POST /session to log
in, DELETE /session to log out) and resource endpoints (GET /api/...).
Responses are consumed strictly in script order, every request is recorded
(method, path, Cookie header, parsed JSON body), and all timekeeping goes
through an injectable clock — nothing here sleeps or reads the wall clock.

Run: python3 test_sessionclient.py
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

    def _record(self, method):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            parsed = {"_undecodable": raw.decode("utf-8", "replace")}
        portal = self.server.portal
        with portal.lock:
            portal.requests.append({
                "method": method,
                "path": self.path,
                "cookie": self.headers.get("Cookie"),
                "content_type": self.headers.get("Content-Type"),
                "json": parsed,
            })

    def _send(self, spec):
        body = json.dumps(spec.get("body", {})).encode("utf-8")
        self.send_response(spec.get("status", 200))
        self.send_header("Content-Type", "application/json")
        if spec.get("set_cookie"):
            self.send_header("Set-Cookie", spec["set_cookie"])
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _pop(self, script, label):
        portal = self.server.portal
        with portal.lock:
            if script:
                return script.pop(0)
        return {"status": 599, "body": {"error": "%s script exhausted" % label}}

    def do_POST(self):
        self._record("POST")
        if self.path != "/session":
            self._send({"status": 404, "body": {"error": "not_found"}})
            return
        self._send(self._pop(self.server.portal.login_script, "login"))

    def do_DELETE(self):
        self._record("DELETE")
        if self.path != "/session":
            self._send({"status": 404, "body": {"error": "not_found"}})
            return
        self._send(self._pop(self.server.portal.logout_script, "logout"))

    def do_GET(self):
        self._record("GET")
        self._send(self._pop(self.server.portal.resource_script, "resource"))


class MockPortal:
    """A scripted maintenance portal on a local ephemeral port."""

    def __init__(self):
        self.lock = threading.Lock()
        self.login_script = []
        self.resource_script = []
        self.logout_script = []
        self.requests = []
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.daemon_threads = True
        self.server.portal = self
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self):
        host, port = self.server.server_address[:2]
        return "http://%s:%d" % (host, port)

    def recorded(self):
        with self.lock:
            return [dict(r) for r in self.requests]

    def logins(self):
        return [r for r in self.recorded() if r["method"] == "POST" and r["path"] == "/session"]

    def gets(self):
        return [r for r in self.recorded() if r["method"] == "GET"]

    def close(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()


def login_ok(value="s-1", expires_in=600):
    body = {} if expires_in is None else {"expires_in": expires_in}
    return {
        "status": 200,
        "set_cookie": "portal_sid=%s; Path=/; HttpOnly" % value,
        "body": body,
    }


def make(mod, portal, clock=None, user="fleet-batch", pw="wrench-42"):
    return mod.SessionClient(portal.base_url, user, pw, clock=clock or FakeClock())


def test_initial_state_is_anonymous_with_no_traffic(mod):
    portal = MockPortal()
    try:
        client = make(mod, portal)
        assert client.state is mod.ClientState.ANONYMOUS, client.state
        assert portal.recorded() == [], portal.recorded()
    finally:
        portal.close()


def test_explicit_login_posts_credentials_and_stores_cookie(mod):
    portal = MockPortal()
    try:
        portal.login_script[:] = [login_ok("s-1")]
        portal.resource_script[:] = [{"status": 200, "body": {"vehicles": 3}}]
        client = make(mod, portal)
        client.login()
        assert client.state is mod.ClientState.AUTHENTICATED, client.state
        assert client.get_json("/api/vehicles") == {"vehicles": 3}
        reqs = portal.recorded()
        assert reqs[0]["method"] == "POST" and reqs[0]["path"] == "/session", reqs[0]
        assert reqs[0]["json"] == {"username": "fleet-batch", "password": "wrench-42"}, reqs[0]["json"]
        assert (reqs[0]["content_type"] or "").startswith("application/json"), reqs[0]["content_type"]
        assert reqs[1]["method"] == "GET" and reqs[1]["path"] == "/api/vehicles", reqs[1]
        assert reqs[1]["cookie"] == "portal_sid=s-1", reqs[1]["cookie"]
    finally:
        portal.close()


def test_first_call_logs_in_lazily(mod):
    portal = MockPortal()
    try:
        portal.login_script[:] = [login_ok("s-1")]
        portal.resource_script[:] = [{"status": 200, "body": {"ok": True}}]
        client = make(mod, portal)
        assert client.get_json("/api/work-orders") == {"ok": True}
        order = [(r["method"], r["path"]) for r in portal.recorded()]
        assert order == [("POST", "/session"), ("GET", "/api/work-orders")], order
        assert client.state is mod.ClientState.AUTHENTICATED
    finally:
        portal.close()


def test_session_is_reused_across_calls(mod):
    portal = MockPortal()
    try:
        portal.login_script[:] = [login_ok("s-1")]
        portal.resource_script[:] = [
            {"status": 200, "body": {"n": 1}},
            {"status": 200, "body": {"n": 2}},
        ]
        client = make(mod, portal)
        assert client.get_json("/api/a") == {"n": 1}
        assert client.get_json("/api/b") == {"n": 2}
        assert len(portal.logins()) == 1, "one login must serve consecutive calls"
        assert [r["cookie"] for r in portal.gets()] == ["portal_sid=s-1", "portal_sid=s-1"]
    finally:
        portal.close()


def test_state_reports_expired_at_the_deadline_boundary(mod):
    portal = MockPortal()
    try:
        portal.login_script[:] = [login_ok("s-1", expires_in=600)]
        clock = FakeClock(1000.0)
        client = make(mod, portal, clock=clock)
        client.login()
        clock.set(1599.999)
        assert client.state is mod.ClientState.AUTHENTICATED, "still inside the lifetime"
        clock.set(1600.0)
        assert client.state is mod.ClientState.EXPIRED, "the deadline instant itself expires"
    finally:
        portal.close()


def test_expired_session_relogs_in_before_the_request(mod):
    portal = MockPortal()
    try:
        portal.login_script[:] = [login_ok("s-1", expires_in=600), login_ok("s-2", expires_in=600)]
        portal.resource_script[:] = [
            {"status": 200, "body": {"n": 1}},
            {"status": 200, "body": {"n": 2}},
        ]
        clock = FakeClock(1000.0)
        client = make(mod, portal, clock=clock)
        assert client.get_json("/api/a") == {"n": 1}
        clock.advance(600)
        assert client.get_json("/api/b") == {"n": 2}
        gets = portal.gets()
        assert [r["cookie"] for r in gets] == ["portal_sid=s-1", "portal_sid=s-2"], (
            "a stale cookie must never reach a resource endpoint: %r" % [r["cookie"] for r in gets]
        )
        assert len(portal.logins()) == 2
        assert client.state is mod.ClientState.AUTHENTICATED
    finally:
        portal.close()


def test_login_without_expires_in_never_proactively_expires(mod):
    portal = MockPortal()
    try:
        portal.login_script[:] = [login_ok("s-1", expires_in=None)]
        portal.resource_script[:] = [{"status": 200, "body": {"ok": True}}]
        clock = FakeClock(1000.0)
        client = make(mod, portal, clock=clock)
        client.login()
        clock.advance(10 ** 9)
        assert client.state is mod.ClientState.AUTHENTICATED
        assert client.get_json("/api/a") == {"ok": True}
        assert len(portal.logins()) == 1, "no deadline means no proactive re-login"
    finally:
        portal.close()


def test_401_triggers_one_relogin_and_one_replay(mod):
    portal = MockPortal()
    try:
        portal.login_script[:] = [login_ok("s-1"), login_ok("s-2")]
        portal.resource_script[:] = [
            {"status": 401, "body": {"error": "session_revoked"}},
            {"status": 200, "body": {"report": "all good"}},
        ]
        client = make(mod, portal)
        assert client.get_json("/api/report") == {"report": "all good"}
        gets = portal.gets()
        assert [(r["path"], r["cookie"]) for r in gets] == [
            ("/api/report", "portal_sid=s-1"),
            ("/api/report", "portal_sid=s-2"),
        ], gets
        assert len(portal.logins()) == 2
        assert client.state is mod.ClientState.AUTHENTICATED
    finally:
        portal.close()


def test_second_401_raises_auth_error_and_resets_to_anonymous(mod):
    portal = MockPortal()
    try:
        portal.login_script[:] = [login_ok("s-1"), login_ok("s-2"), login_ok("s-3")]
        portal.resource_script[:] = [
            {"status": 401, "body": {"error": "session_revoked"}},
            {"status": 401, "body": {"error": "account_review"}},
            {"status": 200, "body": {"ok": True}},
        ]
        client = make(mod, portal)
        try:
            client.get_json("/api/report")
        except mod.AuthError as e:
            assert getattr(e, "status", None) == 401, getattr(e, "status", None)
        else:
            raise AssertionError("a second 401 must raise AuthError")
        assert len(portal.gets()) == 2, "exactly one replay, never a third attempt"
        assert client.state is mod.ClientState.ANONYMOUS, client.state
        # the failure dropped the session for real: the next call starts fresh
        assert client.get_json("/api/report") == {"ok": True}
        assert len(portal.logins()) == 3
        assert portal.gets()[-1]["cookie"] == "portal_sid=s-3"
    finally:
        portal.close()


def test_rejected_login_is_typed_and_not_sticky(mod):
    portal = MockPortal()
    try:
        portal.login_script[:] = [
            {"status": 401, "body": {"error": "bad_credentials"}},
            login_ok("s-1"),
        ]
        client = make(mod, portal)
        try:
            client.login()
        except mod.LoginError as e:
            assert "bad_credentials" in str(e), str(e)
        else:
            raise AssertionError("a rejected login must raise LoginError")
        assert client.state is mod.ClientState.ANONYMOUS
        client.login()
        assert client.state is mod.ClientState.AUTHENTICATED
    finally:
        portal.close()


def test_login_response_without_set_cookie_is_a_login_error(mod):
    portal = MockPortal()
    try:
        portal.login_script[:] = [{"status": 200, "body": {"expires_in": 600}}]
        client = make(mod, portal)
        try:
            client.login()
        except mod.LoginError:
            pass
        else:
            raise AssertionError("a 200 login without Set-Cookie must raise LoginError")
        assert client.state is mod.ClientState.ANONYMOUS
    finally:
        portal.close()


def test_logout_deletes_the_session_and_clears_the_jar(mod):
    portal = MockPortal()
    try:
        portal.login_script[:] = [login_ok("s-1"), login_ok("s-2")]
        portal.resource_script[:] = [{"status": 200, "body": {"ok": True}}]
        client = make(mod, portal)
        client.login()
        client.logout()
        assert client.state is mod.ClientState.ANONYMOUS
        deletes = [r for r in portal.recorded() if r["method"] == "DELETE"]
        assert [(r["path"], r["cookie"]) for r in deletes] == [("/session", "portal_sid=s-1")], deletes
        # the jar is empty: the next call logs in from scratch with the new id
        assert client.get_json("/api/a") == {"ok": True}
        assert portal.gets()[-1]["cookie"] == "portal_sid=s-2"
        assert len(portal.logins()) == 2
    finally:
        portal.close()


def test_logout_while_anonymous_is_a_local_noop(mod):
    portal = MockPortal()
    try:
        client = make(mod, portal)
        client.logout()
        assert client.state is mod.ClientState.ANONYMOUS
        assert portal.recorded() == [], "an anonymous logout must not touch the portal"
    finally:
        portal.close()


def test_resource_set_cookie_rotates_the_jar(mod):
    portal = MockPortal()
    try:
        portal.login_script[:] = [login_ok("s-1", expires_in=None)]
        portal.resource_script[:] = [
            {"status": 200, "body": {"n": 1}, "set_cookie": "portal_sid=s-9; Path=/; HttpOnly"},
            {"status": 200, "body": {"n": 2}},
        ]
        client = make(mod, portal)
        assert client.get_json("/api/a") == {"n": 1}
        assert client.get_json("/api/b") == {"n": 2}
        assert [r["cookie"] for r in portal.gets()] == ["portal_sid=s-1", "portal_sid=s-9"], (
            "a rotated session id must be used from the next request on"
        )
        assert len(portal.logins()) == 1, "rotation is not a re-login"
    finally:
        portal.close()


def test_non_401_errors_are_typed_and_do_not_relogin(mod):
    portal = MockPortal()
    try:
        portal.login_script[:] = [login_ok("s-1")]
        portal.resource_script[:] = [
            {"status": 503, "body": {"error": "maintenance window"}},
            {"status": 200, "body": {"ok": True}},
        ]
        client = make(mod, portal)
        try:
            client.get_json("/api/a")
        except mod.ApiError as e:
            assert getattr(e, "status", None) == 503, getattr(e, "status", None)
            assert not isinstance(e, mod.AuthError)
        else:
            raise AssertionError("a 503 must raise ApiError")
        assert len(portal.gets()) == 1, "non-401 failures are not retried here"
        assert client.state is mod.ClientState.AUTHENTICATED, "a 503 must not end the session"
        assert client.get_json("/api/a") == {"ok": True}
        assert len(portal.logins()) == 1
    finally:
        portal.close()


def test_error_hierarchy(mod):
    assert issubclass(mod.AuthError, mod.ApiError), "AuthError must be catchable as ApiError"
    assert not issubclass(mod.LoginError, mod.ApiError), "LoginError means config, not traffic"
    states = {s.name for s in mod.ClientState}
    assert states == {"ANONYMOUS", "AUTHENTICATED", "EXPIRED"}, states


def main():
    import sessionclient as mod

    test_initial_state_is_anonymous_with_no_traffic(mod)
    test_explicit_login_posts_credentials_and_stores_cookie(mod)
    test_first_call_logs_in_lazily(mod)
    test_session_is_reused_across_calls(mod)
    test_state_reports_expired_at_the_deadline_boundary(mod)
    test_expired_session_relogs_in_before_the_request(mod)
    test_login_without_expires_in_never_proactively_expires(mod)
    test_401_triggers_one_relogin_and_one_replay(mod)
    test_second_401_raises_auth_error_and_resets_to_anonymous(mod)
    test_rejected_login_is_typed_and_not_sticky(mod)
    test_login_response_without_set_cookie_is_a_login_error(mod)
    test_logout_deletes_the_session_and_clears_the_jar(mod)
    test_logout_while_anonymous_is_a_local_noop(mod)
    test_resource_set_cookie_rotates_the_jar(mod)
    test_non_401_errors_are_typed_and_do_not_relogin(mod)
    test_error_hierarchy(mod)
    print("ok")


if __name__ == "__main__":
    main()
