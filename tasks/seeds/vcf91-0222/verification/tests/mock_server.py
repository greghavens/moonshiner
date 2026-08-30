#!/usr/bin/env python3
"""Contract-pinned loopback SDDC Manager and SDDC LCM used by protected verification.

Two independent 127.0.0.1 listeners share one ordered request log:

  * the SDDC Manager listener serves only the two session operations named by the
    contract (createToken, refreshAccessToken) plus the connection handshake that
    Connect-VcfSddcManagerServer performs before any contract operation runs;
  * the SDDC LCM listener serves only the three lifecycle operations named by the
    contract (setDepot, getTask, resolveDepotComponents).

Anything else answers 404 so the verifier can prove no off-contract route was used.
The access token minted by createToken stops being accepted by the SDDC LCM
listener after a fixed number of authenticated lifecycle requests, which is how the
mid-run expiry is made deterministic rather than clock-dependent.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


PINNED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
PINNED_LCM_SPEC_PATH = "specifications/sddc-lcm/sddc-lcm-openapi.yaml"
PINNED_MANAGER_SPEC_PATH = "specifications/sddc-manager/sddc-manager-openapi.json"
EXPECTED_OPERATION_IDS = [
    "createToken",
    "refreshAccessToken",
    "setDepot",
    "getTask",
    "resolveDepotComponents",
]
EXPECTED_ROUTES = [
    ("sddc-manager", "POST", "/v1/tokens"),
    ("sddc-manager", "PATCH", "/v1/tokens/access-token/refresh"),
    ("sddc-lcm", "POST", "/v1/depot"),
    ("sddc-lcm", "GET", "/v1/tasks/{taskId}"),
    ("sddc-lcm", "POST", "/v1/depot/components"),
]

# Connect-VcfSddcManagerServer probes this before any contract operation. It is not a
# contract operation and is never counted as one; it exists only so the caller-owned
# genuine SDK connection can be established against the loopback listener.
HANDSHAKE_METHOD = "GET"
HANDSHAKE_PATH = "/v1/sddc-manager"


@dataclass(frozen=True)
class Route:
    operation_id: str
    service: str
    method: str
    path: str
    pattern: re.Pattern[str]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def require_text(source: dict[str, Any], name: str) -> str:
    value = source.get(name)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"runtime scenario {name} is invalid")
    return value


def path_pattern(path: str) -> re.Pattern[str]:
    parts = []
    for segment in path.split("/"):
        if segment.startswith("{") and segment.endswith("}"):
            parts.append(r"(?P<%s>[^/]+)" % segment[1:-1])
        else:
            parts.append(re.escape(segment))
    return re.compile("^" + "/".join(parts) + "$")


def load_routes(contract_path: Path) -> list[Route]:
    contract = read_json(contract_path)
    source = contract.get("source", {})
    auth_source = contract.get("authSource", {})
    if source.get("repositoryCommitSha") != PINNED_COMMIT:
        raise RuntimeError("contract repository commit is not pinned")
    if auth_source.get("repositoryCommitSha") != PINNED_COMMIT:
        raise RuntimeError("contract auth repository commit is not pinned")
    if source.get("specPath") != PINNED_LCM_SPEC_PATH:
        raise RuntimeError("contract SDDC LCM specification path is not pinned")
    if auth_source.get("specPath") != PINNED_MANAGER_SPEC_PATH:
        raise RuntimeError("contract SDDC Manager specification path is not pinned")

    operations = contract.get("operations", [])
    if [item.get("operationId") for item in operations] != EXPECTED_OPERATION_IDS:
        raise RuntimeError("contract operation set does not match the mock")

    routes = [
        Route(
            item["operationId"],
            item["service"],
            item["method"].upper(),
            item["path"],
            path_pattern(item["path"]),
        )
        for item in operations
    ]
    if [(r.service, r.method, r.path) for r in routes] != EXPECTED_ROUTES:
        raise RuntimeError("contract route projection changed")
    return routes


class MockState:
    def __init__(
        self,
        routes: list[Route],
        request_log: Path,
        scenario: dict[str, Any],
    ) -> None:
        self.routes = routes
        self.request_log = request_log
        self.username = require_text(scenario, "username")
        self.password = require_text(scenario, "password")
        self.access_token = require_text(scenario, "accessToken")
        self.refresh_token_id = require_text(scenario, "refreshTokenId")
        self.refreshed_access_token = require_text(scenario, "refreshedAccessToken")
        self.task_id = require_text(scenario, "taskId")
        self.depot_fqdn = require_text(scenario, "depotFqdn")
        self.depot_certificate = require_text(scenario, "depotCertificate")

        lifetime = scenario.get("lifecycleCallsBeforeExpiry")
        if not isinstance(lifetime, int) or lifetime < 1:
            raise RuntimeError("runtime scenario lifecycleCallsBeforeExpiry is invalid")
        self.lifecycle_calls_before_expiry = lifetime

        resolved = scenario.get("resolvedComponentVersions")
        if not isinstance(resolved, list) or not resolved:
            raise RuntimeError("runtime scenario resolvedComponentVersions is invalid")
        self.resolved_component_versions = resolved

        self.original_token_lifecycle_calls = 0
        self.depot_registered = False
        self.sequence = 0
        self.lock = threading.Lock()
        request_log.parent.mkdir(parents=True, exist_ok=True)
        request_log.write_text("", encoding="utf-8")

    def match(self, service: str, method: str, path: str) -> tuple[Route | None, dict[str, str]]:
        for route in self.routes:
            if route.service != service or route.method != method:
                continue
            found = route.pattern.match(path)
            if found:
                return route, found.groupdict()
        return None, {}

    def record(self, entry: dict[str, Any]) -> None:
        with self.lock:
            self.sequence += 1
            entry["sequence"] = self.sequence
            with self.request_log.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(entry, separators=(",", ":"), sort_keys=True) + "\n"
                )
                stream.flush()
                os.fsync(stream.fileno())


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: MockState, service: str) -> None:
        super().__init__(address, ContractHandler)
        self.state = state
        self.service = service


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

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch()

    def _dispatch(self) -> None:
        service = self.server.service
        state = self.server.state
        target = urlsplit(self.path)
        route, path_values = state.match(service, self.command, target.path)

        try:
            body_length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            body_length = 0
        body = self.rfile.read(max(body_length, 0))

        handshake = (
            service == "sddc-manager"
            and route is None
            and self.command == HANDSHAKE_METHOD
            and target.path == HANDSHAKE_PATH
        )

        if handshake:
            status, response = 200, {"id": "protected-sddc-manager", "version": "9.1.0.0"}
        elif route is None:
            status, response = 404, error_body(
                "NOT_IN_CONTRACT", "operation is outside the focused contract"
            )
        elif route.operation_id == "createToken":
            status, response = self._create_token(target.query, body)
        elif route.operation_id == "refreshAccessToken":
            status, response = self._refresh_access_token(target.query, body)
        elif route.operation_id == "setDepot":
            status, response = self._set_depot(target.query, body)
        elif route.operation_id == "getTask":
            status, response = self._get_task(target.query, body, path_values)
        elif route.operation_id == "resolveDepotComponents":
            status, response = self._resolve_depot_components(target.query, body)
        else:  # pragma: no cover - defensive
            status, response = 404, error_body(
                "NOT_IN_CONTRACT", "operation is outside the focused contract"
            )

        header_values = {
            name.lower(): self.headers.get_all(name) or []
            for name in self.headers.keys()
        }
        state.record(
            {
                "service": service,
                "operationId": route.operation_id if route else None,
                "handshake": handshake,
                "method": self.command,
                "rawTarget": self.path,
                "path": target.path,
                "rawQuery": target.query,
                "pathValues": path_values,
                "headerValues": header_values,
                "bodyLength": len(body),
                "body": body.decode("utf-8", errors="replace"),
                "responseStatus": status,
            }
        )
        self._send_json(status, response)

    # -- shared request checks ---------------------------------------------

    def _bearer(self) -> str | None:
        value = self.headers.get("Authorization")
        if not isinstance(value, str) or not value.startswith("Bearer "):
            return None
        return value[len("Bearer ") :]

    def _json_media_type_error(self) -> tuple[int, Any] | None:
        media_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
        if media_type.lower() != "application/json":
            return 415, error_body("MEDIA_TYPE", "request must be JSON")
        return None

    @staticmethod
    def _decode(body: bytes) -> Any:
        try:
            return json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    def _lifecycle_auth_error(self) -> tuple[int, Any] | None:
        """Deterministic mid-run expiry driven by lifecycle call count."""
        state = self.server.state
        token = self._bearer()
        if token == state.refreshed_access_token:
            return None
        if token == state.access_token:
            with state.lock:
                state.original_token_lifecycle_calls += 1
                expired = (
                    state.original_token_lifecycle_calls
                    > state.lifecycle_calls_before_expiry
                )
            if not expired:
                return None
            return 401, error_body(
                "TOKEN_EXPIRED", "the access token presented has expired"
            )
        return 401, error_body("UNAUTHORIZED", "a valid bearer access token is required")

    # -- SDDC Manager session operations -----------------------------------

    def _create_token(self, raw_query: str, body: bytes) -> tuple[int, Any]:
        state = self.server.state
        if raw_query:
            return 400, error_body("WIRE_SHAPE", "query string must be absent")
        media_error = self._json_media_type_error()
        if media_error:
            return media_error
        value = self._decode(body)
        if not isinstance(value, dict):
            return 400, error_body("WIRE_SHAPE", "TokenCreationSpec must be a JSON object")
        if value.get("username") != state.username or value.get("password") != state.password:
            return 400, error_body("CREDENTIALS", "unexpected credentials")
        return 201, {
            "accessToken": state.access_token,
            "refreshToken": {"id": state.refresh_token_id},
        }

    def _refresh_access_token(self, raw_query: str, body: bytes) -> tuple[int, Any]:
        state = self.server.state
        if raw_query:
            return 400, error_body("WIRE_SHAPE", "query string must be absent")
        media_error = self._json_media_type_error()
        if media_error:
            return media_error
        value = self._decode(body)
        if not isinstance(value, str):
            return 400, error_body(
                "WIRE_SHAPE", "the refresh request body must be a bare JSON string"
            )
        if value != state.refresh_token_id:
            return 404, error_body("UNKNOWN_REFRESH_TOKEN", "refresh token is not known")
        return 200, state.refreshed_access_token

    # -- SDDC LCM lifecycle operations -------------------------------------

    def _set_depot(self, raw_query: str, body: bytes) -> tuple[int, Any]:
        state = self.server.state
        if raw_query:
            return 400, error_body("WIRE_SHAPE", "query string must be absent")
        auth_error = self._lifecycle_auth_error()
        if auth_error:
            return auth_error
        media_error = self._json_media_type_error()
        if media_error:
            return media_error
        value = self._decode(body)
        if not isinstance(value, dict):
            return 400, error_body("WIRE_SHAPE", "FleetDepotSpec must be a JSON object")
        if value.get("fqdn") != state.depot_fqdn:
            return 400, error_body("WIRE_SHAPE", "unexpected fleet depot fqdn")
        if value.get("certificate") != state.depot_certificate:
            return 400, error_body("WIRE_SHAPE", "unexpected fleet depot certificate")
        with state.lock:
            state.depot_registered = True
        return 202, {
            "id": state.task_id,
            "name": "fleet_depot_registration",
            "type": "apply",
            "status": "RUNNING",
            "resourceType": "COMPONENT",
            "retriable": True,
            "cancellable": True,
        }

    def _get_task(
        self, raw_query: str, body: bytes, path_values: dict[str, str]
    ) -> tuple[int, Any]:
        state = self.server.state
        if raw_query:
            return 400, error_body("WIRE_SHAPE", "query string must be absent")
        auth_error = self._lifecycle_auth_error()
        if auth_error:
            return auth_error
        if body:
            return 400, error_body("WIRE_SHAPE", "getTask must not carry a request body")
        if path_values.get("taskId") != state.task_id:
            return 404, error_body("NOT_FOUND", "task is not known")
        return 200, {
            "id": state.task_id,
            "name": "fleet_depot_registration",
            "type": "apply",
            "status": "SUCCEEDED",
            "resourceType": "COMPONENT",
            "retriable": False,
            "cancellable": False,
        }

    def _resolve_depot_components(self, raw_query: str, body: bytes) -> tuple[int, Any]:
        state = self.server.state
        if raw_query:
            return 400, error_body("WIRE_SHAPE", "query string must be absent")
        auth_error = self._lifecycle_auth_error()
        if auth_error:
            return auth_error
        media_error = self._json_media_type_error()
        if media_error:
            return media_error
        value = self._decode(body)
        if not isinstance(value, dict):
            return 400, error_body(
                "WIRE_SHAPE", "DepotComponentsSpec must be a JSON object"
            )
        if not state.depot_registered:
            return 409, error_body(
                "DEPOT_NOT_REGISTERED", "components were resolved before setDepot"
            )
        depot = value.get("fleetDepotSpec")
        if not isinstance(depot, dict):
            return 400, error_body("WIRE_SHAPE", "fleetDepotSpec is required")
        if (
            depot.get("fqdn") != state.depot_fqdn
            or depot.get("certificate") != state.depot_certificate
        ):
            return 400, error_body("WIRE_SHAPE", "unexpected fleet depot endpoint")
        components = value.get("componentVersions")
        if not isinstance(components, list) or not components:
            return 400, error_body("WIRE_SHAPE", "componentVersions is required")
        for item in components:
            if not isinstance(item, dict) or not isinstance(item.get("component"), str):
                return 400, error_body(
                    "WIRE_SHAPE", "each componentVersions entry requires component"
                )
        return 200, {"componentVersions": state.resolved_component_versions}

    # -- response ----------------------------------------------------------

    def _send_json(self, status: int, value: Any) -> None:
        body = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
            self.wfile.flush()


def error_body(code: str, message: str) -> dict[str, Any]:
    return {
        "code": code,
        "detail": message,
        "message": {
            "id": "com.broadcom.lcm.protected." + code.lower(),
            "defaultMessage": message,
            "localizedMessage": message,
        },
        "referenceId": "protected-reference",
    }


def write_ports(path: Path, manager_port: int, lcm_port: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        stream.write(
            json.dumps({"sddcManagerPort": manager_port, "sddcLcmPort": lcm_port})
        )
        stream.flush()
        os.fsync(stream.fileno())


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        raise SystemExit(
            "usage: mock_server.py PORT_FILE LOG_FILE CONTRACT_FILE SCENARIO_FILE"
        )
    port_file, log_file, contract_file, scenario_file = map(Path, argv[1:])
    routes = load_routes(contract_file)
    state = MockState(routes, log_file, read_json(scenario_file))

    manager = ContractServer(("127.0.0.1", 0), state, "sddc-manager")
    lcm = ContractServer(("127.0.0.1", 0), state, "sddc-lcm")
    manager_thread = threading.Thread(
        target=manager.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    manager_thread.start()
    write_ports(
        port_file, int(manager.server_address[1]), int(lcm.server_address[1])
    )
    try:
        lcm.serve_forever(poll_interval=0.05)
    finally:
        manager.shutdown()
        manager.server_close()
        lcm.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
