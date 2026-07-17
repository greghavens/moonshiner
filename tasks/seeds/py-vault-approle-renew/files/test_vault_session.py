"""Acceptance harness for the vaultsession package: a loopback fake Vault
server exercising the AppRole + token-lifecycle wire contract pinned in
docs/contract.json. No real Vault, no real credentials, no real sleeping.
Protected -- do not modify. Run: python3 test_vault_session.py
"""

import contextlib
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROLE_ID = "role-billing-rotator"
SECRET_ID = "sid-dummy-4f9a1c"  # dummy; must never appear in logs
TOKEN_A = "hvs.CAESIdummyA-9f31"  # dummy; must never appear in logs
TOKEN_B = "hvs.CAESIdummyB-77e2"
ACCESSOR_A = "acc-Zw1qmC0aFake"

T0 = 1_000_000.0


def login_ok(token=TOKEN_A, lease=1200, renewable=True, accessor=ACCESSOR_A):
    return (200, {
        "request_id": "f00d",
        "auth": {
            "client_token": token,
            "accessor": accessor,
            "token_policies": ["default", "billing-ro"],
            "metadata": None,
            "lease_duration": lease,
            "renewable": renewable,
        },
    })


def renew_ok(token=TOKEN_A, lease=600, renewable=True):
    return (200, {
        "auth": {
            "client_token": token,
            "policies": ["default", "billing-ro"],
            "lease_duration": lease,
            "renewable": renewable,
        },
    })


class _Recorded:
    def __init__(self, method, path, headers, body):
        self.method = method
        self.path = path
        self.headers = headers  # dict, lower-cased keys
        self.body = body  # parsed JSON dict, or None


class FakeVault:
    def __init__(self):
        self.requests = []
        self.routes = {}  # "POST /v1/..." -> list of (status, payload-or-None)
        self._lock = threading.Lock()

    def route(self, method, path, responses):
        self.routes[method + " " + path] = list(responses)

    def _respond(self, method, path):
        with self._lock:
            queue = self.routes.get(method + " " + path)
            if not queue:
                return 404, {"errors": []}
            if len(queue) > 1:
                return queue.pop(0)
            return queue[0]

    def by_path(self, path):
        return [r for r in self.requests if r.path == path]


def _make_handler(fake):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            body = json.loads(raw) if raw else None
            headers = {k.lower(): v for k, v in self.headers.items()}
            with fake._lock:
                fake.requests.append(_Recorded("POST", self.path, headers, body))
            status, payload = fake._respond("POST", self.path)
            if payload is None:
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    return Handler


@contextlib.contextmanager
def fake_vault():
    fake = FakeVault()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(fake))
    fake.url = "http://127.0.0.1:%d" % server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield fake
    finally:
        server.shutdown()
        server.server_close()


class Clock:
    def __init__(self, start=T0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def new_session(fake, clock, sleeps, **kw):
    from vaultsession import AppRoleSession

    kw.setdefault("namespace", "team-eng/")
    return AppRoleSession(
        fake.url,
        ROLE_ID,
        SECRET_ID,
        clock=clock,
        sleep=sleeps.append,
        **kw,
    )


LOGIN = "/v1/auth/approle/login"
RENEW = "/v1/auth/token/renew-self"
REVOKE = "/v1/auth/token/revoke-self"


def test_login_contract():
    with fake_vault() as fake:
        fake.route("POST", LOGIN, [login_ok()])
        clock, sleeps = Clock(), []
        s = new_session(fake, clock, sleeps)

        assert s.token() == TOKEN_A, "token() must return the client_token from the login response"
        assert s.token() == TOKEN_A
        logins = fake.by_path(LOGIN)
        assert len(logins) == 1, "a second token() inside the lease must not log in again (got %d logins)" % len(logins)
        r = logins[0]
        assert r.method == "POST"
        assert r.body == {"role_id": ROLE_ID, "secret_id": SECRET_ID}, "login body must be exactly role_id + secret_id, got %r" % (r.body,)
        assert "x-vault-token" not in r.headers, "the login request is unauthenticated; it must not send X-Vault-Token"
        assert r.headers.get("x-vault-namespace") == "team-eng/", "X-Vault-Namespace must be sent on login when configured"
        assert r.headers.get("content-type", "").startswith("application/json")

        h = s.headers()
        assert h["X-Vault-Token"] == TOKEN_A, "headers() must carry the live token"
        assert h["X-Vault-Namespace"] == "team-eng/"
        assert sleeps == [], "the happy path must never sleep"


def test_custom_mount_and_root_namespace():
    with fake_vault() as fake:
        path = "/v1/auth/ci-approle/login"
        fake.route("POST", path, [login_ok()])
        clock, sleeps = Clock(), []
        s = new_session(fake, clock, sleeps, mount="ci-approle", namespace=None)

        assert s.token() == TOKEN_A
        r = fake.by_path(path)[0]
        assert "x-vault-namespace" not in r.headers, "a root-namespace session must not send the namespace header at all"
        assert "X-Vault-Namespace" not in s.headers(), "headers() must omit the namespace key for the root namespace"
        assert fake.by_path(LOGIN) == [], "custom mount must change the login path (auth/ci-approle/login)"


def test_renew_at_threshold():
    with fake_vault() as fake:
        fake.route("POST", LOGIN, [login_ok(lease=1200)])
        fake.route("POST", RENEW, [renew_ok(lease=600), renew_ok(lease=600)])
        clock, sleeps = Clock(), []
        s = new_session(fake, clock, sleeps)  # renew_threshold defaults to 0.25

        s.token()
        clock.advance(899)  # remaining 301 > 300
        s.token()
        assert fake.by_path(RENEW) == [], "renew must not fire while remaining ttl is above the threshold"

        clock.advance(1)  # elapsed 900, remaining 300 == 0.25 * 1200
        assert s.token() == TOKEN_A
        renews = fake.by_path(RENEW)
        assert len(renews) == 1, "remaining ttl at exactly the threshold must renew (got %d renewals)" % len(renews)
        r = renews[0]
        assert r.headers.get("x-vault-token") == TOKEN_A, "renew-self must authenticate with the current token"
        assert r.headers.get("x-vault-namespace") == "team-eng/"
        assert r.body == {"increment": "1200s"}, "renew must request the login lease as the increment, got %r" % (r.body,)

        # The renewal was granted only 600s; the next renewal fires from that.
        clock.advance(449)  # remaining 151 > 150
        s.token()
        assert len(fake.by_path(RENEW)) == 1, "granted lease_duration (600) must drive the next threshold, not the requested increment"
        clock.advance(1)  # remaining 150 == 0.25 * 600
        s.token()
        assert len(fake.by_path(RENEW)) == 2
        assert len(fake.by_path(LOGIN)) == 1, "a renewable token must be renewed, never re-logged-in"
        assert sleeps == []


def test_non_renewable_token_relogs_in():
    with fake_vault() as fake:
        fake.route("POST", LOGIN, [login_ok(lease=1200, renewable=False),
                                   login_ok(token=TOKEN_B, lease=1200, renewable=False)])
        clock, sleeps = Clock(), []
        s = new_session(fake, clock, sleeps)

        assert s.token() == TOKEN_A
        clock.advance(901)
        assert s.token() == TOKEN_B, "a non-renewable token past the threshold must be replaced by a fresh login"
        assert len(fake.by_path(LOGIN)) == 2
        assert fake.by_path(RENEW) == [], "renew-self must never be called for a non-renewable token"


def test_retry_standby_and_overload_statuses():
    with fake_vault() as fake:
        fake.route("POST", LOGIN, [
            (503, {"errors": ["Vault is sealed"]}),
            (429, {"errors": ["standby node"]}),
            (472, {"errors": ["disaster recovery mode replication secondary"]}),
            login_ok(),
        ])
        clock, sleeps = Clock(), []
        s = new_session(fake, clock, sleeps, retry_delays=(0.5, 1.0, 2.0))

        assert s.token() == TOKEN_A, "login must succeed after retrying 503/429/472"
        assert len(fake.by_path(LOGIN)) == 4
        assert sleeps == [0.5, 1.0, 2.0], "retries must sleep the injected schedule in order, got %r" % (sleeps,)


def test_retries_exhausted_raise_structured_error():
    from vaultsession import VaultServerError

    with fake_vault() as fake:
        fake.route("POST", LOGIN, [(473, {"errors": ["performance standby"]})])
        clock, sleeps = Clock(), []
        s = new_session(fake, clock, sleeps, retry_delays=(0.5, 1.0, 2.0))

        try:
            s.token()
            raise AssertionError("login must raise once the retry schedule is exhausted")
        except VaultServerError as exc:
            assert exc.status == 473, "exc.status = %r" % (exc.status,)
            assert exc.errors == ["performance standby"], "exc.errors = %r" % (exc.errors,)
            assert "473" in str(exc)
        assert len(fake.by_path(LOGIN)) == 4, "one initial attempt plus one per retry delay"
        assert sleeps == [0.5, 1.0, 2.0]


def test_client_errors_never_retry():
    from vaultsession import VaultServerError

    with fake_vault() as fake:
        fake.route("POST", LOGIN, [(400, {"errors": ["invalid role or secret ID"]})])
        clock, sleeps = Clock(), []
        s = new_session(fake, clock, sleeps)

        try:
            s.token()
            raise AssertionError("a 400 login must raise immediately")
        except VaultServerError as exc:
            assert exc.status == 400
            assert exc.errors == ["invalid role or secret ID"]
        assert len(fake.by_path(LOGIN)) == 1, "4xx client errors must not be retried"
        assert sleeps == []


def test_renew_forbidden_falls_back_to_fresh_login():
    with fake_vault() as fake:
        fake.route("POST", LOGIN, [login_ok(), login_ok(token=TOKEN_B)])
        fake.route("POST", RENEW, [(403, {"errors": ["permission denied"]})])
        clock, sleeps = Clock(), []
        s = new_session(fake, clock, sleeps)

        assert s.token() == TOKEN_A
        clock.advance(900)
        assert s.token() == TOKEN_B, "a 403 on renew-self means the token is gone; the session must log in again"
        assert len(fake.by_path(RENEW)) == 1
        assert len(fake.by_path(LOGIN)) == 2
        assert sleeps == [], "403 is not a standby status; it must not be retried"


def test_revoke_on_shutdown():
    with fake_vault() as fake:
        fake.route("POST", LOGIN, [login_ok()])
        fake.route("POST", REVOKE, [(204, None)])
        clock, sleeps = Clock(), []
        with new_session(fake, clock, sleeps) as s:
            s.token()
        revokes = fake.by_path(REVOKE)
        assert len(revokes) == 1, "leaving the context must revoke the live token"
        r = revokes[0]
        assert r.headers.get("x-vault-token") == TOKEN_A, "revoke-self must authenticate with the token being revoked"
        assert r.headers.get("x-vault-namespace") == "team-eng/"

        s.close()
        assert len(fake.by_path(REVOKE)) == 1, "a second close must not revoke again"

        # A session that never logged in has nothing to revoke.
        s2 = new_session(fake, Clock(), [])
        s2.close()
        assert len(fake.by_path(REVOKE)) == 1, "close() without a token must not call Vault"


def test_token_material_never_logged():
    from vaultsession import VaultServerError

    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(self.format(record))

    capture = Capture(level=logging.DEBUG)
    logger = logging.getLogger("vaultsession")
    old_level = logger.level
    logger.addHandler(capture)
    logger.setLevel(logging.DEBUG)
    try:
        with fake_vault() as fake:
            fake.route("POST", LOGIN, [login_ok()])
            fake.route("POST", RENEW, [renew_ok()])
            fake.route("POST", REVOKE, [(204, None)])
            clock, sleeps = Clock(), []
            s = new_session(fake, clock, sleeps)
            s.token()
            clock.advance(900)
            s.token()
            s.close()

            assert len(records) >= 3, "login, renewal, and revocation must each emit a vaultsession log line (got %d)" % len(records)
            for msg in records:
                assert TOKEN_A not in msg, "client token leaked into a log message: %r" % msg
                assert SECRET_ID not in msg, "secret_id leaked into a log message: %r" % msg
            assert TOKEN_A not in repr(s) and SECRET_ID not in repr(s), "repr() must mask credential material"

        with fake_vault() as fake:
            fake.route("POST", LOGIN, [login_ok(), (403, {"errors": ["permission denied"]})])
            fake.route("POST", RENEW, [(403, {"errors": ["permission denied"]})])
            clock, sleeps = Clock(), []
            s = new_session(fake, clock, sleeps)
            s.token()
            clock.advance(900)
            try:
                s.token()
                raise AssertionError("renew 403 followed by login 403 must raise")
            except VaultServerError as exc:
                assert TOKEN_A not in str(exc), "exception text leaked the client token"
                assert SECRET_ID not in str(exc), "exception text leaked the secret_id"
            for msg in records:
                assert TOKEN_A not in msg and SECRET_ID not in msg
    finally:
        logger.removeHandler(capture)
        logger.setLevel(old_level)


def main():
    tests = [
        test_login_contract,
        test_custom_mount_and_root_namespace,
        test_renew_at_threshold,
        test_non_renewable_token_relogs_in,
        test_retry_standby_and_overload_statuses,
        test_retries_exhausted_raise_structured_error,
        test_client_errors_never_retry,
        test_renew_forbidden_falls_back_to_fresh_login,
        test_revoke_on_shutdown,
        test_token_material_never_logged,
    ]
    for t in tests:
        t()
        print("ok  %s" % t.__name__)
    print("all %d test groups passed" % len(tests))


if __name__ == "__main__":
    main()
