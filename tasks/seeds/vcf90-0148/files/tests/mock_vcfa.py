from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit


class MockState:
    def __init__(
        self,
        contract_path: Path,
        request_log: Path,
        access_token: str,
        api_version: str | None = None,
        filter_value: str | None = None,
        zero_page_at: int | None = None,
        requested_top: int = 3,
    ) -> None:
        self.contract = json.loads(contract_path.read_text(encoding="utf-8"))
        self.request_log = request_log
        self.access_token = access_token
        self.api_version = api_version
        self.filter_value = filter_value
        self.zero_page_at = zero_page_at
        self.requested_top = requested_top
        self.lock = threading.Lock()
        self.projects: list[dict[str, Any]] = [
            {"id": "project-zeta", "name": "Zeta", "owner": "platform"},
            {"id": "project-beta", "name": "alpha", "owner": "applications"},
            {"id": "project-delta", "name": "delta", "owner": "platform"},
            {"id": "project-alpha", "name": "alpha", "owner": "security"},
            {"id": "project-gamma", "name": "Beta", "owner": "applications"},
        ]
        self.server_page_cap = 2

        operations = self.contract["operations"]
        if len(operations) != 1:
            raise ValueError("focused mock requires exactly one contract operation")
        self.operation = operations[0]
        self.named_operations = {
            (item["method"], item["path"]) for item in operations
        }

        self.request_log.parent.mkdir(parents=True, exist_ok=True)
        self.request_log.write_text("", encoding="utf-8")

    def record(self, entry: dict[str, Any]) -> None:
        encoded = json.dumps(entry, separators=(",", ":")) + "\n"
        with self.lock:
            with self.request_log.open("a", encoding="utf-8") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())

    def clear_log(self) -> None:
        with self.lock:
            self.request_log.write_text("", encoding="utf-8")

    def expected_projects(self) -> list[dict[str, Any]]:
        return deepcopy(self.projects)


def _handler_for(state: MockState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "VCFAContractMock/1.0"
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *args: object) -> None:
            return

        def do_GET(self) -> None:
            self._dispatch()

        def do_POST(self) -> None:
            self._reject_unknown()

        def do_PUT(self) -> None:
            self._reject_unknown()

        def do_PATCH(self) -> None:
            self._reject_unknown()

        def do_DELETE(self) -> None:
            self._reject_unknown()

        def _headers_snapshot(self) -> dict[str, list[str]]:
            snapshot: dict[str, list[str]] = {}
            for name in self.headers.keys():
                lowered = name.lower()
                if lowered not in snapshot:
                    snapshot[lowered] = self.headers.get_all(name, failobj=[])
            return snapshot

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length", "0"))
            return self.rfile.read(length)

        def _entry(self, body: bytes) -> dict[str, Any]:
            return {
                "method": self.command,
                "target": self.path,
                "headers": self._headers_snapshot(),
                "bodyLength": len(body),
                "body": body.decode("utf-8"),
            }

        def _dispatch(self) -> None:
            body = self._read_body()
            state.record(self._entry(body))
            parsed = urlsplit(self.path)
            if (
                self.command == state.operation["method"]
                and parsed.path == state.operation["path"]
            ):
                self._get_projects(parsed.query, body)
                return
            self._json_response(404, {"message": "operation not named by contract"})

        def _get_projects(self, raw_query: str, body: bytes) -> None:
            if body:
                self._json_response(400, {"message": "GET body is not allowed"})
                return
            if self.headers.get_all("Authorization", failobj=[]) != [
                f"Bearer {state.access_token}"
            ]:
                self._json_response(401, {"message": "invalid authorization"})
                return
            if self.headers.get_all("Accept", failobj=[]) != ["application/json"]:
                self._json_response(406, {"message": "JSON Accept header required"})
                return

            query = parse_qs(raw_query, keep_blank_values=True, strict_parsing=True)
            expected_query = {"$top": str(state.requested_top), "$skip": None}
            if state.api_version is not None:
                expected_query["apiVersion"] = state.api_version
            if state.filter_value is not None:
                expected_query["$filter"] = state.filter_value
            if set(query) != set(expected_query) or any(
                len(values) != 1 or values[0] == "" for values in query.values()
            ):
                self._json_response(400, {"message": "query contract mismatch"})
                return
            try:
                top = int(query["$top"][0])
                skip = int(query["$skip"][0])
            except ValueError:
                self._json_response(400, {"message": "invalid page controls"})
                return
            if top != state.requested_top or skip not in {0, 2, 4}:
                self._json_response(400, {"message": "unexpected page"})
                return
            if any(
                expected is not None and query[name][0] != expected
                for name, expected in expected_query.items()
            ):
                self._json_response(400, {"message": "query value mismatch"})
                return

            page_size = min(top, state.server_page_cap)
            content = (
                []
                if skip == state.zero_page_at
                else state.projects[skip : skip + page_size]
            )
            self._json_response(
                200,
                {
                    "content": content,
                    "totalElements": len(state.projects),
                    "numberOfElements": len(content),
                },
            )

        def _reject_unknown(self) -> None:
            body = self._read_body()
            state.record(self._entry(body))
            self._json_response(404, {"message": "operation not named by contract"})

        def _json_response(self, status: int, payload: Any) -> None:
            encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(encoded)

    return Handler


def start_mock(
    contract_path: Path,
    request_log: Path,
    access_token: str,
    api_version: str | None = None,
    filter_value: str | None = None,
    zero_page_at: int | None = None,
    requested_top: int = 3,
) -> tuple[ThreadingHTTPServer, MockState]:
    state = MockState(
        contract_path,
        request_log,
        access_token,
        api_version,
        filter_value,
        zero_page_at,
        requested_top,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, state
