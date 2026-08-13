#!/usr/bin/env python3
"""Contract-pinned loopback VCF Operations for Networks appliance.

Routes are derived from ``docs/contract.json`` only. Any request outside the
five projected operations is refused. Every request is appended to a JSONL log
that the protected verifier reads back.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

PINNED_COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
PINNED_SPEC_PATH = (
    "specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml"
)
EXPECTED_OPERATION_IDS = [
    "create",
    "listApplications",
    "getApplicationById",
    "addTier",
    "delete",
]
EXPECTED_ROUTES = [
    ("POST", "/auth/token"),
    ("GET", "/groups/applications"),
    ("GET", "/groups/applications/{id}"),
    ("POST", "/groups/applications/{id}/tiers"),
    ("DELETE", "/auth/token"),
]
UNAUTHENTICATED_OPERATION_IDS = {"create"}


@dataclass(frozen=True)
class Route:
    operation_id: str
    method: str
    path_template: str
    pattern: re.Pattern[str]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def route_pattern(template: str) -> re.Pattern[str]:
    pieces: list[str] = []
    cursor = 0
    for match in re.finditer(r"\{[^{}]+\}", template):
        pieces.append(re.escape(template[cursor : match.start()]))
        pieces.append(r"([^/]+)")
        cursor = match.end()
    pieces.append(re.escape(template[cursor:]))
    return re.compile("^" + "".join(pieces) + "$")


def load_routes(contract_path: Path) -> tuple[list[Route], str]:
    contract = read_json(contract_path)
    source = contract.get("source", {})
    if source.get("repositoryCommitSha") != PINNED_COMMIT:
        raise RuntimeError("contract repository commit is not pinned")
    if source.get("specPath") != PINNED_SPEC_PATH:
        raise RuntimeError("contract specification path is not pinned")
    base_path = source.get("serverBasePath")
    if not isinstance(base_path, str) or not base_path.startswith("/"):
        raise RuntimeError("contract server base path is invalid")
    operations = contract.get("operations", [])
    if [item.get("operationId") for item in operations] != EXPECTED_OPERATION_IDS:
        raise RuntimeError("contract operation set does not match the mock")
    if [
        (item.get("method"), item.get("path")) for item in operations
    ] != EXPECTED_ROUTES:
        raise RuntimeError("contract route projection changed")
    routes = [
        Route(
            item["operationId"],
            item["method"].upper(),
            item["path"],
            route_pattern(base_path + item["path"]),
        )
        for item in operations
    ]
    return routes, base_path


def require_text(source: dict[str, Any], name: str) -> str:
    value = source.get(name)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"scenario {name} is invalid")
    return value


def error_body(code: int, message: str) -> dict[str, Any]:
    return {"code": code, "message": message}


def encode_cursor(offset: int) -> str:
    return base64.b64encode(str(offset).encode("ascii")).decode("ascii")


def decode_cursor(cursor: str) -> int | None:
    try:
        return int(base64.b64decode(cursor.encode("ascii"), validate=True))
    except (ValueError, UnicodeDecodeError):
        return None


class MockState:
    """Appliance state: issued tokens, saved applications and created tiers."""

    def __init__(
        self, routes: list[Route], request_log: Path, scenario: dict[str, Any]
    ) -> None:
        self.routes = routes
        self.request_log = request_log
        self.lock = threading.Lock()
        self.handler_lock = threading.RLock()
        self.sequence = 0

        self.mode = require_text(scenario, "mode")
        if self.mode not in {"expire_after_tiers", "always_expired"}:
            raise RuntimeError("scenario mode is unsupported")

        credentials = scenario.get("credentials")
        if not isinstance(credentials, dict):
            raise RuntimeError("scenario credentials are invalid")
        self.username = require_text(credentials, "username")
        self.password = require_text(credentials, "password")

        mintable = scenario.get("mintableTokens")
        if not isinstance(mintable, list) or len(mintable) < 2:
            raise RuntimeError("scenario must pre-mint at least two tokens")
        if len({str(item) for item in mintable}) != len(mintable):
            raise RuntimeError("scenario tokens must be unique")
        self.mintable_tokens = [str(item) for item in mintable]
        self.token_expiries = scenario.get("tokenExpiries")
        if (
            not isinstance(self.token_expiries, list)
            or len(self.token_expiries) != len(self.mintable_tokens)
        ):
            raise RuntimeError("scenario token expiries are invalid")

        applications = scenario.get("applications")
        if not isinstance(applications, list) or not applications:
            raise RuntimeError("scenario applications are invalid")
        self.applications: list[dict[str, Any]] = []
        for item in applications:
            if not isinstance(item, dict):
                raise RuntimeError("scenario application is invalid")
            self.applications.append(
                {
                    "entity_id": require_text(item, "entity_id"),
                    "name": require_text(item, "name"),
                    "created_by": require_text(item, "created_by"),
                    "create_time": int(item["create_time"]),
                }
            )
        if len({item["entity_id"] for item in self.applications}) != len(
            self.applications
        ):
            raise RuntimeError("scenario application ids must be unique")
        self.by_entity_id = {item["entity_id"]: item for item in self.applications}

        self.expire_after_tier_count = int(scenario.get("expireAfterTierCount", 0))
        self.tier_id_prefix = require_text(scenario, "tierIdPrefix")

        self.forced_error_operation: str | None = None
        self.forced_error_status: int | None = None
        self.forced_error_code: Any = None
        self.forced_error_message = ""
        forced_error = scenario.get("forcedError")
        if forced_error is not None:
            if not isinstance(forced_error, dict):
                raise RuntimeError("scenario forcedError is invalid")
            operation_id = require_text(forced_error, "operationId")
            if operation_id not in EXPECTED_OPERATION_IDS:
                raise RuntimeError("scenario forcedError operation is invalid")
            status = int(forced_error.get("status", 0))
            if status < 400 or status > 599:
                raise RuntimeError("scenario forcedError status is invalid")
            self.forced_error_operation = operation_id
            self.forced_error_status = status
            self.forced_error_code = forced_error.get("code")
            self.forced_error_message = require_text(forced_error, "message")

        self.issued_tokens: list[str] = []
        self.deleted_tokens: set[str] = set()
        self.created_tiers: list[dict[str, str]] = []

        request_log.parent.mkdir(parents=True, exist_ok=True)
        request_log.write_text("", encoding="utf-8")

    # -- token lifecycle -------------------------------------------------

    def mint_token(self) -> tuple[str, int]:
        index = len(self.issued_tokens)
        if index >= len(self.mintable_tokens):
            raise RuntimeError("the client requested more tokens than the scenario mints")
        token = self.mintable_tokens[index]
        self.issued_tokens.append(token)
        return token, int(self.token_expiries[index])

    def token_index(self, token: str | None) -> int | None:
        if token is None:
            return None
        try:
            return self.issued_tokens.index(token)
        except ValueError:
            return None

    def token_is_valid(self, token: str | None) -> bool:
        index = self.token_index(token)
        if index is None or token in self.deleted_tokens:
            return False
        if self.mode == "always_expired":
            return False
        # The first token the appliance hands out lapses once the run has
        # committed `expireAfterTierCount` tiers; later tokens outlive the run.
        if index == 0 and len(self.created_tiers) >= self.expire_after_tier_count:
            return False
        return True

    # -- request log -----------------------------------------------------

    def record(self, entry: dict[str, Any]) -> None:
        with self.lock:
            self.sequence += 1
            entry = {"sequence": self.sequence, **entry}
            with self.request_log.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, separators=(",", ":"), sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())

    def match(self, method: str, path: str) -> tuple[Route, tuple[str, ...]] | None:
        for route in self.routes:
            if route.method != method:
                continue
            found = route.pattern.match(path)
            if found:
                return route, tuple(unquote(item) for item in found.groups())
        return None


class MockHandler(BaseHTTPRequestHandler):
    server_version = "VcfOperationsForNetworksMock/9.1"
    protocol_version = "HTTP/1.1"

    @property
    def state(self) -> MockState:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, *_args: Any) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch()

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch()

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch()

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch()

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch()

    # -- dispatch --------------------------------------------------------

    def _dispatch(self) -> None:
        target = urlsplit(self.path)
        try:
            body_length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            body_length = 0
        body = self.rfile.read(max(body_length, 0))

        matched = self.state.match(self.command, target.path)
        route: Route | None = None
        captures: tuple[str, ...] = ()
        if matched is not None:
            route, captures = matched

        bearer = self._presented_token()
        with self.state.handler_lock:
            status, response = self._handle(route, captures, target.query, body, bearer)

        header_values = {
            name.lower(): self.headers.get_all(name) or []
            for name in self.headers.keys()
        }
        self.state.record(
            {
                "operationId": route.operation_id if route else None,
                "method": self.command,
                "rawTarget": self.path,
                "path": target.path,
                "rawQuery": target.query,
                "query": parse_qs(target.query, keep_blank_values=True),
                "headerValues": header_values,
                "bodyLength": len(body),
                "body": body.decode("utf-8", errors="replace"),
                "responseStatus": status,
                "presentedTokenIndex": self.state.token_index(bearer),
                "presentedAuthorization": bearer is not None,
            }
        )
        self._send_json(status, response)

    def _presented_token(self) -> str | None:
        values = self.headers.get_all("Authorization") or []
        if len(values) != 1:
            return None
        raw = values[0]
        if not raw.startswith("NetworkInsight "):
            return None
        return raw[len("NetworkInsight ") :]

    def _handle(
        self,
        route: Route | None,
        captures: tuple[str, ...],
        query: str,
        body: bytes,
        bearer: str | None,
    ) -> tuple[int, Any]:
        if route is None:
            return 404, error_body(404, "operation is outside the focused contract")
        if not self._accept_is_json():
            return 400, error_body(400, "Accept header must request application/json")

        authorization_present = bool(self.headers.get_all("Authorization"))
        if route.operation_id in UNAUTHENTICATED_OPERATION_IDS:
            if authorization_present:
                return 400, error_body(
                    400, "this operation has an empty security list and takes no Authorization header"
                )
        elif not self.state.token_is_valid(bearer):
            return 401, error_body(401, "auth token is missing, invalid or expired")

        if route.operation_id == self.state.forced_error_operation:
            return self.state.forced_error_status or 500, {
                "code": self.state.forced_error_code,
                "message": self.state.forced_error_message,
            }

        if route.operation_id == "create":
            return self._create_token(query, body)
        if route.operation_id == "delete":
            return self._delete_token(query, body, bearer)
        if route.operation_id == "listApplications":
            return self._list_applications(query, body)
        if route.operation_id == "getApplicationById":
            return self._get_application(captures, query, body)
        if route.operation_id == "addTier":
            return self._add_tier(captures, query, body)
        return 404, error_body(404, "unknown operation")

    def _accept_is_json(self) -> bool:
        values = self.headers.get_all("Accept") or []
        return values == ["application/json"]

    def _content_type_is_json(self) -> bool:
        values = self.headers.get_all("Content-Type") or []
        return values == ["application/json"]

    def _json_body(self, body: bytes) -> Any:
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None

    # -- operations ------------------------------------------------------

    def _create_token(self, query: str, body: bytes) -> tuple[int, Any]:
        if query:
            return 400, error_body(400, "this operation defines no query parameters")
        if not self._content_type_is_json():
            return 400, error_body(400, "Content-Type must be application/json")
        payload = self._json_body(body)
        if not isinstance(payload, dict):
            return 400, error_body(400, "request body is not a JSON object")
        if (
            payload.get("username") != self.state.username
            or payload.get("password") != self.state.password
        ):
            return 401, error_body(401, "invalid user credentials")
        domain = payload.get("domain")
        if domain is not None and not isinstance(domain, dict):
            return 400, error_body(400, "domain must be an object when supplied")
        try:
            token, expiry = self.state.mint_token()
        except RuntimeError as error:
            return 429, error_body(429, str(error))
        return 200, {"token": token, "expiry": expiry}

    def _delete_token(self, query: str, body: bytes, bearer: str | None) -> tuple[int, Any]:
        if query:
            return 400, error_body(400, "this operation defines no query parameters")
        if body:
            return 400, error_body(400, "this operation defines no request body")
        if bearer is not None:
            self.state.deleted_tokens.add(bearer)
        return 204, None

    def _list_applications(self, query: str, body: bytes) -> tuple[int, Any]:
        if body:
            return 400, error_body(400, "this operation defines no request body")
        params = parse_qs(query, keep_blank_values=True)
        unknown = set(params) - {"size", "cursor", "modifiedAfter"}
        if unknown:
            return 400, error_body(400, f"unknown query parameters: {sorted(unknown)}")
        if "size" not in params or len(params["size"]) != 1:
            return 400, error_body(400, "size must be supplied exactly once")
        try:
            size = int(params["size"][0])
        except ValueError:
            return 400, error_body(400, "size is not an integer")
        if size < 1:
            return 400, error_body(400, "size must be positive")

        offset = 0
        if "cursor" in params:
            if len(params["cursor"]) != 1 or not params["cursor"][0]:
                return 400, error_body(400, "cursor must be a single non-empty value")
            decoded = decode_cursor(params["cursor"][0])
            if decoded is None or not 0 < decoded <= len(self.state.applications):
                return 400, error_body(400, "cursor is not one this appliance issued")
            offset = decoded

        window = self.state.applications[offset : offset + size]
        # The specification's PagedListResponse carries EntityId entries; this
        # appliance does not populate entity_name, so names need the detail call.
        payload: dict[str, Any] = {
            "results": [
                {"entity_id": item["entity_id"], "entity_type": "Application"}
                for item in window
            ]
        }
        following = offset + len(window)
        if following < len(self.state.applications):
            payload["cursor"] = encode_cursor(following)
        payload["total_count"] = len(self.state.applications)
        return 200, payload

    def _get_application(
        self, captures: tuple[str, ...], query: str, body: bytes
    ) -> tuple[int, Any]:
        if body:
            return 400, error_body(400, "this operation defines no request body")
        params = parse_qs(query, keep_blank_values=True)
        unknown = set(params) - {"fetch_member_counts", "fetch_update_status"}
        if unknown:
            return 400, error_body(400, f"unknown query parameters: {sorted(unknown)}")
        application = self.state.by_entity_id.get(captures[0])
        if application is None:
            return 404, error_body(404, "application not found")
        return 200, {
            "entity_id": application["entity_id"],
            "name": application["name"],
            "entity_type": "Application",
            "create_time": application["create_time"],
            "created_by": application["created_by"],
            "last_modified_time": 0,
            "last_modified_by": "",
            "last_modified_by_service": "",
        }

    def _add_tier(
        self, captures: tuple[str, ...], query: str, body: bytes
    ) -> tuple[int, Any]:
        if query:
            return 400, error_body(400, "this operation defines no query parameters")
        if not self._content_type_is_json():
            return 400, error_body(400, "Content-Type must be application/json")
        application_id = captures[0]
        if application_id not in self.state.by_entity_id:
            return 404, error_body(404, "application not found")
        payload = self._json_body(body)
        if not isinstance(payload, dict):
            return 400, error_body(400, "request body is not a JSON object")
        name = payload.get("name")
        if not isinstance(name, str) or not name:
            return 400, error_body(400, "tier name is required")
        for existing in self.state.created_tiers:
            if existing["application_id"] == application_id and existing["name"] == name:
                return 409, error_body(
                    409, f"tier '{name}' already exists in this application"
                )
        tier_id = f"{self.state.tier_id_prefix}{len(self.state.created_tiers) + 1}"
        self.state.created_tiers.append(
            {"application_id": application_id, "name": name, "entity_id": tier_id}
        )
        response: dict[str, Any] = {
            "entity_id": tier_id,
            "name": name,
            "entity_type": "Tier",
        }
        if "group_membership_criteria" in payload:
            response["group_membership_criteria"] = payload["group_membership_criteria"]
        if "member_list" in payload:
            response["member_list"] = payload["member_list"]
        response["application"] = {
            "entity_id": application_id,
            "entity_type": "Application",
        }
        return 201, response

    # -- response --------------------------------------------------------

    def _send_json(self, status: int, value: Any) -> None:
        if status == 204 or value is None:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class MockServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], state: MockState) -> None:
        super().__init__(address, MockHandler)
        self.state = state


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: mock_server.py <contract.json> <scenario.json> <request-log>", file=sys.stderr)
        return 2
    contract_path = Path(sys.argv[1]).resolve()
    scenario_path = Path(sys.argv[2]).resolve()
    log_path = Path(sys.argv[3]).resolve()

    routes, _base_path = load_routes(contract_path)
    state = MockState(routes, log_path, read_json(scenario_path))
    server = MockServer(("127.0.0.1", 0), state)

    # The parent reads this line to learn the ephemeral port.
    print(json.dumps({"port": server.server_address[1]}), flush=True)
    try:
        server.serve_forever(poll_interval=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
