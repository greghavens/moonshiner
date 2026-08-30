#!/usr/bin/env python3
"""Contract-pinned loopback SDDC Manager used only by protected verification.

The server derives its HTTP routing table from docs/contract.json, exposes no
control endpoint, and appends every request and response status to a JSONL log.
"""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit


EXPECTED_ROUTES = {
    ("POST", "/v1/tokens"): "createToken",
    ("PATCH", "/v1/tokens/access-token/refresh"): "refreshAccessToken",
    ("GET", "/v1/domains"): "getDomains",
    ("GET", "/v1/hosts"): "getHosts",
}

DUMMY_USERNAME = "svc-inventory"
DUMMY_PASSWORD = "fixture-password"
INITIAL_ACCESS_TOKEN = "fixture-access-1"
REFRESH_TOKEN_ID = "fixture-refresh-1"
REFRESHED_ACCESS_TOKEN = "fixture-access-2"

DOMAINS = [
    {
        "id": "domain-b",
        "name": "Bravo-Compute",
        "type": "VI",
        "status": "ACTIVE",
    },
    {
        "id": "domain-a",
        "name": "Management",
        "type": "MANAGEMENT",
        "status": "ACTIVE",
    },
    {
        "id": "domain-c",
        "name": "Analytics",
        "type": "VI",
        "status": "EXPANDING",
    },
]

HOSTS = [
    {
        "id": "host-c",
        "fqdn": "esx03.example.test",
        "status": "ASSIGNED",
        "domain": {"id": "domain-b"},
    },
    {
        "id": "host-a",
        "fqdn": "esx01.example.test",
        "status": "ASSIGNED",
        "domain": {"id": "domain-a"},
    },
    {
        "id": "host-d",
        "fqdn": "esx04.example.test",
        "status": "UNASSIGNED_USEABLE",
        "domain": {"id": "domain-c"},
    },
    {
        "id": "host-b",
        "fqdn": "esx02.example.test",
        "status": "ASSIGNED",
        "domain": {"id": "domain-b"},
    },
]


def load_routes(contract_path: Path) -> dict[tuple[str, str], str]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    routes = {
        (operation["method"], operation["path"]): operation["operationId"]
        for operation in contract["operations"]
    }
    if routes != EXPECTED_ROUTES:
        raise RuntimeError(
            "contract must name exactly createToken, refreshAccessToken, "
            "getDomains, and getHosts at their pinned methods and paths"
        )
    return routes


class FixtureState:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.lock = threading.Lock()
        self.initial_token_budget = 3
        self.refresh_used = False

    def authorize(self, authorization: str | None) -> bool:
        with self.lock:
            if authorization == f"Bearer {INITIAL_ACCESS_TOKEN}":
                if self.initial_token_budget == 0:
                    return False
                self.initial_token_budget -= 1
                return True
            return authorization == f"Bearer {REFRESHED_ACCESS_TOKEN}"

    def refresh(self, token_id: object) -> bool:
        with self.lock:
            if token_id != REFRESH_TOKEN_ID or self.refresh_used:
                return False
            self.refresh_used = True
            return True

    def record(self, entry: dict[str, object]) -> None:
        line = json.dumps(entry, separators=(",", ":"), ensure_ascii=False)
        with self.lock:
            with self.log_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(line + "\n")
                stream.flush()


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        routes: dict[tuple[str, str], str],
        log_path: Path,
    ) -> None:
        self.routes = routes
        self.state = FixtureState(log_path)
        super().__init__(address, ContractHandler)


class ContractHandler(BaseHTTPRequestHandler):
    server: ContractServer
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def do_PATCH(self) -> None:
        self._dispatch()

    def do_PUT(self) -> None:
        self._dispatch()

    def do_DELETE(self) -> None:
        self._dispatch()

    def _dispatch(self) -> None:
        split = urlsplit(self.path)
        body = self._read_body()
        operation_id = self.server.routes.get((self.command, split.path))
        entry: dict[str, object] = {
            "operationId": operation_id,
            "method": self.command,
            "rawTarget": self.path,
            "path": split.path,
            "queryPairs": [list(pair) for pair in parse_qsl(
                split.query, keep_blank_values=True
            )],
            "accept": self.headers.get("Accept"),
            "authorization": self.headers.get("Authorization"),
            "contentType": self.headers.get("Content-Type"),
            "body": body.decode("utf-8", errors="replace"),
        }

        if operation_id == "createToken":
            status, payload, extra_headers = self._create_token(body)
        elif operation_id == "refreshAccessToken":
            status, payload, extra_headers = self._refresh_access_token(body)
        elif operation_id in {"getDomains", "getHosts"}:
            status, payload, extra_headers = self._get_collection(
                operation_id, split.query, body
            )
        else:
            status, payload, extra_headers = (
                404,
                {
                    "errorCode": "NOT_FOUND",
                    "message": "The loopback fixture serves only contract operations.",
                },
                {},
            )

        entry["responseStatus"] = status
        self.server.state.record(entry)
        self._send_json(status, payload, extra_headers)

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        return self.rfile.read(length) if length > 0 else b""

    def _has_json_content_type(self) -> bool:
        media_type = self.headers.get("Content-Type", "").split(";", 1)[0]
        return media_type.strip().lower() == "application/json"

    @staticmethod
    def _decode_json(body: bytes) -> object:
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    def _create_token(
        self, body: bytes
    ) -> tuple[int, object, dict[str, str]]:
        payload = self._decode_json(body)
        if (
            not self._has_json_content_type()
            or payload
            != {
                "username": DUMMY_USERNAME,
                "password": DUMMY_PASSWORD,
            }
            or self.headers.get("Authorization") is not None
        ):
            return (
                400,
                {
                    "errorCode": "INVALID_TOKEN_SPEC",
                    "message": "The token creation wire shape is invalid.",
                },
                {},
            )
        return (
            201,
            {
                "accessToken": INITIAL_ACCESS_TOKEN,
                "refreshToken": {"id": REFRESH_TOKEN_ID},
            },
            {},
        )

    def _refresh_access_token(
        self, body: bytes
    ) -> tuple[int, object, dict[str, str]]:
        payload = self._decode_json(body)
        if (
            not self._has_json_content_type()
            or self.headers.get("Authorization") is not None
            or not self.server.state.refresh(payload)
        ):
            return (
                401,
                {
                    "errorCode": "INVALID_REFRESH_TOKEN",
                    "message": "The refresh token request is invalid.",
                },
                {},
            )
        return 200, REFRESHED_ACCESS_TOKEN, {}

    def _get_collection(
        self,
        operation_id: str,
        raw_query: str,
        body: bytes,
    ) -> tuple[int, object, dict[str, str]]:
        if body:
            return (
                400,
                {
                    "errorCode": "UNEXPECTED_BODY",
                    "message": "Collection GET requests cannot have a body.",
                },
                {},
            )
        if not self.server.state.authorize(self.headers.get("Authorization")):
            return (
                401,
                {
                    "errorCode": "ACCESS_TOKEN_EXPIRED",
                    "message": "The access token has expired.",
                },
                {"WWW-Authenticate": 'Bearer error="invalid_token"'},
            )

        pairs = parse_qsl(raw_query, keep_blank_values=True)
        names = [name for name, _value in pairs]
        allowed = (
            {"pageSize", "pageNumber", "type"}
            if operation_id == "getDomains"
            else {"pageSize", "pageNumber", "status"}
        )
        if (
            any(name not in allowed for name in names)
            or len(names) != len(set(names))
        ):
            return (
                400,
                {
                    "errorCode": "INVALID_QUERY",
                    "message": "The collection query is not valid for the fixture.",
                },
                {},
            )
        query = dict(pairs)
        try:
            page_size = int(query.get("pageSize", "20"))
            page_number = int(query.get("pageNumber", "0"))
        except ValueError:
            page_size = 0
            page_number = -1
        if page_size <= 0 or page_number < 0:
            return (
                400,
                {
                    "errorCode": "INVALID_PAGE",
                    "message": "Pagination values must be positive.",
                },
                {},
            )

        if operation_id == "getDomains":
            source = list(DOMAINS)
            if "type" in query:
                source = [item for item in source if item["type"] == query["type"]]
        else:
            source = list(HOSTS)
            if "status" in query:
                source = [
                    item for item in source if item["status"] == query["status"]
                ]

        start = page_number * page_size
        elements = source[start : start + page_size]
        total = len(source)
        total_pages = (total + page_size - 1) // page_size
        return (
            200,
            {
                "elements": elements,
                "pageMetadata": {
                    "pageNumber": page_number,
                    "pageSize": page_size,
                    "totalElements": total,
                    "totalPages": total_pages,
                },
            },
            {},
        )

    def _send_json(
        self,
        status: int,
        payload: object,
        headers: dict[str, str],
    ) -> None:
        data = json.dumps(
            payload, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--port-file", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    routes = load_routes(args.contract)
    server = ContractServer(("127.0.0.1", 0), routes, args.log)
    args.port_file.write_text(
        str(server.server_address[1]) + "\n", encoding="ascii"
    )
    try:
        server.serve_forever(poll_interval=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
