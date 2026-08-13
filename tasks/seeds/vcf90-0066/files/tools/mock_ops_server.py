"""Loopback mock of the five VCF Operations endpoints named in docs/contract.json.

The mock is pinned to the contract: it loads docs/contract.json at startup and
serves *only* the operations listed there, enforcing the paths, methods, required
and optional request fields and the enum vocabularies recorded in it. Anything
else answers 404.

Every request is appended to a JSON Lines request log so a test can inspect the
exact wire shape that a client produced. The log carries a monotonic sequence
number rather than a timestamp so that runs are byte-comparable.

This talks to 127.0.0.1 only. It is not a VMware product and contacts nothing.

Run standalone:

    python tools/mock_ops_server.py --port 8443 --log /tmp/requests.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT_PATH = os.path.join(REPO_ROOT, "docs", "contract.json")

# Fixed so that runs are reproducible.
MOCK_TOKEN = "mock-ops-token-0001"
MOCK_TOKEN_VALIDITY = 4102444800000
MOCK_OPERATION_ID = "24495381-716f-472d-9c14-d6138797a63c"
EXPORT_PAYLOAD = b"PK\x03\x04-vcf-operations-mock-export-content-v1"

VALID_USERNAME = "admin"
VALID_PASSWORD = "VMware1!VMware1!"
VALID_AUTH_SOURCE = "Local Users"

# State sequences handed out by successive polls of getLastExportOperation.
# The final entry repeats forever, so behaviour depends on the number of polls
# rather than on wall-clock time.
SCENARIOS = {
    "success": ["INITIALIZED", "RUNNING", "RUNNING", "FINISHED"],
    "failed": ["INITIALIZED", "RUNNING", "FAILED"],
    "stuck": ["RUNNING"],
    "immediate": ["FINISHED"],
}

# Headers that vary between HTTP client implementations and carry no contract
# meaning. They are dropped from the log so assertions stay stable.
BORING_HEADERS = frozenset(
    {"host", "user-agent", "accept-encoding", "connection", "content-length"}
)


def load_contract(path: str = CONTRACT_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


class ContractError(Exception):
    """A request that the pinned contract does not permit."""

    def __init__(self, status: int, message: str, detail: str = ""):
        super().__init__(message)
        self.status = status
        self.message = message
        self.detail = detail


class MockState:
    """Everything the handler mutates, shared across the server's threads."""

    def __init__(self, contract: dict, scenario: str, log_path: str | None):
        self.contract = contract
        self.lock = threading.Lock()
        # Keep each response and its log entry together. The client may open its
        # next connection as soon as it has read a response body; without this
        # separate lock, a later handler thread could otherwise enter dispatch
        # before the earlier handler has appended its log entry.
        self.dispatch_lock = threading.Lock()
        self.states = list(SCENARIOS[scenario])
        self.scenario = scenario
        self.log_path = log_path
        self.log: list[dict] = []
        self.seq = 0
        self.export_started = False
        self.poll_index = 0
        self.token_live = False

        ops = contract["operations"]
        # (method, path) -> operationId, built from the contract rather than
        # hardcoded, so the mock cannot drift from it.
        self.routes = {(op["method"], op["path"]): name for name, op in ops.items()}

        body = ops["exportContent"]["requestBody"]
        self.export_required = set(body["required"])
        self.export_allowed = set(body["properties"])
        self.scope_enum = set(body["properties"]["scope"]["enum"])
        self.content_type_enum = set(contract["enums"]["content-export.contentTypes[]"])

        token_body = ops["acquireToken"]["requestBody"]
        self.token_required = set(token_body["required"])
        self.token_allowed = set(token_body["properties"])

        self.auth_header = contract["security"]["headerName"]
        self.auth_value = contract["security"]["headerValueTemplate"].format(
            token=MOCK_TOKEN
        )
        self.known_9_1_only = set(
            ["LI_EXTRACTED_FIELDS", "LI_AGENT_GROUPS", "LI_TEMPLATES", "CONFIG_TEMPLATES"]
        )

    def record(self, entry: dict) -> None:
        with self.lock:
            entry["seq"] = self.seq
            self.seq += 1
            self.log.append(entry)
            if self.log_path:
                with open(self.log_path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry, sort_keys=True) + "\n")

    def current_state(self) -> str:
        with self.lock:
            if not self.export_started:
                return "NOT_INITIALIZED"
            index = min(self.poll_index, len(self.states)) - 1
            if index < 0:
                return "NOT_INITIALIZED"
            return self.states[min(index, len(self.states) - 1)]

    def next_state(self) -> str:
        with self.lock:
            if not self.export_started:
                return "NOT_INITIALIZED"
            index = min(self.poll_index, len(self.states) - 1)
            self.poll_index += 1
            return self.states[index]

    def start_export(self) -> None:
        with self.lock:
            self.export_started = True
            self.poll_index = 0


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state: MockState  # injected by make_server

    # -- plumbing ---------------------------------------------------------

    def log_message(self, fmt, *args):  # noqa: A003 - silence stderr chatter
        pass

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _interesting_headers(self) -> dict:
        return {
            key.lower(): value
            for key, value in self.headers.items()
            if key.lower() not in BORING_HEADERS
        }

    def _send(self, status: int, payload=None, content_type="application/json"):
        if payload is None:
            body = b""
        elif isinstance(payload, bytes):
            body = payload
        else:
            body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        if body:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)
        return status

    def _dispatch(self, method: str) -> None:
        with self.state.dispatch_lock:
            self._dispatch_locked(method)

    def _dispatch_locked(self, method: str) -> None:
        parsed = urlparse(self.path)
        raw_body = self._read_body()
        entry = {
            "method": method,
            "path": parsed.path,
            "query": {k: v for k, v in parse_qs(parsed.query).items()},
            "headers": self._interesting_headers(),
            "body_raw": raw_body.decode("utf-8", "replace") if raw_body else None,
        }
        try:
            entry["body"] = json.loads(raw_body) if raw_body else None
        except ValueError:
            entry["body"] = None
        entry["body_keys"] = (
            sorted(entry["body"]) if isinstance(entry["body"], dict) else None
        )

        operation_id = self.state.routes.get((method, parsed.path))
        entry["operationId"] = operation_id

        try:
            if operation_id is None:
                raise ContractError(
                    404,
                    "No such operation in the pinned contract.",
                    "%s %s is not one of: %s"
                    % (
                        method,
                        parsed.path,
                        ", ".join(sorted(self.state.routes.values())),
                    ),
                )
            status = getattr(self, "_op_" + operation_id)(entry)
        except ContractError as exc:
            status = self._send(
                exc.status,
                {
                    "message": exc.message,
                    "detail": exc.detail,
                    "contractViolation": True,
                },
            )

        entry["status"] = status
        self.state.record(entry)

    def do_GET(self):  # noqa: N802
        self._dispatch("GET")

    def do_POST(self):  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self):  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self):  # noqa: N802
        self._dispatch("DELETE")

    # -- shared checks ----------------------------------------------------

    def _require_auth(self) -> None:
        supplied = self.headers.get(self.state.auth_header)
        if not supplied:
            raise ContractError(
                401,
                "Missing %s header." % self.state.auth_header,
                "Expected %r." % self.state.auth_value,
            )
        if supplied != self.state.auth_value:
            raise ContractError(
                401,
                "Bad %s header." % self.state.auth_header,
                "Expected %r, got %r." % (self.state.auth_value, supplied),
            )
        if not self.state.token_live:
            raise ContractError(401, "Token is not valid (never acquired or released).")

    def _require_json_body(self, entry: dict) -> dict:
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if content_type != "application/json":
            raise ContractError(
                415,
                "Content-Type must be application/json.",
                "Got %r." % (content_type or "<absent>"),
            )
        if entry["body_raw"] is None:
            raise ContractError(400, "Request body is required.")
        if not isinstance(entry["body"], dict):
            raise ContractError(400, "Request body must be a JSON object.")
        return entry["body"]

    @staticmethod
    def _check_fields(body: dict, allowed: set, required: set, schema: str) -> None:
        unknown = sorted(set(body) - allowed)
        if unknown:
            raise ContractError(
                400,
                "Unknown field(s) for %s: %s." % (schema, ", ".join(unknown)),
                "The 9.0 contract allows only: %s." % ", ".join(sorted(allowed)),
            )
        empty = sorted(
            key for key, value in body.items() if value is None or value == "" or value == []
        )
        if empty:
            raise ContractError(
                400,
                "Field(s) sent empty: %s." % ", ".join(empty),
                "Optional fields must be omitted from the JSON object entirely, "
                "not sent as null, \"\" or [].",
            )
        missing = sorted(required - set(body))
        if missing:
            raise ContractError(
                400, "Missing required field(s) for %s: %s." % (schema, ", ".join(missing))
            )

    # -- operations -------------------------------------------------------

    def _op_acquireToken(self, entry: dict) -> int:  # noqa: N802
        if self.headers.get(self.state.auth_header) is not None:
            raise ContractError(
                400,
                "acquireToken must not carry an %s header." % self.state.auth_header,
                "The specification sets \"security\": [] on this operation.",
            )
        body = self._require_json_body(entry)
        self._check_fields(
            body, self.state.token_allowed, self.state.token_required, "username-password"
        )
        for key, value in body.items():
            if not isinstance(value, str):
                raise ContractError(400, "Field %r must be a string." % key)
        if body["username"] != VALID_USERNAME or body["password"] != VALID_PASSWORD:
            raise ContractError(401, "Authentication failed.")
        if "authSource" in body and body["authSource"] != VALID_AUTH_SOURCE:
            raise ContractError(
                401, "Authentication failed.", "Unknown authSource %r." % body["authSource"]
            )
        self.state.token_live = True
        return self._send(
            200,
            {
                "token": MOCK_TOKEN,
                "validity": MOCK_TOKEN_VALIDITY,
                "expiresAt": "Friday, January 01, 2100 12:00:00 AM UTC",
                "roles": ["ContentAdmin"],
            },
        )

    def _op_exportContent(self, entry: dict) -> int:  # noqa: N802
        self._require_auth()
        encryption_password = self.headers.get("EncryptionPassword")
        if encryption_password is not None and encryption_password.strip() == "":
            raise ContractError(
                400,
                "EncryptionPassword header was sent empty.",
                "It is an optional header parameter: omit it entirely when unused.",
            )
        body = self._require_json_body(entry)
        self._check_fields(
            body, self.state.export_allowed, self.state.export_required, "content-export"
        )
        if body["scope"] not in self.state.scope_enum:
            raise ContractError(
                400,
                "Unknown scope %r." % body["scope"],
                "Allowed: %s." % ", ".join(sorted(self.state.scope_enum)),
            )
        content_types = body["contentTypes"]
        if not isinstance(content_types, list) or not all(
            isinstance(item, str) for item in content_types
        ):
            raise ContractError(400, "contentTypes must be an array of strings.")
        for item in content_types:
            if item in self.state.content_type_enum:
                continue
            hint = (
                "%r was added in the 9.1.0.0 revision of this specification and does "
                "not exist at 9.0.0.0." % item
                if item in self.state.known_9_1_only
                else "%r is not a content type at 9.0.0.0." % item
            )
            raise ContractError(400, "Unknown content type %r." % item, hint)
        self.state.start_export()
        return self._send(
            202,
            {
                "scope": body["scope"],
                "contentTypes": content_types,
                "links": [
                    {
                        "href": "/suite-api/api/content/operations/export",
                        "rel": "RELATED",
                        "name": "ExportStatusCheckURL",
                        "description": "Status of the last export operation",
                    }
                ],
            },
        )

    def _op_getLastExportOperation(self, entry: dict) -> int:  # noqa: N802
        self._require_auth()
        state = self.state.next_state()
        payload = {
            "id": MOCK_OPERATION_ID,
            "type": "EXPORT",
            "state": state,
            "operationSummaries": [],
            "errorMessages": [],
            "links": [
                {
                    "href": "/suite-api/api/content/operations/export/zip",
                    "rel": "RELATED",
                    "name": "lastExportDownloadURL",
                    "description": "Download last exported content data",
                }
            ],
        }
        if state == "FAILED":
            payload["errorMessages"] = ["Content export failed on node vcfops-01."]
            payload["errorCode"] = "OPERATION_FAILED"
        if state in ("FINISHED", "FAILED"):
            payload["endTime"] = 1625238320326
        if state != "NOT_INITIALIZED":
            payload["startTime"] = 1625238290546
        return self._send(200, payload)

    def _op_download(self, entry: dict) -> int:
        self._require_auth()
        state = self.state.current_state()
        if state != "FINISHED":
            raise ContractError(
                409,
                "No completed export is available for download.",
                "The last observed operation state is %s. Poll getLastExportOperation "
                "until it reports FINISHED before calling download." % state,
            )
        return self._send(200, EXPORT_PAYLOAD, content_type="application/octet-stream")

    def _op_releaseToken(self, entry: dict) -> int:  # noqa: N802
        self._require_auth()
        self.state.token_live = False
        return self._send(200, None)


def make_server(host="127.0.0.1", port=0, scenario="success", log_path=None, contract=None):
    """Build (but do not start) a mock server bound to a loopback port."""
    if scenario not in SCENARIOS:
        raise ValueError("unknown scenario %r; choose from %s" % (scenario, sorted(SCENARIOS)))
    state = MockState(contract or load_contract(), scenario, log_path)
    handler = type("BoundHandler", (Handler,), {"state": state})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    httpd.mock_state = state
    return httpd


class RunningMock:
    """Context manager that serves the mock on a background thread."""

    def __init__(self, scenario="success", log_path=None, contract=None):
        self.httpd = make_server(scenario=scenario, log_path=log_path, contract=contract)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.port = self.httpd.server_address[1]
        self.base_url = "http://127.0.0.1:%d" % self.port
        self.state = self.httpd.mock_state

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        return False

    def requests(self, operation_id=None):
        entries = list(self.state.log)
        if operation_id is not None:
            entries = [e for e in entries if e.get("operationId") == operation_id]
        return entries


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--scenario", default="success", choices=sorted(SCENARIOS))
    parser.add_argument("--log", dest="log_path", default=None, help="JSON Lines request log")
    args = parser.parse_args()

    httpd = make_server(args.host, args.port, args.scenario, args.log_path)
    host, port = httpd.server_address[:2]
    print("mock VCF Operations on http://%s:%d  scenario=%s" % (host, port, args.scenario))
    print("states: %s" % " -> ".join(SCENARIOS[args.scenario]))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
