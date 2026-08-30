"""Hermetic loopback server for the pinned VCF Operations for Logs contract."""

from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import parse_qsl, unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
CONTRACT_SHA256 = "9904b504d3193f7eeaf642f5e8443a0fd678c7a5eccf1a487c60449e23b46d3a"

EVENTS = [
    {
        "text": "alpha service ready",
        "timestamp": 1700000001000,
        "fields": [{"name": "source", "content": "node-a"}],
    },
    {
        "text": "bravo collector connected",
        "timestamp": 1700000002000,
        "fields": [{"name": "source", "content": "node-b"}],
    },
    {
        "text": "charlie index rotated",
        "timestamp": 1700000003000,
        "fields": [{"name": "source", "content": "node-c"}],
    },
    {
        "text": "delta archive complete",
        "timestamp": 1700000004000,
        "fields": [{"name": "source", "content": "node-d"}],
    },
    {
        "text": "echo health check passed",
        "timestamp": 1700000005000,
        "fields": [{"name": "source", "content": "node-e"}],
    },
]

TIED_EVENTS = [
    {"text": "alpha", "timestamp": 1700000001000},
    {"text": "zulu", "timestamp": 1700000002000},
    {"text": "bravo", "timestamp": 1700000002000},
    {"text": "delta", "timestamp": 1700000003000},
    {"text": "echo", "timestamp": 1700000004000},
]


def _load_contract():
    raw = CONTRACT_PATH.read_bytes()
    actual_hash = hashlib.sha256(raw).hexdigest()
    if actual_hash != CONTRACT_SHA256:
        raise AssertionError(
            f"contract hash mismatch: expected {CONTRACT_SHA256}, got {actual_hash}"
        )
    contract = json.loads(raw)
    operations = contract["operations"]
    if set(operations) != {"POST_sessions", "GET_events-+path"}:
        raise AssertionError("mock contract must name exactly the two supported operations")
    if (operations["POST_sessions"]["method"], operations["POST_sessions"]["path"]) != (
        "POST",
        "/sessions",
    ):
        raise AssertionError("POST_sessions contract changed")
    if (
        operations["GET_events-+path"]["method"],
        operations["GET_events-+path"]["path"],
    ) != ("GET", "/events/{+path}"):
        raise AssertionError("GET_events-+path contract changed")
    return contract


class MockEndpoint:
    def __init__(self, server):
        self._server = server
        self.base_url = f"http://127.0.0.1:{server.server_address[1]}"

    @property
    def request_log(self):
        return self._server.request_log


def _handler(contract, incomplete_cursor, events, provider):
    server_path = contract["server_path"]
    sessions_path = server_path + contract["operations"]["POST_sessions"]["path"]
    events_prefix = server_path + "/events/"
    allowed_query_names = {
        item["name"]
        for item in contract["operations"]["GET_events-+path"]["parameters"]
        if item["in"] == "query"
    }

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format, *_args):
            return

        def _record(self, body):
            record = {
                "method": self.command,
                "raw_path": self.path,
                "headers": {key.lower(): value for key, value in self.headers.items()},
                "body": body,
            }
            self.server.request_log.append(record)
            return record

        def _send_json(self, status, payload):
            raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            self._record(body)
            if self.path != sessions_path:
                self._send_json(404, {"error": "operation not served"})
                return
            try:
                payload = json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON"})
                return
            if payload != {
                "username": "analyst",
                "password": "fixture-secret",
                "provider": provider,
            }:
                self._send_json(401, {"error": "invalid fixture credentials"})
                return
            self._send_json(
                200,
                {
                    "userId": "00000000-0000-0000-0000-000000000001",
                    "sessionId": "fixture-session-token",
                    "ttl": 1800,
                },
            )

        def do_GET(self):
            self._record(b"")
            split = urlsplit(self.path)
            if not split.path.startswith(events_prefix):
                self._send_json(404, {"error": "operation not served"})
                return
            if self.headers.get("Authorization") != "Bearer fixture-session-token":
                self._send_json(401, {"error": "missing fixture session"})
                return

            query_pairs = parse_qsl(split.query, keep_blank_values=True)
            if any(name not in allowed_query_names for name, _value in query_pairs):
                self._send_json(400, {"error": "query field is outside the contract"})
                return
            query = {}
            for name, value in query_pairs:
                query.setdefault(name, []).append(value)
            if "limit" not in query or query.get("order-by-direction") != ["ASC"]:
                self._send_json(400, {"error": "ascending page shape required"})
                return
            try:
                limit = int(query["limit"][0])
                constraint = unquote(split.path[len(events_prefix) :])
                prefix = "timestamp/GT "
                if not constraint.startswith(prefix):
                    raise ValueError
                cursor = int(constraint[len(prefix) :])
            except (ValueError, IndexError):
                self._send_json(400, {"error": "invalid timestamp page constraint"})
                return
            if limit < 1:
                self._send_json(400, {"error": "limit must be positive"})
                return

            page = [event for event in events if event["timestamp"] > cursor][:limit]
            complete = incomplete_cursor is None or cursor != incomplete_cursor
            payload = {"complete": complete, "duration": 1, "events": page}
            if not complete:
                payload["warnings"] = [{"id": 128, "details": "fixture timeout"}]
            self._send_json(200, payload)

    return Handler


@contextmanager
def run_mock(*, incomplete_cursor=None, events=None, provider="Local"):
    """Run the contract-pinned server on an ephemeral IPv4 loopback port."""

    contract = _load_contract()
    if events is None:
        events = EVENTS
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), _handler(contract, incomplete_cursor, events, provider)
    )
    server.request_log = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield MockEndpoint(server)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
