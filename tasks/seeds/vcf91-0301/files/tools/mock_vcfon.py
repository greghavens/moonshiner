#!/usr/bin/env python3
"""Loopback mock of the VCF Operations for Networks 9.1 endpoints named in
docs/contract.json.

It serves exactly three operations - `create`, `bulkDataSourceOperation` and
`getBulkOperationDetails` - and nothing else. Any other path answers 404 and is
recorded as unmatched.

The bulk operation is genuinely asynchronous: the submit answers 202 with only a
request id, and `getBulkOperationDetails` reports partial counts that advance on
each poll until success_count + failed_count == total_count. A client that treats
the 202, or the first report, as completion gets the wrong answer.

Every request is appended to a JSON Lines log so a test can assert the exact wire
shape that was sent.

Run standalone while developing:

    python3 tools/mock_vcfon.py --port 8080 --log /tmp/vcfon-requests.jsonl
"""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = "/api/ni"

PATH_AUTH_TOKEN = BASE + "/auth/token"
PATH_BULK = BASE + "/data-sources/bulk"
PATH_VIEW_DETAILS_PREFIX = BASE + "/data-sources/bulk/view-details/"

# --- fixture ---------------------------------------------------------------

TOKENS = [
    "NI-9Qm2vX9pLd0aTgW4uZ=",
    "NI-4Hs7bN3kQe1xRt6yPo=",
    "NI-7Kp5dR8sYu2wFj9cLm=",
]
TOKEN_EXPIRY = 1780000000000
REQUEST_ID = "bulk-req-3f9c1a"

ENTITY_A = "18230:963:993642895"
ENTITY_B = "18230:963:993642896"
ENTITY_C = "18230:963:993642897"
ENTITY_D = "18230:963:993642898"

FAIL_REASON = "Credentials could not be validated for the data source."

# Report returned for the Nth poll of REQUEST_ID (1-indexed). The last entry is the
# terminal state and is repeated for any further poll.
POLL_REPORTS = [
    {
        # A newly accepted operation may not have populated its total yet. All
        # counts being zero is not terminal because the contract also requires
        # total_count > 0.
        "total_count": 0,
        "success_count": 0,
        "failed_count": 0,
        "successful_data_sources": [],
        "failed_data_sources": [],
    },
    {
        "total_count": 4,
        "success_count": 1,
        "failed_count": 0,
        "successful_data_sources": [ENTITY_A],
        "failed_data_sources": [],
    },
    {
        "total_count": 4,
        "success_count": 3,
        "failed_count": 0,
        "successful_data_sources": [ENTITY_A, ENTITY_B, ENTITY_C],
        "failed_data_sources": [],
    },
    {
        "total_count": 4,
        "success_count": 3,
        "failed_count": 1,
        "successful_data_sources": [ENTITY_A, ENTITY_B, ENTITY_C],
        "failed_data_sources": [{"entity_id": ENTITY_D, "reason": FAIL_REASON}],
    },
]

TERMINAL_REPORT = POLL_REPORTS[-1]
POLLS_TO_TERMINAL = len(POLL_REPORTS)

# Headers captured into the request log.
LOGGED_HEADERS = ("authorization", "content-type", "accept", "host")


class _State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.seq = 0
        self.auth_calls = 0
        self.issued_tokens: list[str] = []
        self.poll_count = 0
        self.submitted = False


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "VCFOpsForNetworksMock/9.1"
    sys_version = ""

    # -- plumbing ----------------------------------------------------------

    def log_message(self, fmt, *args):  # silence stderr access log
        pass

    def _read_body(self) -> bytes:
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            chunks = []
            while True:
                size_line = self.rfile.readline().strip()
                size = int(size_line.split(b";")[0], 16)
                if size == 0:
                    self.rfile.readline()
                    break
                chunks.append(self.rfile.read(size))
                self.rfile.readline()
            return b"".join(chunks)
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _respond(self, status: int, payload) -> None:
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        if body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _log(self, method: str, raw_path: str, body: bytes, matched, status: int) -> None:
        state = self.server.state
        try:
            parsed = json.loads(body.decode("utf-8")) if body else None
            parse_error = None
        except Exception as exc:  # pragma: no cover - defensive
            parsed = None
            parse_error = str(exc)

        path, _, query = raw_path.partition("?")
        entry = {
            "method": method,
            "path": path,
            "query": query,
            "matched_operation_id": matched,
            "status": status,
            "headers": {
                name: self.headers.get(name)
                for name in LOGGED_HEADERS
                if self.headers.get(name) is not None
            },
            "body_raw": body.decode("utf-8", "replace") if body else None,
            "body_json": parsed,
            "body_parse_error": parse_error,
        }
        with state.lock:
            state.seq += 1
            entry["seq"] = state.seq
            with open(self.server.log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")

    def _bearer_ok(self) -> bool:
        header = self.headers.get("Authorization")
        if not header:
            return False
        with self.server.state.lock:
            issued = list(self.server.state.issued_tokens)
        return any(header == "NetworkInsight " + tok for tok in issued)

    # -- routes ------------------------------------------------------------

    def do_POST(self):  # noqa: N802
        body = self._read_body()
        path = self.path.partition("?")[0]

        if path == PATH_AUTH_TOKEN:
            status, payload, matched = self._op_create(body)
        elif path == PATH_BULK:
            status, payload, matched = self._op_bulk_submit(body)
        else:
            status, payload, matched = 404, {"message": "not found: " + path}, None

        self._log("POST", self.path, body, matched, status)
        self._respond(status, payload)

    def do_GET(self):  # noqa: N802
        path = self.path.partition("?")[0]

        if path.startswith(PATH_VIEW_DETAILS_PREFIX):
            status, payload, matched = self._op_view_details(path)
        else:
            status, payload, matched = 404, {"message": "not found: " + path}, None

        self._log("GET", self.path, b"", matched, status)
        self._respond(status, payload)

    def do_PUT(self):  # noqa: N802
        self._unsupported("PUT")

    def do_PATCH(self):  # noqa: N802
        self._unsupported("PATCH")

    def do_DELETE(self):  # noqa: N802
        self._unsupported("DELETE")

    def do_HEAD(self):  # noqa: N802
        self._unsupported("HEAD")

    def _unsupported(self, method: str) -> None:
        body = self._read_body()
        self._log(method, self.path, body, None, 404)
        self._respond(404, {"message": "not found: " + method + " " + self.path})

    # -- operations --------------------------------------------------------

    def _op_create(self, body: bytes):
        """operationId `create` - POST /api/ni/auth/token."""
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            return 400, {"message": "malformed request body"}, "create"
        if not isinstance(payload, dict):
            return 400, {"message": "expected a JSON object"}, "create"
        if not payload.get("username") or not payload.get("password"):
            return 401, {"message": "invalid credentials"}, "create"

        state = self.server.state
        with state.lock:
            index = min(state.auth_calls, len(TOKENS) - 1)
            state.auth_calls += 1
            token = TOKENS[index]
            if token not in state.issued_tokens:
                state.issued_tokens.append(token)
        return 200, {"token": token, "expiry": TOKEN_EXPIRY}, "create"

    def _op_bulk_submit(self, body: bytes):
        """operationId `bulkDataSourceOperation` - POST /api/ni/data-sources/bulk."""
        op = "bulkDataSourceOperation"
        if not self._bearer_ok():
            return 401, {"message": "missing or invalid auth token"}, op
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            return 400, {"message": "malformed request body"}, op
        if not isinstance(payload, dict):
            return 400, {"message": "expected a JSON object"}, op
        if not payload.get("action_type"):
            return 400, {"message": "action_type is required"}, op
        sources = payload.get("data_sources")
        if not isinstance(sources, list) or not sources:
            return 400, {"message": "data_sources is required"}, op

        with self.server.state.lock:
            self.server.state.submitted = True
        # 202 Submitted: accepted, not finished. Only the request id comes back.
        return 202, {"request_id": REQUEST_ID}, op

    def _op_view_details(self, path: str):
        """operationId `getBulkOperationDetails` -
        GET /api/ni/data-sources/bulk/view-details/{request_id}."""
        op = "getBulkOperationDetails"
        if not self._bearer_ok():
            return 401, {"message": "missing or invalid auth token"}, op
        request_id = path[len(PATH_VIEW_DETAILS_PREFIX):]
        if request_id != REQUEST_ID:
            return 404, {"message": "unknown request id: " + request_id}, op

        state = self.server.state
        with state.lock:
            if not state.submitted:
                return 404, {"message": "unknown request id: " + request_id}, op
            state.poll_count += 1
            index = 0 if self.server.never_terminal else min(state.poll_count, len(POLL_REPORTS)) - 1
        return 200, dict(POLL_REPORTS[index]), op


class MockServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, log_path: str, never_terminal: bool = False):
        super().__init__(address, _Handler)
        self.log_path = log_path
        self.never_terminal = never_terminal
        self.state = _State()


def start_server(
    log_path: str,
    host: str = "127.0.0.1",
    port: int = 0,
    never_terminal: bool = False,
):
    """Start the mock on a background thread. Returns (server, thread, port)."""
    open(log_path, "w", encoding="utf-8").close()
    server = MockServer((host, port), log_path, never_terminal=never_terminal)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, server.server_address[1]


def read_log(log_path: str):
    """Read the request log as a list of entries ordered by arrival."""
    entries = []
    with open(log_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    entries.sort(key=lambda e: e["seq"])
    return entries


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--log", default="/tmp/vcfon-requests.jsonl")
    args = ap.parse_args()

    server, _thread, port = start_server(args.log, args.host, args.port)
    print(f"VCFON_BASE_URL=http://{args.host}:{port}", flush=True)
    print(f"request log: {args.log}", flush=True)
    try:
        while True:
            _thread.join(1.0)
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
