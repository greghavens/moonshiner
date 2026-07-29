#!/usr/bin/env python3
"""Contract-pinned loopback fixture for the VCF domain snapshot task."""

from __future__ import annotations

import argparse
import json
import math
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit


EXPECTED_ROUTES = [
    ("createToken", "POST", "/v1/tokens"),
    (
        "refreshAccessToken",
        "PATCH",
        "/v1/tokens/access-token/refresh",
    ),
    ("getDomains", "GET", "/v1/domains"),
]


class FixtureState:
    def __init__(
        self,
        contract_path: Path,
        scenario_path: Path,
        log_path: Path,
    ) -> None:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        routes = [
            (item.get("operationId"), item.get("method"), item.get("path"))
            for item in contract.get("operations", [])
        ]
        if routes != EXPECTED_ROUTES:
            raise RuntimeError("loopback fixture contract routes do not match")
        self.routes = {
            (method, path): operation_id
            for operation_id, method, path in routes
        }

        scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
        required = {
            "username",
            "password",
            "first_access_token",
            "refresh_token_id",
            "second_access_token",
            "page_size",
            "domains",
        }
        if set(scenario) != required:
            raise RuntimeError("loopback scenario has an unexpected shape")
        if not isinstance(scenario["domains"], list):
            raise RuntimeError("loopback scenario domains must be a list")
        self.scenario = scenario
        self.log_path = log_path
        self.log_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.create_count = 0
        self.refresh_count = 0
        self.first_token_expired = False
        self.reverse_next_page = True

    def append_log(self, entry: dict[str, Any]) -> None:
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        with self.log_lock:
            with self.log_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(line + "\n")


class FixtureServer(ThreadingHTTPServer):
    state: FixtureState


class Handler(BaseHTTPRequestHandler):
    server: FixtureServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_POST(self) -> None:
        self._dispatch()

    def do_PATCH(self) -> None:
        self._dispatch()

    def do_GET(self) -> None:
        self._dispatch()

    def do_PUT(self) -> None:
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
                )
            ],
            "accept": self.headers.get("Accept"),
            "contentType": self.headers.get("Content-Type"),
            "authorization": self.headers.get("Authorization"),
            "body": body.decode("utf-8", errors="replace"),
        }

        if operation_id == "createToken":
            self._create_token(entry, parsed, body)
        elif operation_id == "refreshAccessToken":
            self._refresh_access_token(entry, parsed, body)
        elif operation_id == "getDomains":
            self._get_domains(entry, parsed, body)
        else:
            self._send_json(
                entry,
                404,
                {
                    "errorCode": "ROUTE_NOT_IN_CONTRACT",
                    "message": "Route is not served by this fixture",
                },
            )

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        return self.rfile.read(max(length, 0))

    @staticmethod
    def _decode_json(body: bytes) -> Any:
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return object()

    def _common_json_headers_are_valid(
        self,
        entry: dict[str, Any],
    ) -> bool:
        return entry["accept"] == "application/json"

    def _create_token(
        self,
        entry: dict[str, Any],
        parsed: Any,
        body: bytes,
    ) -> None:
        state = self.server.state
        scenario = state.scenario
        expected_body = {
            "username": scenario["username"],
            "password": scenario["password"],
        }
        valid = (
            not parsed.query
            and entry["authorization"] is None
            and entry["contentType"] == "application/json"
            and self._common_json_headers_are_valid(entry)
            and self._decode_json(body) == expected_body
        )
        with state.state_lock:
            state.create_count += 1
            first_call = state.create_count == 1
        if not valid:
            self._send_json(
                entry,
                400,
                {
                    "errorCode": "BAD_TOKEN_REQUEST",
                    "message": "Token request did not match the contract",
                },
            )
            return
        if not first_call:
            self._send_json(
                entry,
                409,
                {
                    "errorCode": "REAUTHENTICATION_FORBIDDEN",
                    "message": "The interrupted run must use its refresh token",
                },
            )
            return
        self._send_json(
            entry,
            201,
            {
                "accessToken": scenario["first_access_token"],
                "refreshToken": {"id": scenario["refresh_token_id"]},
            },
        )

    def _refresh_access_token(
        self,
        entry: dict[str, Any],
        parsed: Any,
        body: bytes,
    ) -> None:
        state = self.server.state
        scenario = state.scenario
        valid = (
            not parsed.query
            and entry["authorization"] is None
            and entry["contentType"] == "application/json"
            and self._common_json_headers_are_valid(entry)
            and self._decode_json(body) == scenario["refresh_token_id"]
        )
        with state.state_lock:
            state.refresh_count += 1
            first_call = state.refresh_count == 1
        if not valid:
            self._send_json(
                entry,
                400,
                {
                    "errorCode": "BAD_REFRESH_REQUEST",
                    "message": "Refresh request did not match the contract",
                },
            )
            return
        if not first_call:
            self._send_json(
                entry,
                409,
                {
                    "errorCode": "REFRESH_ALREADY_USED",
                    "message": "Only one refresh is available",
                },
            )
            return
        self._send_json(entry, 200, scenario["second_access_token"])

    def _get_domains(
        self,
        entry: dict[str, Any],
        _parsed: Any,
        body: bytes,
    ) -> None:
        state = self.server.state
        scenario = state.scenario
        pairs = entry["queryPairs"]
        valid_query = (
            isinstance(pairs, list)
            and len(pairs) == 2
            and pairs[0][0] == "pageNumber"
            and pairs[1][0] == "pageSize"
        )
        try:
            page_number = int(pairs[0][1]) if valid_query else -1
            page_size = int(pairs[1][1]) if valid_query else -1
        except (TypeError, ValueError):
            page_number = -1
            page_size = -1
        valid = (
            valid_query
            and page_number >= 0
            and page_size == scenario["page_size"]
            and not body
            and entry["contentType"] is None
            and self._common_json_headers_are_valid(entry)
        )
        if not valid:
            self._send_json(
                entry,
                400,
                {
                    "errorCode": "BAD_PAGE_REQUEST",
                    "message": "Collection request did not match the contract",
                },
            )
            return

        authorization = entry["authorization"]
        first_authorization = (
            "Bearer " + scenario["first_access_token"]
        )
        second_authorization = (
            "Bearer " + scenario["second_access_token"]
        )
        with state.state_lock:
            if authorization == first_authorization:
                if page_number >= 1:
                    state.first_token_expired = True
                authorized = not state.first_token_expired
            else:
                authorized = authorization == second_authorization
        if not authorized:
            self._send_json(
                entry,
                401,
                {
                    "errorCode": "TOKEN_EXPIRED",
                    "message": "Access token expired",
                },
            )
            return

        domains = scenario["domains"]
        total_elements = len(domains)
        total_pages = math.ceil(total_elements / page_size)
        if page_number >= total_pages:
            self._send_json(
                entry,
                400,
                {
                    "errorCode": "PAGE_OUT_OF_RANGE",
                    "message": "Requested page does not exist",
                },
            )
            return

        start = page_number * page_size
        elements = list(domains[start : start + page_size])
        with state.state_lock:
            reverse = state.reverse_next_page
            state.reverse_next_page = not state.reverse_next_page
        if reverse:
            elements.reverse()
        entry["servedElementIds"] = [item.get("id") for item in elements]
        self._send_json(
            entry,
            200,
            {
                "elements": elements,
                "pageMetadata": {
                    "pageNumber": page_number,
                    "pageSize": len(elements),
                    "totalElements": total_elements,
                    "totalPages": total_pages,
                },
            },
        )

    def _send_json(
        self,
        entry: dict[str, Any],
        status: int,
        payload: Any,
    ) -> None:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        entry["responseStatus"] = status
        self.server.state.append_log(entry)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--port-file", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    state = FixtureState(args.contract, args.scenario, args.log)
    server = FixtureServer(("127.0.0.1", 0), Handler)
    server.state = state
    args.port_file.write_text(
        str(server.server_address[1]),
        encoding="ascii",
    )
    server.serve_forever(poll_interval=0.05)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
