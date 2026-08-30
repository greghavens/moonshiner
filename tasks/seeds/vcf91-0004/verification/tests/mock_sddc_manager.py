#!/usr/bin/env python3
"""Contract-pinned loopback SDDC Manager for protected verification."""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
PINNED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
PINNED_SPEC_PATH = "specifications/sddc-manager/sddc-manager-openapi.json"
EXPECTED_OPERATION_IDS = {"createToken", "updateDepotSettings"}


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
    routes = [Route.from_contract(item) for item in operations]
    return routes, contract


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
        self.username = "sdk-" + secrets.token_hex(7)
        self.password = "pw-" + secrets.token_urlsafe(15)
        self.access_token = "at-" + secrets.token_urlsafe(20)
        self.refresh_token = "rt-" + secrets.token_urlsafe(18)
        self.download_token = "dl-" + secrets.token_hex(12)
        self.desired_canonical: bytes | None = None
        self.effect_count = 0
        self.put_count = 0
        self.sequence = 0
        self.lock = threading.Lock()
        request_log.parent.mkdir(parents=True, exist_ok=True)
        request_log.write_text("", encoding="utf-8")

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

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch()

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch()

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch()

    def _dispatch(self) -> None:
        target = urlsplit(self.path)
        route, parameters = self.server.state.match(self.command, target.path)
        version_probe = self.command == "GET" and target.path == "/v1/sddc-manager"
        try:
            body_length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            body_length = 0
        body = self.rfile.read(body_length)

        if target.query:
            status, response = 400, self._error(
                "QUERY_NOT_ALLOWED", "A query string is not allowed", "query"
            )
        elif version_probe:
            status, response = self._version_probe(body)
        elif route is None:
            status, response = 404, self._error(
                "NOT_FOUND", "Operation is outside the focused contract", "route"
            )
        else:
            status, response = self._handle(route.operation_id, parameters, body)

        headers = {
            name.lower(): value.strip() for name, value in self.headers.items()
        }
        self.server.state.record(
            {
                "operationId": route.operation_id if route else None,
                "method": self.command,
                "rawTarget": self.path,
                "path": target.path,
                "rawQuery": target.query,
                "headers": headers,
                "authorization": self.headers.get("Authorization") or "",
                "contentType": self.headers.get("Content-Type") or "",
                "bodyLength": len(body),
                "body": body.decode("utf-8", errors="replace"),
                "responseStatus": status,
                "mutationEffectCount": self.server.state.effect_count,
            }
        )
        self._send_json(status, response)

    def _handle(
        self, operation_id: str, _parameters: dict[str, str], body: bytes
    ) -> tuple[int, Any]:
        if operation_id == "createToken":
            return self._create_token(body)
        if operation_id == "updateDepotSettings":
            return self._update_depot(body)
        return 500, self._error("HANDLER_MISSING", "Handler missing", "handler")

    def _create_token(self, body: bytes) -> tuple[int, Any]:
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None
        expected = {
            "username": self.server.state.username,
            "password": self.server.state.password,
        }
        if payload != expected or self.headers.get("Authorization") is not None:
            return 400, self._error(
                "INVALID_CREDENTIALS", "Invalid loopback credentials", "auth"
            )
        return 201, {
            "accessToken": self.server.state.access_token,
            "refreshToken": {"id": self.server.state.refresh_token},
        }

    def _version_probe(self, body: bytes) -> tuple[int, Any]:
        if not self._authorized():
            return 401, self._error("UNAUTHORIZED", "Bearer token required", "auth")
        if body:
            return 400, self._error(
                "BODY_NOT_ALLOWED", "Version probe body is not allowed", "body"
            )
        return 200, {"version": self.server.state.contract["derived_from"]["info_version"]}

    def _update_depot(self, body: bytes) -> tuple[int, Any]:
        if not self._authorized():
            return 401, self._error("UNAUTHORIZED", "Bearer token required", "auth")
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return 400, self._error("INVALID_JSON", "Malformed JSON", "json")

        account_schema = self.server.state.contract["schemas"]["DepotAccount"]
        allowed_account = set(account_schema["properties"])
        if not isinstance(payload, dict) or set(payload) != {"vmwareAccount"}:
            return 400, self._error(
                "WIRE_SHAPE_MISMATCH", "Unexpected top-level members", "shape"
            )
        account = payload.get("vmwareAccount")
        if not isinstance(account, dict) or not set(account).issubset(allowed_account):
            return 400, self._error(
                "WIRE_SHAPE_MISMATCH", "Unexpected account members", "shape"
            )
        if account.get("downloadToken") != self.server.state.download_token:
            return 400, self._error(
                "WIRE_SHAPE_MISMATCH", "Download token is missing", "shape"
            )
        if any(value in (None, "", [], {}) for value in account.values()):
            return 400, self._error(
                "WIRE_SHAPE_MISMATCH", "Empty account values are not allowed", "shape"
            )

        canonical = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        with self.server.state.lock:
            if canonical != self.server.state.desired_canonical:
                self.server.state.desired_canonical = canonical
                self.server.state.effect_count += 1
            self.server.state.put_count += 1
            put_number = self.server.state.put_count

        if put_number == 1:
            return 500, self._error(
                "DEPOT_UPDATE_TRANSIENT",
                "Desired state was applied before a transient failure",
                "transient",
            )
        return 202, payload

    def _authorized(self) -> bool:
        expected = f"Bearer {self.server.state.access_token}"
        return self.headers.get("Authorization") == expected

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
        "accessToken": state.access_token,
        "downloadToken": state.download_token,
    }
    write_atomic(runtime_info, json.dumps(info, separators=(",", ":")))
    write_atomic(port_file, str(server.server_address[1]))
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
