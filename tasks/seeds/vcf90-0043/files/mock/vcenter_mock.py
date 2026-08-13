"""A loopback mock of the vSphere Automation API endpoints in docs/contract.json.

The route table is built from ``docs/contract.json`` at start-up, so the server
answers exactly the three operations the contract names and nothing else.  Any
other method/path pair is logged as unmatched and answered with 404.

Every request is appended to a JSONL log so a test can inspect the exact wire
shape that a client produced.  Run it standalone with::

    python -m mock.vcenter_mock --port 8080 --log /tmp/requests.jsonl

Nothing here talks to a real vCenter; the socket is bound to 127.0.0.1.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

CONTRACT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "contract.json"
)

UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# Fixed clock: the mock never reads wall time, so item models are byte-stable.
BASE_TIME = "2026-01-15T09:30:0%d.000Z"

DEFAULT_USERNAME = "administrator@vsphere.local"
DEFAULT_PASSWORD = "VMw@re123!Secure"

# Libraries the mock pretends already exist.
DEFAULT_LIBRARIES = ("0c1d2e3f-4a5b-4c6d-8e9f-0a1b2c3d4e5f",)


def load_contract(path=CONTRACT_PATH):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


class Route:
    """One contract operation compiled into a matchable route."""

    def __init__(self, operation_id, method, base_path, path_template):
        self.operation_id = operation_id
        self.method = method
        self.path_template = path_template
        pattern = re.escape(base_path + path_template)
        # Un-escape the {placeholder} segments into named capture groups.
        pattern = re.sub(
            r"\\\{(\w+)\\\}", lambda m: "(?P<%s>[^/]+)" % m.group(1), pattern
        )
        self.regex = re.compile("^" + pattern + "$")

    def match(self, method, path):
        if method != self.method:
            return None
        m = self.regex.match(path)
        return m.groupdict() if m else None


def build_routes(contract):
    base = contract["server"]["base_path"]
    routes = []
    for operation_id, op in contract["operations"].items():
        if op.get("query_discriminator"):
            # None of the pinned operations use one; guard the assumption anyway.
            raise ValueError(
                "operation %s needs query-discriminator routing" % operation_id
            )
        routes.append(Route(operation_id, op["method"], base, op["path"]))
    return routes


class MockState:
    """Server-side state and the contract rules the mock enforces."""

    def __init__(
        self,
        contract,
        username=DEFAULT_USERNAME,
        password=DEFAULT_PASSWORD,
        libraries=DEFAULT_LIBRARIES,
        log_path=None,
        create_fault_count=1,
        session_fault_count=0,
    ):
        self.contract = contract
        self.routes = build_routes(contract)
        self.username = username
        self.password = password
        self.libraries = set(libraries)
        self.log_path = log_path
        # Number of Content.Library.Item_create responses to turn into a 503
        # *after* the server has already decided what the item is.  This models
        # the dangerous failure: the write landed, the answer did not.
        self.create_fault_count = create_fault_count
        # Session creation is also documented with a transient 503. These
        # failures happen before a token is issued, so retrying does not create
        # abandoned server-side sessions.
        self.session_fault_count = session_fault_count

        self.lock = threading.Lock()
        self.sessions = set()
        self.items = {}  # item id -> ItemModel dict
        self.tokens = {}  # Client-Token -> item id
        self.seq = 0
        self._session_n = 0
        self._item_n = 0

        create = contract["schemas"]["Content.Library.ItemModel"]["create"]
        self.create_required = set(create["required"])
        self.create_optional = set(create["optional"])
        self.create_not_used = set(create["not_used"])

        if log_path:
            open(log_path, "w", encoding="utf-8").close()

    # -- identifiers ------------------------------------------------------
    def new_session_token(self):
        self._session_n += 1
        return hashlib.sha256(
            ("mock-session-%d" % self._session_n).encode()
        ).hexdigest()[:32]

    def new_item_id(self):
        self._item_n += 1
        return "c4a1f0d2-0000-4000-8000-%012d" % self._item_n

    # -- request log ------------------------------------------------------
    def log(self, record):
        if not self.log_path:
            return
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")


def _error(error_type, message):
    return {
        "error_type": error_type,
        "messages": [
            {
                "id": "com.vmware.api.mock.%s" % error_type.lower(),
                "default_message": message,
                "args": [],
            }
        ],
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "vcenter-mock/9.0.0.0"

    # -- plumbing ---------------------------------------------------------
    def log_message(self, fmt, *args):  # silence stderr chatter
        pass

    @property
    def state(self):
        return self.server.state

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _send(self, status, payload):
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        if body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)
        return status

    def _dispatch(self, method):
        parsed = urlparse(self.path)
        raw_body = self._read_body()
        try:
            body_json = json.loads(raw_body) if raw_body else None
        except ValueError:
            body_json = None

        matched, path_params = None, {}
        for route in self.state.routes:
            groups = route.match(method, parsed.path)
            if groups is not None:
                matched, path_params = route, groups
                break

        with self.state.lock:
            self.state.seq += 1
            seq = self.state.seq
            if matched is None:
                status, payload = 404, _error(
                    "NOT_FOUND",
                    "No operation in docs/contract.json serves %s %s."
                    % (method, parsed.path),
                )
            else:
                status, payload = self._handle(
                    matched, path_params, parsed, body_json, raw_body
                )

            self.state.log(
                {
                    "seq": seq,
                    "method": method,
                    "path": parsed.path,
                    "query": parse_qs(parsed.query),
                    "operation_id": matched.operation_id if matched else None,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body_raw": raw_body.decode("utf-8", "replace"),
                    "body_json": body_json,
                    "status": status,
                }
            )
        self._send(status, payload)

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

    # -- operations -------------------------------------------------------
    def _handle(self, route, path_params, parsed, body_json, raw_body):
        if route.operation_id == "Cis.Session_create":
            return self._session_create()
        unauth = self._require_session()
        if unauth:
            return unauth
        if route.operation_id == "Content.Library.Item_create":
            return self._item_create(body_json)
        if route.operation_id == "Content.Library.Item_get":
            return self._item_get(path_params["libraryItemId"])
        raise AssertionError("unroutable operation %s" % route.operation_id)

    def _session_create(self):
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return 401, _error(
                "UNAUTHENTICATED", "Cis.Session_create requires HTTP Basic auth."
            )
        try:
            decoded = base64.b64decode(header[6:]).decode("utf-8")
            user, _, password = decoded.partition(":")
        except Exception:
            return 401, _error("UNAUTHENTICATED", "Malformed Basic credentials.")
        if user != self.state.username or password != self.state.password:
            return 401, _error("UNAUTHENTICATED", "Invalid credentials.")
        if self.state.session_fault_count > 0:
            self.state.session_fault_count -= 1
            return 503, _error(
                "SERVICE_UNAVAILABLE",
                "The session service is temporarily unavailable; retry the request.",
            )
        token = self.state.new_session_token()
        self.state.sessions.add(token)
        return 201, token

    def _require_session(self):
        token = self.headers.get("vmware-api-session-id")
        if not token or token not in self.state.sessions:
            return 401, _error(
                "UNAUTHENTICATED",
                "Missing or unknown vmware-api-session-id header.",
            )
        return None

    def _item_create(self, body_json):
        client_token = self.headers.get("Client-Token")
        if client_token is not None and not UUID_RE.match(client_token):
            return 400, _error(
                "INVALID_ARGUMENT", "Client-Token does not conform to the UUID format."
            )

        # An already-seen token replays the original outcome: no second item.
        if client_token is not None and client_token in self.state.tokens:
            return self._maybe_fault(201, self.state.tokens[client_token])

        if not isinstance(body_json, dict):
            return 400, _error(
                "INVALID_ARGUMENT", "Request body must be a JSON object."
            )

        unknown = sorted(set(body_json) - self.state.create_required
                         - self.state.create_optional)
        if unknown:
            return 400, _error(
                "INVALID_ARGUMENT",
                "Properties not used for the create operation: %s."
                % ", ".join(unknown),
            )
        missing = sorted(self.state.create_required - set(body_json))
        if missing:
            return 400, _error(
                "INVALID_ARGUMENT",
                "Properties required for the create operation: %s."
                % ", ".join(missing),
            )

        name = body_json.get("name")
        if not isinstance(name, str) or not name:
            return 400, _error("INVALID_ARGUMENT", "name may not be empty.")
        if len(name) > 80:
            return 400, _error("INVALID_ARGUMENT", "name exceeds 80 characters.")
        description = body_json.get("description")
        if description is not None and not isinstance(description, str):
            return 400, _error("INVALID_ARGUMENT", "description must be a string.")
        if isinstance(description, str) and len(description) > 2000:
            return 400, _error(
                "INVALID_ARGUMENT", "description exceeds 2000 characters."
            )
        library_id = body_json.get("library_id")
        if library_id not in self.state.libraries:
            return 404, _error(
                "NOT_FOUND", "Library %r does not exist." % (library_id,)
            )
        if any(
            item["name"] == name and item["library_id"] == library_id
            for item in self.state.items.values()
        ):
            return 400, _error(
                "ALREADY_EXISTS",
                "A library item named %r already exists in the library." % name,
            )

        item_id = self.state.new_item_id()
        stamp = BASE_TIME % min(self.state._item_n, 9)
        item = {
            "id": item_id,
            "library_id": library_id,
            "name": name,
            "content_version": "1",
            "metadata_version": "1",
            "version": "1",
            "creation_time": stamp,
            "last_modified_time": stamp,
            "cached": False,
            "size": 0,
            "security_compliance": True,
        }
        if "description" in body_json:
            item["description"] = description
        if "type" in body_json:
            item["type"] = body_json["type"]
        self.state.items[item_id] = item
        if client_token is not None:
            self.state.tokens[client_token] = item_id
        return self._maybe_fault(201, item_id)

    def _maybe_fault(self, status, payload):
        """Convert the first N create answers into a transient 503.

        The item is already committed by this point, which is exactly why the
        retry has to carry the original Client-Token.
        """
        if self.state.create_fault_count > 0:
            self.state.create_fault_count -= 1
            return 503, _error(
                "SERVICE_UNAVAILABLE",
                "The service is temporarily unavailable; retry the request.",
            )
        return status, payload

    def _item_get(self, item_id):
        item = self.state.items.get(item_id)
        if item is None:
            return 404, _error("NOT_FOUND", "No item with id %r exists." % item_id)
        return 200, item


class MockServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, state):
        self.state = state
        super().__init__(address, Handler)


def start(host="127.0.0.1", port=0, **kwargs):
    """Start the mock on a background thread; returns (server, base_url)."""
    state = MockState(load_contract(), **kwargs)
    server = MockServer((host, port), state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "http://%s:%d%s" % (
        host,
        server.server_address[1],
        state.contract["server"]["base_path"],
    )
    return server, base


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--log", default="requests.jsonl")
    ap.add_argument(
        "--create-faults",
        type=int,
        default=1,
        help="how many create responses to turn into a transient 503",
    )
    args = ap.parse_args()
    server, base = start(
        args.host, args.port, log_path=args.log, create_fault_count=args.create_faults
    )
    print("mock vCenter on %s  (request log: %s)" % (base, args.log))
    print("username: %s" % DEFAULT_USERNAME)
    print("password: %s" % DEFAULT_PASSWORD)
    print("library_id: %s" % DEFAULT_LIBRARIES[0])
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
