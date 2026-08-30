#!/usr/bin/env python3
"""Contract-pinned loopback SDDC Manager used by protected verification."""

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
    "getClusters",
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
        self.flip_counts = {"getDomains": 0, "getClusters": 0}
        self.lock = threading.Lock()
        self.domains = self._make_domains()
        by_name = sorted(self.domains, key=lambda item: (item["name"], item["id"]))
        self.expiry_domain_id = by_name[1]["id"]
        request_log.parent.mkdir(parents=True, exist_ok=True)
        request_log.write_text("", encoding="utf-8")

    @staticmethod
    def _make_domains() -> list[dict[str, Any]]:
        marker = secrets.token_hex(4)
        definitions = [
            (
                f"Zulu-Compute-{marker}",
                "VI",
                [(f"alpha-Compute-{marker}", False, False),
                 (f"Gamma-Compute-{marker}", True, True)],
            ),
            (
                f"Alpha-Management-{marker}",
                "MANAGEMENT",
                [(f"zeta-Management-{marker}", False, False),
                 (f"Beta-Management-{marker}", True, False)],
            ),
            (
                f"Bravo-Edge-{marker}",
                "VI",
                [(f"omega-Edge-{marker}", False, True),
                 (f"Delta-Edge-{marker}", True, False)],
            ),
        ]
        result: list[dict[str, Any]] = []
        for domain_name, domain_type, cluster_defs in definitions:
            domain_id = str(uuid.uuid4())
            clusters: list[dict[str, Any]] = []
            for cluster_name, is_default, is_stretched in cluster_defs:
                clusters.append(
                    {
                        "id": str(uuid.uuid4()),
                        "domain": {
                            "id": domain_id,
                            "name": domain_name,
                            "type": domain_type,
                        },
                        "name": cluster_name,
                        "status": "ACTIVE",
                        "isDefault": is_default,
                        "isStretched": is_stretched,
                    }
                )
            result.append(
                {
                    "id": domain_id,
                    "name": domain_name,
                    "status": "ACTIVE",
                    "type": domain_type,
                    "isManagementSsoDomain": domain_type == "MANAGEMENT",
                    "clusters": clusters,
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

    def flip(self, operation_id: str, elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with self.lock:
            self.flip_counts[operation_id] += 1
            reverse = self.flip_counts[operation_id] % 2 == 1
        return list(reversed(elements)) if reverse else list(elements)

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
        if operation_id == "getClusters":
            return self._get_clusters(query, body)
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
        if self.headers.get("Authorization") != (
            "Bearer " + self.server.state.old_access_token
        ):
            return 401, self._error("UNAUTHORIZED", "Unexpected bearer token", "auth")
        with self.server.state.lock:
            if self.server.state.refreshed:
                return 400, self._error(
                    "ALREADY_REFRESHED", "Only one refresh is allowed", "repeat-refresh"
                )
            self.server.state.refreshed = True
        return 200, self.server.state.new_access_token

    def _validate_collection_request(
        self,
        query: str,
        body: bytes,
        allowed: set[str],
    ) -> tuple[dict[str, list[str]] | None, tuple[int, Any] | None]:
        if body:
            return None, (400, self._error(
                "BODY_NOT_ALLOWED", "Collection GET must be bodyless", "body"
            ))
        parsed = parse_qs(query, keep_blank_values=True)
        if not set(parsed).issubset(allowed):
            return None, (400, self._error(
                "UNSET_OPTIONAL_PRESENT", "Unexpected query parameter", "query"
            ))
        if any(len(values) != 1 or values[0] == "" for values in parsed.values()):
            return None, (400, self._error(
                "EMPTY_QUERY_VALUE", "Empty or repeated query value", "query"
            ))
        if parsed.get("pageSize") != ["2"]:
            return None, (400, self._error(
                "INVALID_PAGE_SIZE", "The focused scenario requires pageSize 2", "paging"
            ))
        return parsed, None

    def _authorized_for_collection(self) -> bool:
        authorization = self.headers.get("Authorization")
        if self.server.state.refreshed:
            return authorization == "Bearer " + self.server.state.new_access_token
        return authorization == "Bearer " + self.server.state.old_access_token

    def _get_domains(self, query: str, body: bytes) -> tuple[int, Any]:
        parsed, failure = self._validate_collection_request(
            query, body, {"pageNumber", "pageSize"}
        )
        if failure is not None:
            return failure
        assert parsed is not None
        if not self._authorized_for_collection():
            return 401, self._error("UNAUTHORIZED", "Bearer token required", "auth")
        try:
            page_number = int(parsed.get("pageNumber", ["1"])[0])
        except ValueError:
            page_number = -1
        if page_number not in (1, 2):
            return 400, self._error(
                "INVALID_PAGE_NUMBER", "Unexpected domain page number", "paging"
            )
        start = (page_number - 1) * 2
        elements = [
            {key: value for key, value in item.items() if key != "clusters"}
            for item in self.server.state.domains[start : start + 2]
        ]
        elements = self.server.state.flip("getDomains", elements)
        return 200, {
            "elements": elements,
            "pageMetadata": {
                "pageNumber": page_number,
                "pageSize": 2,
                "totalElements": len(self.server.state.domains),
                "totalPages": 2,
            },
        }

    def _get_clusters(self, query: str, body: bytes) -> tuple[int, Any]:
        parsed, failure = self._validate_collection_request(
            query, body, {"domainId", "pageNumber", "pageSize"}
        )
        if failure is not None:
            return failure
        assert parsed is not None
        domain_ids = parsed.get("domainId")
        if domain_ids is None:
            return 400, self._error(
                "DOMAIN_REQUIRED", "The scenario requires domainId", "domain"
            )
        domain = next(
            (item for item in self.server.state.domains if item["id"] == domain_ids[0]),
            None,
        )
        if domain is None:
            return 400, self._error("UNKNOWN_DOMAIN", "Unknown domain", "domain")
        try:
            page_number = int(parsed.get("pageNumber", ["1"])[0])
        except ValueError:
            page_number = -1
        if page_number != 1:
            return 400, self._error(
                "INVALID_PAGE_NUMBER", "Unexpected cluster page number", "paging"
            )

        authorization = self.headers.get("Authorization")
        old = "Bearer " + self.server.state.old_access_token
        new = "Bearer " + self.server.state.new_access_token
        if not self.server.state.refreshed:
            if authorization != old:
                return 401, self._error("UNAUTHORIZED", "Original token required", "auth")
            if domain["id"] == self.server.state.expiry_domain_id:
                with self.server.state.lock:
                    first_expiry = not self.server.state.expiry_sent
                    self.server.state.expiry_sent = True
                if first_expiry:
                    return 401, self._error(
                        "ACCESS_TOKEN_EXPIRED", "Access token expired", "expired"
                    )
                return 401, self._error("UNAUTHORIZED", "Access token is stale", "auth")
        elif authorization != new:
            return 401, self._error("UNAUTHORIZED", "Replacement token required", "auth")

        elements = self.server.state.flip("getClusters", domain["clusters"])
        return 200, {
            "elements": elements,
            "pageMetadata": {
                "pageNumber": 1,
                "pageSize": 2,
                "totalElements": len(domain["clusters"]),
                "totalPages": 1,
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
        "expiryDomainId": state.expiry_domain_id,
        "domains": state.domains,
    }
    write_atomic(runtime_info, json.dumps(info, separators=(",", ":")))
    write_atomic(port_file, str(server.server_address[1]))
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
