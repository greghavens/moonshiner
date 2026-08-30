#!/usr/bin/env python3
"""Contract-pinned loopback stand-in for a VCF 9.0 vCenter Automation endpoint.

Routing is built from docs/contract.json, which is a projection of
specifications/vsphere/openapi/automation/vcenter.yaml in vmware/vcf-api-specs
at tag 9.0.0.0.  Only the four operations the contract names are served; every
other target answers 404.  The form-encoded body of
Vcenter.Authentication.Token_issue is validated against the projected IssueSpec,
so an unknown, repeated or empty-valued field is rejected the way the real
endpoint rejects it.

Two knobs let a test reproduce a busy endpoint deterministically:

  --barrier-count N  the first N token exchanges are held until all N of them
                     have arrived, so a client that dispatches them one after
                     another never gets past the barrier
  --hold-ms MS       once released, a held exchange waits MS milliseconds before
                     its response is produced
  --expire-session-on-issue N
                     immediately before authenticating token exchange N, expire
                     the session it presents (zero disables this fixture)

While an exchange is held its session may be deleted underneath it.  When that
happens the exchange is answered 401 Unauthenticated and flagged "stranded" in
the log, exactly the outcome a rotation is supposed to avoid.

Every request is appended to a JSON Lines log with an arrival ordinal and a
completion ordinal drawn from one monotonic sequence, so a test can order any
arrival against any completion and assert how overlapping requests interleaved.

This file is protected.  Do not modify it.
"""

import argparse
import base64
import binascii
import json
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, unquote, urlparse

IMPLEMENTED = (
    "Cis.Session_create",
    "Cis.Session_get",
    "Cis.Session_delete",
    "Vcenter.Authentication.Token_issue",
)

TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
DEFAULT_ISSUED_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"

# Fixed epoch so Cis.Session.Info timestamps are reproducible run to run.
EPOCH = "2026-03-04T08:%02d:%02dZ"


class OauthError(Exception):
    """An Oauth2.Errors.Error response, as documented for Token_issue."""

    def __init__(self, status, error, description):
        super().__init__(description)
        self.status = status
        self.payload = {"error": error, "error_description": description}


class VapiError(Exception):
    """A Vapi.Std.Errors.Error response."""

    def __init__(self, status, error_type, message, message_id):
        super().__init__(message)
        self.status = status
        self.payload = {
            "error_type": error_type,
            "messages": [{"args": [], "default_message": message, "id": message_id}],
        }


def unauthenticated(message):
    return VapiError(401, "UNAUTHENTICATED", message,
                     "com.vmware.vapi.std.errors.unauthenticated")


def not_found(message):
    return VapiError(404, "NOT_FOUND", message,
                     "com.vmware.vapi.std.errors.not_found")


class StrandedSession(VapiError):
    """Raised when a session is terminated while one of its requests is open."""

    def __init__(self):
        VapiError.__init__(
            self, 401, "UNAUTHENTICATED",
            "the session was terminated while this request was still in flight",
            "com.vmware.vapi.std.errors.unauthenticated")


# ---------------------------------------------------------------------------
# contract
# ---------------------------------------------------------------------------

class Contract:
    def __init__(self, path):
        with open(path, "r", encoding="utf-8") as handle:
            self.doc = json.load(handle)
        self.base_path = self.doc["basePath"].rstrip("/")
        self.schemas = self.doc["schemas"]
        self.routes = []
        for op in self.doc["operations"]:
            if op["operationId"] not in IMPLEMENTED:
                raise SystemExit(
                    "contract names operation %r, which this mock does not implement"
                    % op["operationId"])
            self.routes.append(op)
        self.api_key_header = self.doc["securitySchemes"]["api_key_auth"]["name"].lower()

    def issue_spec(self):
        return self.schemas["Vcenter.Authentication.Token.IssueSpec"]

    def match(self, method, path, query):
        if path != self.base_path and not path.startswith(self.base_path + "/"):
            return None
        rest = path[len(self.base_path):] or "/"
        for op in self.routes:
            if op["method"] != method:
                continue
            if op["path"] != rest:
                continue
            pinned = op.get("query") or {}
            if any(query.get(key) != value for key, value in pinned.items()):
                continue
            if set(query) - set(pinned):
                continue
            return op
        return None


# ---------------------------------------------------------------------------
# endpoint state
# ---------------------------------------------------------------------------

class Endpoint:
    def __init__(self, contract, args):
        self.contract = contract
        self.username = args.username
        self.accepted_passwords = [args.password]
        if args.rotated_password is not None:
            self.accepted_passwords.append(args.rotated_password)
        self.session_user = args.session_user or args.username
        self.hold_seconds = args.hold_ms / 1000.0
        self.lock = threading.Lock()
        self.sessions = {}
        self.session_seq = 0
        # One monotonic sequence covers both arrivals and completions, so a test
        # can order an arrival against a completion.
        self.event_seq = 0
        self.barrier_slots = args.barrier_count
        self.barrier = threading.Barrier(args.barrier_count) if args.barrier_count > 0 else None
        self.issue_seq = 0
        self.expire_session_on_issue = args.expire_session_on_issue
        self.log_path = args.log
        self.log_lock = threading.Lock()

    # -- sessions ----------------------------------------------------------

    def create_session(self):
        with self.lock:
            self.session_seq += 1
            token = "7b3f9c2e4d1a48f0b6c5%012x" % self.session_seq
            self.sessions[token] = {
                "user": self.session_user,
                "created": EPOCH % (10 + self.session_seq, 0),
            }
            return token

    def session_alive(self, token):
        with self.lock:
            return token is not None and token in self.sessions

    def session_info(self, token):
        with self.lock:
            return dict(self.sessions[token])

    def drop_session(self, token):
        with self.lock:
            return self.sessions.pop(token, None) is not None

    # -- ordinals ----------------------------------------------------------

    def next_event(self):
        with self.lock:
            self.event_seq += 1
            return self.event_seq

    # -- barrier -----------------------------------------------------------

    def take_barrier_slot(self):
        with self.lock:
            if self.barrier_slots <= 0:
                return False
            self.barrier_slots -= 1
            return True

    def should_expire_issue_session(self):
        """Model a session expiring just as a selected exchange arrives."""
        with self.lock:
            self.issue_seq += 1
            return self.issue_seq == self.expire_session_on_issue

    def wait_at_barrier(self):
        try:
            self.barrier.wait(timeout=15)
        except threading.BrokenBarrierError:
            raise OauthError(
                400, "invalid_request",
                "the endpoint holds concurrent token exchanges together; this one "
                "waited alone and timed out, so the caller did not have the other "
                "in-flight exchanges open at the same time")

    def release_barrier(self):
        if self.barrier is not None:
            self.barrier.abort()

    # -- log ---------------------------------------------------------------

    def record(self, entry):
        with self.log_lock:
            with open(self.log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")
                handle.flush()


# ---------------------------------------------------------------------------
# request handling
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "mock-vcenter/1.0"

    def log_message(self, fmt, *args):  # keep the test output clean
        pass

    # -- plumbing ----------------------------------------------------------

    def _read_body(self):
        length = self.headers.get("Content-Length")
        if not length:
            return b""
        try:
            count = int(length)
        except ValueError:
            return b""
        return self.rfile.read(count) if count > 0 else b""

    def _headers(self):
        out = {}
        for key, value in self.headers.items():
            out[key.lower()] = value
        return out

    def _dispatch(self, method):
        endpoint = self.server.endpoint
        contract = endpoint.contract
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = {}
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            query[key] = value
        headers = self._headers()
        raw_body = self._read_body()
        arrived = endpoint.next_event()

        operation = contract.match(method, path, query)
        self.parsed_form = None
        stranded = False
        status = None
        payload = None
        try:
            if operation is None:
                raise not_found("no operation is served at %s %s" % (method, self.path))
            handler = {
                "Cis.Session_create": self._session_create,
                "Cis.Session_get": self._session_get,
                "Cis.Session_delete": self._session_delete,
                "Vcenter.Authentication.Token_issue": self._token_issue,
            }[operation["operationId"]]
            status, payload = handler(headers, raw_body)
        except StrandedSession as err:
            status, payload, stranded = err.status, err.payload, True
        except (OauthError, VapiError) as err:
            status, payload = err.status, err.payload

        form = self.parsed_form
        completed = endpoint.next_event()
        endpoint.record({
            "arrived": arrived,
            "completed": completed,
            "method": method,
            "target": self.path,
            "path": path,
            "query": query,
            "headers": headers,
            "body": raw_body.decode("utf-8", "replace"),
            "form": form,
            "operationId": None if operation is None else operation["operationId"],
            "session": headers.get(contract.api_key_header),
            "status": status,
            "stranded": stranded,
        })
        self._respond(status, payload)

    def _respond(self, status, payload):
        if status == 204 or payload is None:
            body = b""
        else:
            body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        if body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")

    # -- operations --------------------------------------------------------

    def _require_session(self, headers):
        endpoint = self.server.endpoint
        token = headers.get(endpoint.contract.api_key_header)
        if not token or not endpoint.session_alive(token):
            raise unauthenticated("the session identifier is missing or no longer valid")
        return token

    def _session_create(self, headers, raw_body):
        endpoint = self.server.endpoint
        authorization = headers.get("authorization", "")
        if not authorization.lower().startswith("basic "):
            raise unauthenticated("Cis.Session_create requires HTTP Basic credentials")
        try:
            decoded = base64.b64decode(authorization.split(None, 1)[1].strip(),
                                       validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, IndexError):
            raise unauthenticated("the Authorization header is not valid Basic credentials")
        user, _, password = decoded.partition(":")
        if user != endpoint.username or password not in endpoint.accepted_passwords:
            raise unauthenticated("the supplied credentials were rejected")
        return 201, endpoint.create_session()

    def _session_get(self, headers, raw_body):
        endpoint = self.server.endpoint
        token = self._require_session(headers)
        info = endpoint.session_info(token)
        tick = endpoint.event_seq
        accessed = EPOCH % (10 + tick // 60, tick % 60)
        return 200, {
            "user": info["user"],
            "created_time": info["created"],
            "last_accessed_time": accessed,
        }

    def _session_delete(self, headers, raw_body):
        endpoint = self.server.endpoint
        token = self._require_session(headers)
        endpoint.drop_session(token)
        return 204, None

    def _token_issue(self, headers, raw_body):
        endpoint = self.server.endpoint
        media = headers.get("content-type", "").split(";")[0].strip().lower()
        if media != "application/x-www-form-urlencoded":
            raise OauthError(
                400, "invalid_request",
                "Vcenter.Authentication.Token_issue expects "
                "application/x-www-form-urlencoded, got %r" % (media or "nothing"))
        if endpoint.should_expire_issue_session():
            endpoint.drop_session(headers.get(endpoint.contract.api_key_header))
        token = self._require_session(headers)

        pairs = parse_qsl(raw_body.decode("utf-8", "replace"), keep_blank_values=True)
        declared = endpoint.contract.issue_spec()["properties"]
        form = {}
        for key, value in pairs:
            if key not in declared:
                raise OauthError(
                    400, "invalid_request",
                    "IssueSpec declares no property %r; declared properties are %s"
                    % (key, ", ".join(declared)))
            if key in form:
                raise OauthError(400, "invalid_request",
                                 "property %r is repeated in the request body" % key)
            if value == "":
                raise OauthError(
                    400, "invalid_request",
                    "property %r was sent with an empty value; an optional property "
                    "that has no value must be absent from the request" % key)
            form[key] = value
        self.parsed_form = form

        if "grant_type" not in form:
            raise OauthError(400, "invalid_request", "grant_type is required")
        if form["grant_type"] != TOKEN_EXCHANGE:
            raise OauthError(400, "unsupported_grant_type",
                             "grant_type %r is not supported" % form["grant_type"])
        for required in ("subject_token", "subject_token_type"):
            if required not in form:
                raise OauthError(
                    400, "invalid_request",
                    "%s is required under the token-exchange grant type" % required)
        if ("actor_token" in form) != ("actor_token_type" in form):
            raise OauthError(
                400, "invalid_request",
                "actor_token and actor_token_type are sent together or not at all")

        if endpoint.barrier is not None and endpoint.take_barrier_slot():
            endpoint.wait_at_barrier()
        if endpoint.hold_seconds > 0:
            time.sleep(endpoint.hold_seconds)

        if not endpoint.session_alive(token):
            # The session was terminated while this exchange was still open.
            raise StrandedSession()

        body = {
            "access_token": "at-%s" % token[-6:],
            "token_type": "Bearer",
            "expires_in": 600,
            "issued_token_type": form.get("requested_token_type", DEFAULT_ISSUED_TOKEN_TYPE),
        }
        if "scope" in form:
            body["scope"] = form["scope"]
        return 200, body


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "docs", "contract.json"))
    parser.add_argument("--log", required=True)
    parser.add_argument("--port-file", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--rotated-password", default=None)
    parser.add_argument("--session-user", default=None)
    parser.add_argument("--barrier-count", type=int, default=0)
    parser.add_argument("--hold-ms", type=int, default=0)
    parser.add_argument("--expire-session-on-issue", type=int, default=0,
                        help="expire the request's session immediately before the "
                             "selected token-exchange ordinal")
    args = parser.parse_args(argv)

    contract = Contract(args.contract)
    open(args.log, "a", encoding="utf-8").close()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.endpoint = Endpoint(contract, args)

    def shutdown(*_):
        server.endpoint.release_barrier()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    tmp = args.port_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(str(server.server_address[1]))
    os.replace(tmp, args.port_file)

    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
