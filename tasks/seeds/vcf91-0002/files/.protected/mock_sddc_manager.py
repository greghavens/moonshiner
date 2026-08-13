#!/usr/bin/env python3
"""Contract-pinned loopback SDDC Manager for protected verification."""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
import threading
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
PINNED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
PINNED_SPEC_PATH = "specifications/sddc-manager/sddc-manager-openapi.json"
EXPECTED_OPERATION_IDS = {
    "createToken",
    "refreshAccessToken",
    "getDomains",
}


@dataclass(frozen=True)
class Route:
    operation_id: str
    method: str
    path_template: str
    pattern: re.Pattern[str]

    @staticmethod
    def from_contract(operation: dict[str, Any]) -> "Route":
        template = operation["path"]
        pieces: list[str] = []
        cursor = 0
        for match in re.finditer(r"\{([A-Za-z][A-Za-z0-9]*)\}", template):
            pieces.append(re.escape(template[cursor : match.start()]))
            pieces.append(f"(?P<{match.group(1)}>[^/]+)")
            cursor = match.end()
        pieces.append(re.escape(template[cursor:]))
        return Route(
            operation_id=operation["operationId"],
            method=operation["method"].upper(),
            path_template=template,
            pattern=re.compile("^" + "".join(pieces) + "$"),
        )


def load_contract() -> tuple[list[Route], dict[str, Any]]:
    contract = json.loads(
        (ROOT / "docs" / "contract.json").read_text(encoding="utf-8")
    )
    source = contract.get("derived_from", {})
    if source.get("repository_commit_sha") != PINNED_COMMIT:
        raise RuntimeError("contract commit does not match the pinned source")
    if source.get("spec_path") != PINNED_SPEC_PATH:
        raise RuntimeError("contract specification path is not pinned")
    operations = contract.get("operations", [])
    operation_ids = {item.get("operationId") for item in operations}
    if operation_ids != EXPECTED_OPERATION_IDS:
        raise RuntimeError("contract operation set does not match the mock")
    return [Route.from_contract(item) for item in operations], contract


class MockState:
    def __init__(
        self,
        routes: list[Route],
        contract: dict[str, Any],
        request_log: Path,
    ) -> None:
        self.routes = routes
        self.contract = contract
        self.request_log = request_log
        self.username = "svc-" + secrets.token_hex(8)
        self.password = "pw-" + secrets.token_urlsafe(18)
        self.old_access_token = "at-old-" + secrets.token_urlsafe(20)
        self.new_access_token = "at-new-" + secrets.token_urlsafe(20)
        self.refresh_token_id = "rt-" + secrets.token_urlsafe(20)
        self.refreshed = False
        self.expiry_sent = False
        self.sequence = 0
        self.lock = threading.Lock()
        self.domains = self._make_domains()
        request_log.parent.mkdir(parents=True, exist_ok=True)
        request_log.write_text("", encoding="utf-8")

    @staticmethod
    def _make_domains() -> list[dict[str, Any]]:
        marker = secrets.token_hex(4)
        names = [
            f"Zulu-Compute-{marker}",
            f"Alpha-Management-{marker}",
            f"Bravo-Edge-{marker}",
            f"alpha-Analytics-{marker}",
            f"Bravo-Edge-{marker}",
        ]
        types = ["VI", "MANAGEMENT", "VI", "VI", "VI"]
        result: list[dict[str, Any]] = []
        for index, (name, domain_type) in enumerate(zip(names, types, strict=True)):
            result.append(
                {
                    "id": str(uuid.uuid4()),
                    "name": name,
                    "orgName": "org-" + secrets.token_hex(3),
                    "status": "ACTIVE",
                    "type": domain_type,
                    "isManagementSsoDomain": index == 1,
                    "clusters": [],
                }
            )
        return result

    def match(self, method: str, path: str) -> tuple[Route | None, dict[str, str]]:
        for route in self.routes:
            if route.method != method:
                continue
            match = route.pattern.fullmatch(path)
            if match:
                return route, {
                    key: unquote(value) for key, value in match.groupdict().items()
                }
        return None, {}

    def record(self, record: dict[str, Any]) -> None:
        with self.lock:
            self.sequence += 1
            record["sequence"] = self.sequence
            with self.request_log.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n"
                )
                stream.flush()
                os.fsync(stream.fileno())


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: MockState) -> None:
        super().__init__(address, ContractHandler)
        self.state = state


class ContractHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: ContractServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch()

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch()

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch()

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch()

    def _dispatch(self) -> None:
        target = urlsplit(self.path)
        route, parameters = self.server.state.match(self.command, target.path)
        bootstrap = self.command == "GET" and target.path == "/v1/sddc-manager"
        try:
            body_length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            body_length = 0
        body = self.rfile.read(body_length)

        if bootstrap:
            status, response = self._bootstrap(target.query, body)
        elif route is None:
            status, response = 404, self._error(
                "NOT_IN_CONTRACT", "Operation is outside the focused contract", "route"
            )
        else:
            status, response = self._handle(
                route.operation_id, parameters, target.query, body
            )

        header_values = {
            name.lower(): self.headers.get_all(name) or []
            for name in self.headers.keys()
        }
        self.server.state.record(
            {
                "operationId": route.operation_id if route else None,
                "sdkBootstrap": bootstrap,
                "method": self.command,
                "rawTarget": self.path,
                "path": target.path,
                "rawQuery": target.query,
                "query": parse_qs(target.query, keep_blank_values=True),
                "headerValues": header_values,
                "authorization": self.headers.get("Authorization") or "",
                "contentType": self.headers.get("Content-Type") or "",
                "accept": self.headers.get("Accept") or "",
                "bodyLength": len(body),
                "body": body.decode("utf-8", errors="replace"),
                "responseStatus": status,
            }
        )
        self._send_json(status, response)

    def _handle(
        self,
        operation_id: str,
        _parameters: dict[str, str],
        query: str,
        body: bytes,
    ) -> tuple[int, Any]:
        if operation_id == "createToken":
            return self._create_token(query, body)
        if operation_id == "refreshAccessToken":
            return self._refresh_access_token(query, body)
        if operation_id == "getDomains":
            return self._get_domains(query, body)
        return 500, self._error("HANDLER_MISSING", "Handler missing", "handler")

    def _create_token(self, query: str, body: bytes) -> tuple[int, Any]:
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None
        expected = {
            "username": self.server.state.username,
            "password": self.server.state.password,
        }
        if query or payload != expected:
            return 400, self._error(
                "INVALID_TOKEN_REQUEST", "Token request does not match", "create"
            )
        if self.headers.get("Authorization") is not None:
            return 400, self._error(
                "UNEXPECTED_AUTHORIZATION", "Create token must be unauthenticated", "auth"
            )
        if not self._json_content_type():
            return 400, self._error(
                "INVALID_CONTENT_TYPE", "JSON content type required", "content-type"
            )
        return 201, {
            "accessToken": self.server.state.old_access_token,
            "refreshToken": {"id": self.server.state.refresh_token_id},
        }

    def _bootstrap(self, query: str, body: bytes) -> tuple[int, Any]:
        if query or body:
            return 400, self._error(
                "INVALID_BOOTSTRAP", "SDK bootstrap must be bodyless", "bootstrap"
            )
        if self.headers.get("Authorization") != (
            "Bearer " + self.server.state.old_access_token
        ):
            return 401, self._error("UNAUTHORIZED", "Bearer token required", "auth")
        return 200, {"version": self.server.state.contract["derived_from"]["info_version"]}

    def _refresh_access_token(self, query: str, body: bytes) -> tuple[int, Any]:
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None
        if query or payload != self.server.state.refresh_token_id:
            return 400, self._error(
                "INVALID_REFRESH_TOKEN", "Refresh token id is invalid", "refresh"
            )
        if not self._json_content_type():
            return 400, self._error(
                "INVALID_CONTENT_TYPE", "JSON content type required", "content-type"
            )
        authorization = self.headers.get("Authorization")
        if authorization != "Bearer " + self.server.state.old_access_token:
            return 401, self._error("UNAUTHORIZED", "Unexpected bearer token", "auth")
        with self.server.state.lock:
            if self.server.state.refreshed:
                return 400, self._error(
                    "ALREADY_REFRESHED", "Only one refresh is allowed", "repeat-refresh"
                )
            self.server.state.refreshed = True
        return 200, self.server.state.new_access_token

    def _get_domains(self, query: str, body: bytes) -> tuple[int, Any]:
        if body:
            return 400, self._error(
                "BODY_NOT_ALLOWED", "getDomains must be bodyless", "body"
            )
        parsed = parse_qs(query, keep_blank_values=True)
        allowed = {"pageNumber", "pageSize"}
        if not set(parsed).issubset(allowed):
            return 400, self._error(
                "UNSET_OPTIONAL_PRESENT", "Unexpected query parameter", "query"
            )
        if any(len(values) != 1 or values[0] == "" for values in parsed.values()):
            return 400, self._error(
                "EMPTY_QUERY_VALUE", "Empty or repeated query value", "query"
            )
        if parsed.get("pageSize") != ["2"]:
            return 400, self._error(
                "INVALID_PAGE_SIZE", "The focused scenario requires pageSize 2", "paging"
            )
        if "pageNumber" not in parsed:
            page_number = 1
        else:
            try:
                page_number = int(parsed["pageNumber"][0])
            except ValueError:
                page_number = -1
        if page_number not in (1, 2, 3):
            return 400, self._error(
                "INVALID_PAGE_NUMBER", "Unexpected page number", "paging"
            )

        authorization = self.headers.get("Authorization")
        old = "Bearer " + self.server.state.old_access_token
        new = "Bearer " + self.server.state.new_access_token
        if page_number == 1:
            if authorization != old:
                return 401, self._error("UNAUTHORIZED", "Original token required", "auth")
        elif authorization == old and page_number == 2:
            with self.server.state.lock:
                first_expiry = not self.server.state.expiry_sent
                self.server.state.expiry_sent = True
            if first_expiry:
                return 401, self._error(
                    "ACCESS_TOKEN_EXPIRED", "Access token expired", "expired"
                )
            return 401, self._error("UNAUTHORIZED", "Access token is stale", "auth")
        elif authorization != new or not self.server.state.refreshed:
            return 401, self._error("UNAUTHORIZED", "Replacement token required", "auth")

        start = (page_number - 1) * 2
        elements = self.server.state.domains[start : start + 2]
        return 200, {
            "elements": elements,
            "pageMetadata": {
                "pageNumber": page_number,
                "pageSize": 2,
                "totalElements": len(self.server.state.domains),
                "totalPages": 3,
            },
        }

    def _json_content_type(self) -> bool:
        return (self.headers.get("Content-Type") or "").lower().startswith(
            "application/json"
        )

    @staticmethod
    def _error(code: str, message: str, marker: str) -> dict[str, str]:
        return {
            "errorCode": code,
            "message": message,
            "referenceToken": "loopback-" + marker,
        }

    def _send_json(self, status: int, payload: Any) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded)


def write_atomic(path: Path, value: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    if len(sys.argv) != 4:
        print(
            "usage: mock_sddc_manager.py PORT_FILE REQUEST_LOG RUNTIME_INFO",
            file=sys.stderr,
        )
        return 2
    port_file = Path(sys.argv[1])
    request_log = Path(sys.argv[2])
    runtime_info = Path(sys.argv[3])
    routes, contract = load_contract()
    state = MockState(routes, contract, request_log)
    server = ContractServer(("127.0.0.1", 0), state)
    info = {
        "username": state.username,
        "password": state.password,
        "oldAccessToken": state.old_access_token,
        "newAccessToken": state.new_access_token,
        "refreshTokenId": state.refresh_token_id,
        "domains": state.domains,
    }
    write_atomic(runtime_info, json.dumps(info, separators=(",", ":")))
    write_atomic(port_file, str(server.server_address[1]))
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
