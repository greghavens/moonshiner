#!/usr/bin/env python3
"""Contract-pinned loopback mock for VCF Operations for Networks (vcf91-0285).

Serves only the operations named in docs/contract.json and records every
request it receives to a JSON Lines log the verifier reads back. Binds to
127.0.0.1 on an ephemeral port. No live VMware endpoint is involved.
"""

from __future__ import annotations

import argparse
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

USERNAME = "admin@local"
PASSWORD = "VMware1!VMware1!"
DETAIL_FAILURE_MODIFIED_AFTER = "1700000000001"
REPEATED_CURSOR_MODIFIED_AFTER = "1700000000002"
MISSING_APPLICATION_ID = "18230:561:999999999"

# Deliberately unsorted server order. Names differ only by case in places and
# repeat in one place, so that only an ordinal sort with an entity_id
# tie-break reproduces the expected emission order.
APPLICATIONS = [
    {"entity_id": "18230:561:271275765", "name": "web-tier", "tier_count": 3, "member_count": 41},
    {"entity_id": "18230:561:271275766", "name": "App-Prod", "tier_count": 2, "member_count": 18},
    {"entity_id": "18230:561:271275767", "name": "app-prod", "tier_count": 5, "member_count": 96},
    {"entity_id": "18230:561:271275768", "name": "APP-PROD", "tier_count": 1, "member_count": 7},
    {"entity_id": "18230:561:271275769", "name": "billing", "tier_count": 4, "member_count": 55},
    {"entity_id": "18230:561:271275770", "name": "billing", "tier_count": 6, "member_count": 12},
    {"entity_id": "18230:561:271275771", "name": "Zeta", "tier_count": 2, "member_count": 33},
    {"entity_id": "18230:561:271275772", "name": "alpha", "tier_count": 7, "member_count": 64},
]

BY_ID = {entry["entity_id"]: entry for entry in APPLICATIONS}


def encode_cursor(offset: int) -> str:
    return base64.b64encode(str(offset).encode("ascii")).decode("ascii")


def decode_cursor(value: str) -> int:
    raw = base64.b64decode(value.encode("ascii"), validate=True)
    return int(raw.decode("ascii"))


class MockState:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.lock = threading.Lock()
        self.sequence = 0
        self.tokens: dict[str, str] = {}
        self.token_counter = 0
        self.repeated_cursor_serves = 0

    def next_sequence(self) -> int:
        self.sequence += 1
        return self.sequence

    def issue_token(self) -> str:
        self.token_counter += 1
        token = f"ni-token-{self.token_counter:04d}"
        self.tokens[token] = USERNAME
        return token

    def record(self, entry: dict) -> None:
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
            handle.flush()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state: MockState = None  # type: ignore[assignment]
    contract_paths: dict = {}

    def log_message(self, *args) -> None:  # silence stderr chatter
        return

    # ---------- plumbing ----------

    def _read_body(self) -> str:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return ""
        return self.rfile.read(length).decode("utf-8", "replace")

    def _send(self, status: int, payload: dict, record: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        record["responseStatus"] = status
        with self.state.lock:
            self.state.record(record)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str, record: dict, detail_code: int = 0,
               target: list | None = None, detail_message: str | None = None) -> None:
        payload = {
            "code": status,
            "message": message,
            "details": [
                {
                    "code": detail_code or status * 10,
                    "message": detail_message or message,
                    "target": target or [],
                }
            ],
        }
        self._send(status, payload, record)

    def _auth_token(self) -> str | None:
        header = self.headers.get("Authorization")
        if not header:
            return None
        prefix = "NetworkInsight "
        if not header.startswith(prefix):
            return None
        token = header[len(prefix):].strip()
        return token if token in self.state.tokens else None

    # ---------- dispatch ----------

    def _handle(self) -> None:
        split = urlsplit(self.path)
        path = unquote(split.path)
        body = self._read_body()
        with self.state.lock:
            sequence = self.state.next_sequence()
        record = {
            "sequence": sequence,
            "method": self.command,
            "rawPath": split.path,
            "path": path,
            "rawQuery": split.query,
            "query": [list(pair) for pair in parse_qsl(split.query, keep_blank_values=True)],
            "headers": {key.lower(): value for key, value in self.headers.items()},
            "body": body,
        }

        if self.command == "POST" and path == "/api/ni/auth/token":
            record["operationId"] = "create"
            return self._op_create(record, body)
        if self.command == "GET" and path == "/api/ni/groups/applications":
            record["operationId"] = "listApplications"
            return self._op_list(record, split.query)
        if self.command == "GET" and path.startswith("/api/ni/groups/applications/"):
            record["operationId"] = "getApplicationById"
            entity_id = path[len("/api/ni/groups/applications/"):]
            return self._op_detail(record, entity_id, split.query)

        record["operationId"] = None
        self._error(404, f"No contract operation serves {self.command} {path}", record)

    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle
    do_DELETE = _handle
    do_PATCH = _handle

    # ---------- operations ----------

    def _op_create(self, record: dict, body: str) -> None:
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type != "application/json":
            return self._error(400, "create requires Content-Type application/json", record)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as error:
            return self._error(400, f"create body is not JSON: {error}", record)
        if not isinstance(payload, dict):
            return self._error(400, "create body must be a JSON object", record)

        allowed = {"username", "password", "domain"}
        unknown = sorted(set(payload) - allowed)
        if unknown:
            return self._error(400, f"UserCredential has unknown fields: {unknown}", record)
        for key in ("username", "password"):
            if not isinstance(payload.get(key), str) or not payload[key]:
                return self._error(400, f"UserCredential.{key} must be a non-empty string", record)

        if "domain" in payload:
            domain = payload["domain"]
            if not isinstance(domain, dict):
                return self._error(400, "UserCredential.domain must be an object when present", record)
            domain_unknown = sorted(set(domain) - {"domain_type", "value"})
            if domain_unknown:
                return self._error(400, f"Domain has unknown fields: {domain_unknown}", record)
            if domain.get("domain_type") not in ("LDAP", "LOCAL"):
                return self._error(400, "Domain.domain_type must be LDAP or LOCAL", record)
            if "value" in domain and not isinstance(domain["value"], str):
                return self._error(400, "Domain.value must be a string when present", record)
            if domain.get("domain_type") == "LDAP" and not domain.get("value"):
                return self._error(400, "Domain.value is required for LDAP", record)

        if payload["username"] != USERNAME or payload["password"] != PASSWORD:
            return self._error(
                401,
                "Invalid credentials",
                record,
                detail_code=1001,
                target=["username"],
                detail_message="Authentication failed for user",
            )

        with self.state.lock:
            token = self.state.issue_token()
        record["issuedToken"] = token
        self._send(200, {"token": token, "expiry": 1793491200000}, record)

    def _strict_query(self, raw_query: str, allowed: set, record: dict):
        pairs = parse_qsl(raw_query, keep_blank_values=True)
        seen: dict[str, str] = {}
        for key, value in pairs:
            if key not in allowed:
                self._error(400, f"unknown query parameter {key!r}", record)
                return None
            if value == "":
                self._error(
                    400,
                    f"query parameter {key!r} was sent with an empty value; unset "
                    f"optional parameters must be omitted",
                    record,
                )
                return None
            if key in seen:
                self._error(400, f"query parameter {key!r} was sent more than once", record)
                return None
            seen[key] = value
        return seen

    def _op_list(self, record: dict, raw_query: str) -> None:
        if self._auth_token() is None:
            return self._error(401, "Missing or invalid NetworkInsight token", record,
                               detail_code=1002, target=["Authorization"])

        params = self._strict_query(raw_query, {"size", "cursor", "modifiedAfter"}, record)
        if params is None:
            return

        size_raw = params.get("size", "10")
        try:
            size = int(size_raw)
        except ValueError:
            return self._error(400, f"size must be an integer, got {size_raw!r}", record)
        if size < 1:
            return self._error(400, "size must be positive", record)

        if "modifiedAfter" in params:
            try:
                int(params["modifiedAfter"])
            except ValueError:
                return self._error(400, "modifiedAfter must be an integer", record)

        mode = params.get("modifiedAfter")

        # Deterministic failure fixture for verifying that a non-2xx detail
        # response is surfaced as the SDK ApiError model.  The collection call
        # itself remains a valid contract response and returns one unresolved
        # entity id.
        if mode == DETAIL_FAILURE_MODIFIED_AFTER:
            if "cursor" in params:
                return self._error(400, "detail-failure fixture takes no cursor", record)
            record["servedOffset"] = 0
            return self._send(200, {
                "results": [{
                    "entity_id": MISSING_APPLICATION_ID,
                    "entity_type": "Application",
                    "entity_name": "missing-application",
                }],
                "total_count": 1,
            }, record)

        offset = 0
        if "cursor" in params:
            try:
                offset = decode_cursor(params["cursor"])
            except Exception:
                return self._error(400, f"cursor {params['cursor']!r} was not issued by this server", record)
            if offset <= 0 or offset >= len(APPLICATIONS):
                return self._error(400, f"cursor {params['cursor']!r} is out of range", record)

        window = APPLICATIONS[offset:offset + size]
        payload = {
            "results": [
                {
                    "entity_id": entry["entity_id"],
                    "entity_type": "Application",
                    "entity_name": entry["name"],
                }
                for entry in window
            ],
            "total_count": len(APPLICATIONS),
        }
        next_offset = offset + len(window)
        # The cursor field is absent on the final page.
        if next_offset < len(APPLICATIONS):
            payload["cursor"] = encode_cursor(next_offset)

        # Return the first cursor twice to prove that clients detect it before
        # re-requesting the already-served page.  A buggy third request receives
        # a finite 500 response so the protected verifier cannot hang.
        if mode == REPEATED_CURSOR_MODIFIED_AFTER and offset == 3:
            with self.state.lock:
                self.state.repeated_cursor_serves += 1
                repeated_serve = self.state.repeated_cursor_serves
            if repeated_serve > 1:
                return self._error(500, "Repeated cursor was requested again", record,
                                   detail_code=1500, target=["cursor"])
            payload["cursor"] = encode_cursor(offset)

        record["servedOffset"] = offset
        record["servedCount"] = len(window)
        record["returnedCursor"] = payload.get("cursor")
        self._send(200, payload, record)

    def _op_detail(self, record: dict, entity_id: str, raw_query: str) -> None:
        if self._auth_token() is None:
            return self._error(401, "Missing or invalid NetworkInsight token", record,
                               detail_code=1002, target=["Authorization"])
        if raw_query:
            return self._error(400, "getApplicationById takes no query parameters", record)

        record["entityId"] = entity_id
        entry = BY_ID.get(entity_id)
        if entry is None:
            return self._error(404, f"Application {entity_id!r} not found", record,
                               detail_code=1404, target=["id"])

        self._send(200, {
            "entity_id": entry["entity_id"],
            "name": entry["name"],
            "entity_type": "Application",
            "create_time": 1509410056733,
            "created_by": "admin@local",
            "last_modified_time": 0,
            "last_modified_by": "",
            "last_modified_by_service": "",
            "tier_count": entry["tier_count"],
            "member_count": entry["member_count"],
        }, record)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--port-file", required=True)
    args = parser.parse_args()

    contract = json.loads(Path(args.contract).read_text(encoding="utf-8"))
    served = {
        (definition["method"], definition["absolutePath"])
        for definition in contract["operations"].values()
    }
    expected = {
        ("POST", "/api/ni/auth/token"),
        ("GET", "/api/ni/groups/applications"),
        ("GET", "/api/ni/groups/applications/{id}"),
    }
    if served != expected:
        raise SystemExit(f"mock is pinned to a different contract: {sorted(served)}")

    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")

    Handler.state = MockState(log_path)
    Handler.contract_paths = contract["operations"]

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    port_file = Path(args.port_file)
    port_file.write_text(str(port), encoding="utf-8")
    try:
        server.serve_forever(poll_interval=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
