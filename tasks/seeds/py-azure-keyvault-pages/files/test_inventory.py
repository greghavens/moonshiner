"""Protected acceptance tests for the Key Vault secret-metadata inventory.

Hermetic: a loopback http.server stands in for the vault; nothing leaves
127.0.0.1 and no real credentials exist anywhere.
"""

import contextlib
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from kvclient import VaultSession
from kvinventory import (
    VaultAuthenticationError,
    VaultAuthorizationError,
    VaultRequestError,
    VaultThrottledError,
    collect_inventory,
)

TOKEN = "dummy-kv-token"
API = "2025-07-01"


class MockVault:
    """Scripted loopback vault. Routes map an exact "path?query" string (or a
    bare path as fallback) to a list of (status, headers, body) responses
    consumed in order; the last response repeats."""

    def __init__(self):
        self.routes = {}
        self.requests = []

    def route(self, key, *responses):
        self.routes[key] = list(responses)

    def lookup(self, raw_path):
        if raw_path in self.routes:
            return self.routes[raw_path]
        bare = raw_path.split("?", 1)[0]
        return self.routes.get(bare)


@contextlib.contextmanager
def run_vault():
    mock = MockVault()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            mock.requests.append(
                {
                    "path": self.path,
                    "auth": self.headers.get("Authorization"),
                    "accept": self.headers.get("Accept"),
                }
            )
            responses = mock.lookup(self.path)
            if not responses:
                status, headers, body = 599, {}, {"error": {"code": "UnexpectedRequest",
                                                            "message": self.path}}
            elif len(responses) > 1:
                status, headers, body = responses.pop(0)
            else:
                status, headers, body = responses[0]
            payload = json.dumps(body).encode("utf-8")
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    mock.base = "http://127.0.0.1:%d" % server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield mock
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def session_for(mock):
    return VaultSession(mock.base, lambda: TOKEN)


def query_of(recorded):
    return urllib.parse.parse_qs(
        urllib.parse.urlsplit(recorded["path"]).query, keep_blank_values=True
    )


def item(name, base, attributes=None, **extra):
    body = {"id": "%s/secrets/%s" % (base, name)}
    if attributes is not None:
        body["attributes"] = attributes
    body.update(extra)
    return body


def test_first_page_request_shape():
    with run_vault() as mock:
        mock.route("/secrets", (200, {}, {"value": [], "nextLink": None}))
        report = collect_inventory(session_for(mock))

    assert report["active"] == [], report
    assert report["deleted"] == []
    assert len(mock.requests) == 1, mock.requests
    first = mock.requests[0]
    assert first["path"].split("?")[0] == "/secrets"
    q = query_of(first)
    assert q["api-version"] == [API], q
    assert "maxresults" not in q, "maxresults must be omitted when no page size was requested"
    assert first["auth"] == "Bearer " + TOKEN
    assert first["accept"] == "application/json"


def test_maxresults_parameter():
    with run_vault() as mock:
        mock.route("/secrets", (200, {}, {"value": [], "nextLink": None}))
        collect_inventory(session_for(mock), max_page_size=10)

    q = query_of(mock.requests[0])
    assert q["maxresults"] == ["10"], q
    assert q["api-version"] == [API]


def test_next_link_is_followed_verbatim():
    with run_vault() as mock:
        base = mock.base
        # The live service emits continuation links whose api-version differs
        # from the one we sent (the docs sample shows 7.2). Opaque means opaque.
        link2 = base + "/secrets?api-version=7.2&$skiptoken=eyJOZXh0TWFya2VyIjoiMiE4OCJ9&maxresults=1"
        link3 = base + "/secrets?api-version=7.2&$skiptoken=eyJOZXh0TWFya2VyIjoiMyE4OCJ9&maxresults=1"
        mock.route(
            "/secrets",
            (200, {}, {"value": [item("alpha", base)], "nextLink": link2}),
        )
        # An empty intermediate page with a nextLink is NOT the end of the listing.
        mock.route(
            "/secrets?api-version=7.2&$skiptoken=eyJOZXh0TWFya2VyIjoiMiE4OCJ9&maxresults=1",
            (200, {}, {"value": [], "nextLink": link3}),
        )
        mock.route(
            "/secrets?api-version=7.2&$skiptoken=eyJOZXh0TWFya2VyIjoiMyE4OCJ9&maxresults=1",
            (200, {}, {"value": [item("omega", base)], "nextLink": None}),
        )
        report = collect_inventory(session_for(mock))

    names = [entry["name"] for entry in report["active"]]
    assert names == ["alpha", "omega"], names
    assert len(mock.requests) == 3, [r["path"] for r in mock.requests]
    assert mock.requests[1]["path"] == \
        "/secrets?api-version=7.2&$skiptoken=eyJOZXh0TWFya2VyIjoiMiE4OCJ9&maxresults=1"
    assert mock.requests[2]["path"] == \
        "/secrets?api-version=7.2&$skiptoken=eyJOZXh0TWFya2VyIjoiMyE4OCJ9&maxresults=1"
    for recorded in mock.requests:
        assert recorded["auth"] == "Bearer " + TOKEN, "every page needs the bearer token"


def test_metadata_mapping_and_no_value_fetch():
    with run_vault() as mock:
        base = mock.base
        rich = item(
            "db-password",
            base,
            attributes={
                "enabled": False,
                "created": 1719878400,
                "updated": 1720656000,
                "nbf": 1721260800,
                "exp": 1752796800,
                "recoveryLevel": "Recoverable+Purgeable",
                "recoverableDays": 90,
            },
            tags={"owner": "platform", "rotation": "quarterly"},
            contentType="text/plain",
            managed=True,
        )
        bare = item("legacy-key", base, attributes={"enabled": True})
        mock.route("/secrets", (200, {}, {"value": [rich, bare], "nextLink": ""}))
        report = collect_inventory(session_for(mock))

    first, second = report["active"]
    assert first["name"] == "db-password"
    assert first["id"] == mock.base + "/secrets/db-password"
    assert first["enabled"] is False
    assert first["created"] == 1719878400
    assert first["updated"] == 1720656000
    assert first["not_before"] == 1721260800
    assert first["expires"] == 1752796800
    assert first["tags"] == {"owner": "platform", "rotation": "quarterly"}
    assert first["content_type"] == "text/plain"
    assert first["managed"] is True

    assert second["name"] == "legacy-key"
    assert second["enabled"] is True
    assert second["not_before"] is None
    assert second["expires"] is None
    assert second["tags"] == {}
    assert second["content_type"] is None
    assert second["managed"] is None

    for recorded in mock.requests:
        path = recorded["path"].split("?")[0]
        assert not path.startswith("/secrets/"), \
            "inventory fetched a secret value: " + recorded["path"]


def test_deleted_secret_variants():
    with run_vault() as mock:
        base = mock.base
        link2 = base + "/deletedsecrets?api-version=7.2&$skiptoken=ZGVsZXRlZA=="
        gone = item(
            "old-cert",
            base,
            attributes={"enabled": True, "created": 1700000000},
            recoveryId=base + "/deletedsecrets/old-cert",
            deletedDate=1720915200,
            scheduledPurgeDate=1728691200,
            tags={"env": "prod"},
        )
        mock.route("/secrets", (200, {}, {"value": [item("live-one", base)], "nextLink": None}))
        mock.route("/deletedsecrets", (200, {}, {"value": [gone], "nextLink": link2}))
        mock.route(
            "/deletedsecrets?api-version=7.2&$skiptoken=ZGVsZXRlZA==",
            (200, {}, {"value": [], "nextLink": None}),
        )
        report = collect_inventory(session_for(mock), include_deleted=True)

    assert [entry["name"] for entry in report["active"]] == ["live-one"]
    deleted = report["deleted"]
    assert len(deleted) == 1, deleted
    entry = deleted[0]
    assert entry["name"] == "old-cert"
    assert entry["id"] == mock.base + "/secrets/old-cert"
    assert entry["recovery_id"] == mock.base + "/deletedsecrets/old-cert"
    assert entry["deleted_date"] == 1720915200
    assert entry["scheduled_purge_date"] == 1728691200
    assert entry["tags"] == {"env": "prod"}

    first_deleted = next(r for r in mock.requests if r["path"].startswith("/deletedsecrets"))
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(first_deleted["path"]).query)
    assert q["api-version"] == [API], q
    paged = [r["path"] for r in mock.requests if r["path"].startswith("/deletedsecrets")]
    assert paged[1] == "/deletedsecrets?api-version=7.2&$skiptoken=ZGVsZXRlZA=="


def test_deleted_secrets_not_requested_by_default():
    with run_vault() as mock:
        mock.route("/secrets", (200, {}, {"value": [], "nextLink": None}))
        report = collect_inventory(session_for(mock))
    assert report["deleted"] == []
    assert all(not r["path"].startswith("/deletedsecrets") for r in mock.requests), \
        mock.requests


def error_body(code, message):
    return {"error": {"code": code, "message": message}}


def expect(exc_type, fn):
    try:
        fn()
    except exc_type as err:
        return err
    raise AssertionError("expected %s" % exc_type.__name__)


def test_401_maps_to_authentication_error():
    with run_vault() as mock:
        mock.route("/secrets", (401, {}, error_body("Unauthorized", "AKV10032: token expired")))
        err = expect(VaultAuthenticationError, lambda: collect_inventory(session_for(mock)))
    assert isinstance(err, VaultRequestError)
    assert err.status == 401
    assert err.code == "Unauthorized"
    assert "AKV10032" in err.message


def test_403_maps_to_authorization_error():
    with run_vault() as mock:
        mock.route("/secrets", (403, {}, error_body(
            "Forbidden", "Client address is not authorized and caller is not a trusted service")))
        err = expect(VaultAuthorizationError, lambda: collect_inventory(session_for(mock)))
    assert err.status == 403
    assert err.code == "Forbidden"
    assert not isinstance(err, VaultAuthenticationError), "401 and 403 must map to distinct types"
    assert not isinstance(err, VaultThrottledError)


def test_429_maps_to_throttled_error_with_retry_after():
    with run_vault() as mock:
        mock.route("/secrets", (429, {"Retry-After": "5"},
                                error_body("Throttled", "Request rate too high")))
        err = expect(VaultThrottledError, lambda: collect_inventory(session_for(mock)))
    assert err.status == 429
    assert err.code == "Throttled"
    assert err.retry_after == 5
    assert not isinstance(err, (VaultAuthenticationError, VaultAuthorizationError))

    with run_vault() as mock:
        mock.route("/secrets", (429, {}, error_body("Throttled", "no header this time")))
        err = expect(VaultThrottledError, lambda: collect_inventory(session_for(mock)))
    assert err.retry_after is None


def test_other_statuses_stay_generic():
    with run_vault() as mock:
        mock.route("/secrets", (500, {}, error_body("InternalServerError", "boom")))
        err = expect(VaultRequestError, lambda: collect_inventory(session_for(mock)))
    assert type(err) is VaultRequestError, "5xx must not pretend to be auth/throttle"
    assert err.status == 500
    assert err.code == "InternalServerError"


def test_error_during_pagination_is_mapped_too():
    with run_vault() as mock:
        base = mock.base
        link2 = base + "/secrets?api-version=7.2&$skiptoken=cGFnZTI="
        mock.route("/secrets", (200, {}, {"value": [item("kept", base)], "nextLink": link2}))
        mock.route(
            "/secrets?api-version=7.2&$skiptoken=cGFnZTI=",
            (403, {}, error_body("Forbidden", "policy changed mid-scan")),
        )
        err = expect(VaultAuthorizationError, lambda: collect_inventory(session_for(mock)))
    assert err.status == 403


def test_existing_get_secret_behavior_still_works():
    with run_vault() as mock:
        mock.route(
            "/secrets/deploy-token",
            (200, {}, {"value": "s3cr3t-fixture", "id": mock.base + "/secrets/deploy-token"}),
        )
        value = session_for(mock).get_secret("deploy-token")
    assert value == "s3cr3t-fixture"
    q = query_of(mock.requests[0])
    assert q["api-version"] == [API]


def main():
    tests = [fn for name, fn in sorted(globals().items()) if name.startswith("test_")]
    for fn in tests:
        fn()
        print("ok  %s" % fn.__name__)
    print("%d tests passed" % len(tests))


if __name__ == "__main__":
    main()
