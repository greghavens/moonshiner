"""Acceptance tests for the Okta API transport behind the user/group sync.

A loopback fake Okta org serves both the OAuth token endpoint and /api/v1,
speaking the wire contract pinned in docs/contract.json (private_key_jwt
client credentials, X-Rate-Limit-* headers, Link pagination). No real Okta,
no real credentials, no sleeps — the clock and all waiting are injected.
Protected — do not modify this file or anything under docs/.

Run: python3 test_okta_transport.py
"""

import base64
import hashlib
import http.server
import json
import threading
import urllib.parse
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import format_datetime

from oktasync.auth import ClientCredentialsTokenSource
from oktasync.transport import OktaTransport, RateLimitError, OktaAuthError
from oktasync.sync import sync_users

T0 = 1_767_600_000  # fake "now", UTC epoch seconds
CLIENT_ID = "0oadummyserviceapp01"
SCOPES = ["okta.users.read", "okta.users.manage"]
ASSERTION_URN = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"

CHECKS = 0


def check(cond, msg):
    global CHECKS
    assert cond, msg
    CHECKS += 1


def b64url_decode(seg):
    pad = "=" * (-len(seg) % 4)
    return base64.urlsafe_b64decode(seg + pad)


def b64url_encode(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def fake_signer(signing_input):
    """Stands in for the KMS-held private key: deterministic bytes over the
    exact JWT signing input."""
    return b"FAKESIG:" + hashlib.sha256(signing_input).digest()[:12]


class FakeClock:
    def __init__(self, start):
        self._now = float(start)

    def now(self):
        return self._now

    def advance(self, seconds):
        self._now += seconds


def http_date(epoch):
    return format_datetime(datetime.fromtimestamp(epoch, timezone.utc), usegmt=True)


def rate_headers(limit, remaining, reset):
    return {
        "X-Rate-Limit-Limit": str(limit),
        "X-Rate-Limit-Remaining": str(remaining),
        "X-Rate-Limit-Reset": str(reset),
    }


TOKEN_RESPONSE_1 = {
    "token_type": "Bearer",
    "expires_in": 3600,
    "access_token": "eyJdummy.access.token-1",
    "scope": "okta.users.read okta.users.manage",
}
TOKEN_RESPONSE_2 = dict(TOKEN_RESPONSE_1, access_token="eyJdummy.access.token-2")

RATE_LIMITED_BODY = {
    "errorCode": "E0000047",
    "errorSummary": "API call exceeded rate limit due to too many requests.",
    "errorLink": "E0000047",
    "errorId": "oaeQPivGUjND5v78vbYWW047",
    "errorCauses": [],
}

INVALID_TOKEN_BODY = {
    "errorCode": "E0000011",
    "errorSummary": "Invalid token provided",
    "errorLink": "E0000011",
    "errorId": "oaeQPivGUjND5v78vbYWW011",
    "errorCauses": [],
}


def user(uid, login):
    return {
        "id": uid,
        "status": "ACTIVE",
        "created": "2026-06-01T10:00:00.000Z",
        "profile": {
            "firstName": login.split(".")[0].title(),
            "lastName": "Fixture",
            "email": login,
            "login": login,
        },
    }


@contextmanager
def mock_okta(script):
    """Serves scripted responses in request order; records every request.

    Script items: dict(status=, body=(json-able or None), headers=dict,
    date_epoch=int). The Date header is always sent explicitly so reset
    arithmetic is exact.
    """
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _handle(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            requests.append({
                "method": self.command,
                "url": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": raw,
            })
            item = script[min(len(requests) - 1, len(script) - 1)]
            body = item.get("body")
            payload = b"" if body is None else json.dumps(body).encode("utf-8")
            self.send_response_only(item.get("status", 200))
            self.send_header("Date", http_date(item.get("date_epoch", T0)))
            for k, v in item.get("headers", {}).items():
                self.send_header(k, v)
            if payload:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = do_PUT = do_DELETE = _handle

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", requests
    finally:
        server.shutdown()
        server.server_close()


def build(base, script_clock=None, sleeps=None):
    clock = script_clock or FakeClock(T0)
    recorded = sleeps if sleeps is not None else []
    source = ClientCredentialsTokenSource(base, CLIENT_ID, SCOPES, fake_signer, clock)
    transport = OktaTransport(base, source, clock, recorded.append)
    return transport, clock, recorded


def parse_form(body):
    return {k: v[0] for k, v in urllib.parse.parse_qs(body).items()}


def query_of(url):
    q = urllib.parse.urlsplit(url).query
    return {k: v[0] for k, v in urllib.parse.parse_qs(q).items()}


# ----------------------------------------------------------------- tests


def test_token_request_is_documented_private_key_jwt():
    script = [
        {"body": TOKEN_RESPONSE_1, "headers": {}},
        {"body": user("00uA1", "ada.chen@example.com"), "headers": rate_headers(600, 599, T0 + 60)},
    ]
    with mock_okta(script) as (base, requests):
        transport, clock, sleeps = build(base)
        data = transport.get_json("/api/v1/users/00uA1")

        check(len(requests) == 2, "token fetch happens lazily before the first API call")
        tok = requests[0]
        check(tok["method"] == "POST", "token request must POST")
        check(tok["url"] == "/oauth2/v1/token", "org authorization server token endpoint")
        check("application/x-www-form-urlencoded" in tok["headers"].get("content-type", ""),
              "token request is form-encoded")
        check("authorization" not in tok["headers"],
              "client auth rides in the assertion, not an Authorization header")

        form = parse_form(tok["body"])
        check(form["grant_type"] == "client_credentials", "grant_type")
        check(form["scope"] == "okta.users.read okta.users.manage",
              "scopes are space-separated in request order")
        check(form["client_assertion_type"] == ASSERTION_URN, "client_assertion_type URN")

        seg = form["client_assertion"].split(".")
        check(len(seg) == 3, "client_assertion is a compact JWT")
        header = json.loads(b64url_decode(seg[0]))
        check(header == {"alg": "RS256", "typ": "JWT"}, f"JWT header, got {header}")
        claims = json.loads(b64url_decode(seg[1]))
        check(claims["iss"] == CLIENT_ID and claims["sub"] == CLIENT_ID,
              "iss and sub must both be the client_id")
        check(claims["aud"] == base + "/oauth2/v1/token",
              "aud is the exact token endpoint URL")
        check(claims["exp"] == T0 + 300, "exp comes from the injected clock (300s lifetime)")
        signing_input = (seg[0] + "." + seg[1]).encode("ascii")
        check(seg[2] == b64url_encode(fake_signer(signing_input)),
              "signature must be the injected signer's output over header.claims")

        api = requests[1]
        check(api["method"] == "GET" and api["url"] == "/api/v1/users/00uA1", "API call after token")
        check(api["headers"].get("authorization") == "Bearer " + TOKEN_RESPONSE_1["access_token"],
              "Bearer scheme with the fetched access token")
        check("application/json" in api["headers"].get("accept", ""), "Accept: application/json")
        check(data["profile"]["login"] == "ada.chen@example.com", "response JSON decoded")
        check(sleeps == [], "no waiting on the happy path")


def test_token_is_cached_within_its_lifetime():
    script = [
        {"body": TOKEN_RESPONSE_1},
        {"body": user("00uA1", "a@example.com"), "headers": rate_headers(600, 599, T0 + 60)},
        {"body": user("00uB2", "b@example.com"), "headers": rate_headers(600, 598, T0 + 60)},
    ]
    with mock_okta(script) as (base, requests):
        transport, clock, _ = build(base)
        transport.get_json("/api/v1/users/00uA1")
        clock.advance(1000)
        transport.get_json("/api/v1/users/00uB2")
        posts = [r for r in requests if r["url"] == "/oauth2/v1/token"]
        check(len(posts) == 1, "a fresh token is reused, not refetched")
        check(len(requests) == 3, "exactly one token call and two API calls")


def test_token_refreshes_before_expiry():
    script = [
        {"body": TOKEN_RESPONSE_1},
        {"body": user("00uA1", "a@example.com"), "headers": rate_headers(600, 599, T0 + 60)},
        {"body": TOKEN_RESPONSE_2},
        {"body": user("00uB2", "b@example.com"), "headers": rate_headers(600, 598, T0 + 4600)},
    ]
    with mock_okta(script) as (base, requests):
        transport, clock, _ = build(base)
        transport.get_json("/api/v1/users/00uA1")
        clock.advance(3541)  # 60s early-refresh window on a 3600s token
        transport.get_json("/api/v1/users/00uB2")
        posts = [r for r in requests if r["url"] == "/oauth2/v1/token"]
        check(len(posts) == 2, "token refreshes inside the 60s pre-expiry window")
        check(requests[3]["headers"].get("authorization")
              == "Bearer " + TOKEN_RESPONSE_2["access_token"],
              "the refreshed token is the one actually used")


def test_invalid_token_401_refreshes_once_then_succeeds():
    script = [
        {"body": TOKEN_RESPONSE_1},
        {"status": 401, "body": INVALID_TOKEN_BODY},
        {"body": TOKEN_RESPONSE_2},
        {"body": user("00uA1", "a@example.com"), "headers": rate_headers(600, 599, T0 + 60)},
    ]
    with mock_okta(script) as (base, requests):
        transport, clock, sleeps = build(base)
        data = transport.get_json("/api/v1/users/00uA1")
        check([r["url"] for r in requests]
              == ["/oauth2/v1/token", "/api/v1/users/00uA1", "/oauth2/v1/token", "/api/v1/users/00uA1"],
              "401 E0000011 invalidates the cache, refreshes, retries once")
        check(requests[3]["headers"].get("authorization")
              == "Bearer " + TOKEN_RESPONSE_2["access_token"], "retry carries the new token")
        check(data["id"] == "00uA1", "retried call returns the payload")
        check(sleeps == [], "auth retry does not wait")


def test_persistent_401_raises_auth_error_without_loop():
    script = [
        {"body": TOKEN_RESPONSE_1},
        {"status": 401, "body": INVALID_TOKEN_BODY},
        {"body": TOKEN_RESPONSE_2},
        {"status": 401, "body": INVALID_TOKEN_BODY},
    ]
    with mock_okta(script) as (base, requests):
        transport, clock, _ = build(base)
        try:
            transport.get_json("/api/v1/users/00uA1")
            check(False, "second consecutive 401 must raise")
        except OktaAuthError as e:
            check("E0000011" in str(e), "auth error names the Okta errorCode")
            check(TOKEN_RESPONSE_1["access_token"] not in str(e)
                  and TOKEN_RESPONSE_2["access_token"] not in str(e),
                  "tokens never leak into error text")
        check(len(requests) == 4, "exactly one refresh attempt, no auth retry loop")


def test_rate_limit_headers_are_parsed_on_every_response():
    script = [
        {"body": TOKEN_RESPONSE_1},
        {"body": user("00uA1", "a@example.com"), "headers": rate_headers(600, 597, T0 + 42)},
    ]
    with mock_okta(script) as (base, _):
        transport, clock, _ = build(base)
        transport.get_json("/api/v1/users/00uA1")
        rl = transport.rate_limit
        check(rl is not None, "transport exposes the last rate-limit reading")
        check(rl.limit == 600, "X-Rate-Limit-Limit parsed")
        check(rl.remaining == 597, "X-Rate-Limit-Remaining parsed")
        check(rl.reset_epoch == T0 + 42, "X-Rate-Limit-Reset is UTC epoch seconds")


def test_429_on_get_waits_until_reset_then_retries_same_url():
    path = "/api/v1/users?limit=200&filter=" + urllib.parse.quote('status eq "ACTIVE"')
    script = [
        {"body": TOKEN_RESPONSE_1},
        {"status": 429, "body": RATE_LIMITED_BODY,
         "headers": rate_headers(600, 0, T0 + 25), "date_epoch": T0},
        {"body": [user("00uA1", "a@example.com")],
         "headers": rate_headers(600, 599, T0 + 85)},
    ]
    with mock_okta(script) as (base, requests):
        transport, clock, sleeps = build(base)
        data = transport.get_json(path)
        check(sleeps == [25], f"wait exactly reset-minus-Date seconds, got {sleeps}")
        check(requests[1]["url"] == requests[2]["url"] == path,
              "the retry re-issues the identical URL")
        check(len(requests) == 3, "one token call, one 429, one successful retry")
        check(len(data) == 1 and data[0]["id"] == "00uA1", "payload delivered after retry")


def test_429_get_retries_are_bounded():
    script = [
        {"body": TOKEN_RESPONSE_1},
        {"status": 429, "body": RATE_LIMITED_BODY,
         "headers": rate_headers(600, 0, T0 + 10), "date_epoch": T0},
        {"status": 429, "body": RATE_LIMITED_BODY,
         "headers": rate_headers(600, 0, T0 + 20), "date_epoch": T0 + 10},
        {"status": 429, "body": RATE_LIMITED_BODY,
         "headers": rate_headers(600, 0, T0 + 30), "date_epoch": T0 + 20},
    ]
    with mock_okta(script) as (base, requests):
        transport, clock, sleeps = build(base)
        try:
            transport.get_json("/api/v1/users/00uA1")
            check(False, "three 429s must exhaust the retry budget")
        except RateLimitError as e:
            check("E0000047" in str(e), "rate-limit error names the Okta errorCode")
            check(e.reset_epoch == T0 + 30, "error carries the latest reset epoch")
        check(len(requests) == 4, "initial attempt plus exactly two retries")
        check(sleeps == [10, 10], "one wait per retry, anchored on each response Date")


def test_pagination_follows_next_link_verbatim():
    first = "/api/v1/users?limit=200"
    script = [
        {"body": TOKEN_RESPONSE_1},
        {"body": [user("00uA1", "a@example.com"), user("00uB2", "b@example.com")],
         "headers": {}},
        {"body": [user("00uC3", "c@example.com")], "headers": {}},
    ]
    with mock_okta(script) as (base, requests):
        # Page 1 advertises a next link with reordered params and a server
        # marker; page 2 carries only a self link.
        script[1]["headers"] = {
            "Link": f'<{base}{first}>; rel="self", '
                    f'<{base}/api/v1/users?after=00uB2&limit=200&srvMarker=keep>; rel="next"',
            **rate_headers(600, 599, T0 + 60),
        }
        script[2]["headers"] = {
            "Link": f'<{base}/api/v1/users?after=00uB2&limit=200>; rel="self"',
            **rate_headers(600, 598, T0 + 60),
        }
        transport, clock, _ = build(base)
        logins = [u["profile"]["login"] for u in transport.paginate(first)]
        check(logins == ["a@example.com", "b@example.com", "c@example.com"],
              "all pages, wire order preserved")
        check(len(requests) == 3, "token plus one request per page")
        q = query_of(requests[2]["url"])
        check(q.get("after") == "00uB2", "cursor comes from the next link")
        check(q.get("srvMarker") == "keep",
              "the rel=next URL must be requested verbatim, not rebuilt")


def test_create_is_never_retried_on_429():
    profile = {"firstName": "New", "lastName": "Hire", "email": "n@example.com",
               "login": "n@example.com"}
    script = [
        {"body": TOKEN_RESPONSE_1},
        {"status": 429, "body": RATE_LIMITED_BODY,
         "headers": rate_headers(600, 0, T0 + 55), "date_epoch": T0},
    ]
    with mock_okta(script) as (base, requests):
        transport, clock, sleeps = build(base)
        try:
            transport.create("/api/v1/users?activate=false", {"profile": profile})
            check(False, "429 on a create must raise")
        except RateLimitError as e:
            check(e.reset_epoch == T0 + 55, "error carries the reset epoch for the caller")
        posts = [r for r in requests if r["method"] == "POST" and r["url"] != "/oauth2/v1/token"]
        check(len(posts) == 1, "a non-idempotent create is NEVER blindly retried")
        check(sleeps == [], "no wait — surfacing beats double-creating a user")


def test_sync_creates_missing_users_and_halts_cleanly_on_429():
    existing_page = [user("00uA1", "ada.chen@example.com")]
    new_carol = {"firstName": "Carol", "lastName": "Diaz",
                 "email": "carol.diaz@example.com", "login": "carol.diaz@example.com"}
    new_dev = {"firstName": "Devi", "lastName": "Rao",
               "email": "devi.rao@example.com", "login": "devi.rao@example.com"}
    ada = {"firstName": "Ada", "lastName": "Chen",
           "email": "ada.chen@example.com", "login": "ada.chen@example.com"}
    script = [
        {"body": TOKEN_RESPONSE_1},
        {"body": existing_page, "headers": rate_headers(600, 599, T0 + 60)},
        {"body": dict(user("00uC9", "carol.diaz@example.com")),
         "headers": rate_headers(600, 598, T0 + 60)},
        {"status": 429, "body": RATE_LIMITED_BODY,
         "headers": rate_headers(600, 0, T0 + 90), "date_epoch": T0},
    ]
    with mock_okta(script) as (base, requests):
        transport, clock, sleeps = build(base)
        report = sync_users(transport, [ada, new_carol, new_dev])

        creates = [r for r in requests if r["method"] == "POST" and r["url"] != "/oauth2/v1/token"]
        check(len(creates) == 2, "one create per missing login, none for existing, no retry")
        check(query_of(creates[0]["url"]).get("activate") == "false",
              "sync stages users; it must override Okta's activate-on-create default")
        body = json.loads(creates[0]["body"])
        check(body == {"profile": new_carol}, "create body nests the profile only")

        check(report["existing"] == ["ada.chen@example.com"], "already-present logins reported")
        check(report["created"] == ["carol.diaz@example.com"], "successful creates reported")
        check(report["halted"] == {"login": "devi.rao@example.com", "reset_epoch": T0 + 90},
              "the rate-limited create halts the sync with resume info")
        check(sleeps == [], "sync never sleeps through a create rate limit")


def main():
    tests = [
        test_token_request_is_documented_private_key_jwt,
        test_token_is_cached_within_its_lifetime,
        test_token_refreshes_before_expiry,
        test_invalid_token_401_refreshes_once_then_succeeds,
        test_persistent_401_raises_auth_error_without_loop,
        test_rate_limit_headers_are_parsed_on_every_response,
        test_429_on_get_waits_until_reset_then_retries_same_url,
        test_429_get_retries_are_bounded,
        test_pagination_follows_next_link_verbatim,
        test_create_is_never_retried_on_429,
        test_sync_creates_missing_users_and_halts_cleanly_on_429,
    ]
    for t in tests:
        t()
        print(f"ok - {t.__name__}")
    print(f"OK — {CHECKS} checks passed")


if __name__ == "__main__":
    main()
