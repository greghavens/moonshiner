#!/usr/bin/env python3
"""Contract-pinned loopback fixture for vCenter role pagination."""

from __future__ import annotations

import argparse
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit


EXPECTED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
EXPECTED_SPEC_PATH = (
    "specifications/vsphere/openapi/automation/vcenter.yaml"
)
EXPECTED_ROUTES = [
    (
        "Vcenter.Authorization.Roles_list",
        "GET",
        "/api/vcenter/authorization/roles",
    )
]
EXPECTED_QUERY_FIELDS = [
    "is_system",
    "names",
    "privileges",
    "page_size",
    "marker",
]


class FixtureState:
    """Validated contract and runtime-only response scenario."""

    def __init__(
        self,
        contract_path: Path,
        scenario_path: Path,
        log_path: Path,
    ) -> None:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        source = contract.get("source", {})
        if (
            source.get("commitSha") != EXPECTED_COMMIT
            or source.get("specPath") != EXPECTED_SPEC_PATH
        ):
            raise RuntimeError("loopback fixture contract source is not pinned")
        routes = [
            (item.get("operationId"), item.get("method"), item.get("path"))
            for item in contract.get("operations", [])
        ]
        if routes != EXPECTED_ROUTES:
            raise RuntimeError("loopback fixture contract routes do not match")
        operation = contract["operations"][0]
        fields = [
            item.get("name")
            for item in operation.get("effectiveQueryFields", [])
        ]
        if fields != EXPECTED_QUERY_FIELDS:
            raise RuntimeError("loopback fixture query contract does not match")
        if (
            operation.get("requestBody") is not False
            or operation.get("responses", {}).get("200", {}).get("schema")
            != "Vcenter.Authorization.Roles.ListResult"
            or contract.get("securitySchemes", {})
            .get("api_key_auth", {})
            .get("name")
            != "vmware-api-session-id"
        ):
            raise RuntimeError("loopback fixture response or security changed")
        self.routes = {
            (method, path): operation_id
            for operation_id, method, path in routes
        }

        scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
        if set(scenario) != {
            "session_token",
            "page_size",
            "roles",
            "markers",
            "fault",
            "error_secret",
        }:
            raise RuntimeError("loopback scenario has an unexpected shape")
        roles = scenario["roles"]
        markers = scenario["markers"]
        page_size = scenario["page_size"]
        if (
            not isinstance(scenario["session_token"], str)
            or not scenario["session_token"]
            or isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or page_size <= 0
            or not isinstance(roles, list)
            or any(not isinstance(item, dict) for item in roles)
            or not isinstance(markers, list)
            or any(not isinstance(item, str) or not item for item in markers)
            or scenario["fault"]
            not in {
                None,
                "http_500",
                "unexpected_204",
                "repeated_marker",
                "missing_items",
            }
            or not isinstance(scenario["error_secret"], str)
            or not scenario["error_secret"]
        ):
            raise RuntimeError("loopback scenario values are invalid")
        page_count = max(1, (len(roles) + page_size - 1) // page_size)
        if len(markers) != page_count - 1:
            raise RuntimeError("loopback scenario marker count is invalid")

        self.scenario = scenario
        self.log_path = log_path
        self.log_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.reverse_next_response = True

    def append_log(self, entry: dict[str, Any]) -> None:
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        with self.log_lock:
            with self.log_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(line + "\n")
                stream.flush()
                os.fsync(stream.fileno())


class FixtureServer(ThreadingHTTPServer):
    """Threading server carrying one validated fixture state."""

    daemon_threads = True
    state: FixtureState


class Handler(BaseHTTPRequestHandler):
    """Serve only routes allow-listed by docs/contract.json."""

    server: FixtureServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def do_PUT(self) -> None:
        self._dispatch()

    def do_PATCH(self) -> None:
        self._dispatch()

    def do_DELETE(self) -> None:
        self._dispatch()

    def _dispatch(self) -> None:
        parsed = urlsplit(self.path)
        operation_id = self.server.state.routes.get(
            (self.command, parsed.path)
        )
        body = self._read_body()
        entry: dict[str, Any] = {
            "operationId": operation_id,
            "method": self.command,
            "rawTarget": self.path,
            "queryPairs": [
                [name, value]
                for name, value in parse_qsl(
                    parsed.query,
                    keep_blank_values=True,
                    strict_parsing=False,
                )
            ],
            "accept": self.headers.get("Accept"),
            "contentType": self.headers.get("Content-Type"),
            "contentLength": self.headers.get("Content-Length"),
            "sessionToken": self.headers.get("vmware-api-session-id"),
            "body": body.decode("utf-8", errors="replace"),
        }
        if operation_id == "Vcenter.Authorization.Roles_list":
            self._list_roles(entry, body)
        else:
            self._send_json(
                entry,
                404,
                {
                    "error_type": "ROUTE_NOT_IN_CONTRACT",
                    "message": "Route is not served by this fixture",
                },
            )

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        return self.rfile.read(max(length, 0))

    def _list_roles(
        self,
        entry: dict[str, Any],
        body: bytes,
    ) -> None:
        state = self.server.state
        scenario = state.scenario
        pairs = entry["queryPairs"]
        valid_query = (
            isinstance(pairs, list)
            and len(pairs) in {1, 2}
            and pairs[0][0] == "page_size"
            and (len(pairs) == 1 or pairs[1][0] == "marker")
        )
        try:
            page_size = int(pairs[0][1]) if valid_query else -1
        except (TypeError, ValueError):
            page_size = -1

        marker = pairs[1][1] if valid_query and len(pairs) == 2 else None
        markers = scenario["markers"]
        if marker is None:
            page_number = 0
        else:
            try:
                page_number = markers.index(marker) + 1
            except ValueError:
                page_number = -1

        valid = (
            valid_query
            and page_size == scenario["page_size"]
            and page_number >= 0
            and not body
            and entry["contentType"] is None
            and entry["contentLength"] is None
            and entry["accept"] == "application/json"
            and entry["sessionToken"] == scenario["session_token"]
        )
        if not valid:
            self._send_json(
                entry,
                400,
                {
                    "error_type": "BAD_ROLE_PAGE_REQUEST",
                    "message": "Collection request did not match the contract",
                },
            )
            return

        if scenario["fault"] == "http_500":
            self._send_json(
                entry,
                500,
                {
                    "error_type": "FIXTURE_INTERNAL_ERROR",
                    "secret": scenario["error_secret"],
                },
            )
            return
        if scenario["fault"] == "unexpected_204":
            self._send_empty(entry, 204)
            return

        roles = scenario["roles"]
        start = page_number * page_size
        if start >= len(roles) and roles:
            self._send_json(
                entry,
                400,
                {
                    "error_type": "MARKER_OUT_OF_RANGE",
                    "message": "Marker is outside the collection",
                },
            )
            return
        items = [
            json.loads(json.dumps(item, ensure_ascii=False))
            for item in roles[start : start + page_size]
        ]
        with state.state_lock:
            reverse = state.reverse_next_response
            state.reverse_next_response = not state.reverse_next_response
        if reverse:
            items.reverse()

        response: dict[str, Any] = {"items": items}
        if page_number < len(markers):
            response["marker"] = markers[page_number]
        if scenario["fault"] == "repeated_marker" and page_number == 1:
            response["marker"] = markers[0]
        if scenario["fault"] == "missing_items":
            response.pop("items")
        self._send_json(entry, 200, response)

    def _send_json(
        self,
        entry: dict[str, Any],
        status: int,
        payload: Any,
    ) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        entry["responseStatus"] = status
        self.server.state.append_log(entry)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_empty(self, entry: dict[str, Any], status: int) -> None:
        entry["responseStatus"] = status
        self.server.state.append_log(entry)
        self.send_response(status)
        self.end_headers()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--scenario", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--ready", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    state = FixtureState(args.contract, args.scenario, args.log)
    server = FixtureServer(("127.0.0.1", 0), Handler)
    server.state = state
    host, port = server.server_address
    args.ready.write_text(
        f"http://{host}:{port}",
        encoding="utf-8",
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
