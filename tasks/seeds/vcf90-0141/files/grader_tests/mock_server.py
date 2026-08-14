#!/usr/bin/env python3
"""Loopback-only mock whose routes are loaded from the pinned OpenAPI slice."""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


EXPECTED_OPERATION_IDS = {
    "updateCertificate",
    "fetchCertificateUpdateStatusForUpdateId",
}

SPECIAL_UPDATE_ID = "update /✓?#%"


SCENARIOS: dict[str, dict[str, object]] = {
    "happy": {
        "submit": (202, {
            "id": "update-42",
            "name": "platform cert/primary",
            "status": "SUBMITTED",
        }),
        "polls": [
            (200, {
                "id": "update-42",
                "name": "platform cert/primary",
                "status": "IN_PROGRESS",
                "last_modified_time": 1750000000000,
            }),
            (200, {
                "id": "update-42",
                "name": "platform cert/primary",
                "status": "IN_PROGRESS",
                "last_modified_time": 1750000000000,
            }),
            (200, {
                "id": "update-42",
                "name": "platform cert/primary",
                "status": "SUCCESS",
                "last_modified_time": 1750000000000,
            }),
        ],
    },
    "chain_success": {
        "submit": (202, {
            "id": SPECIAL_UPDATE_ID,
            "name": "submission",
            "status": "SUBMITTED",
        }),
        "polls": [
            (200, {"id": SPECIAL_UPDATE_ID, "status": "SUBMITTED"}),
            (200, {"id": SPECIAL_UPDATE_ID, "status": "IN_PROGRESS"}),
            (200, {
                "id": SPECIAL_UPDATE_ID,
                "name": "node\n\"quoted\"",
                "status": "SUCCESS",
            }),
        ],
    },
    "failed": {
        "submit": (202, {"id": "failed-7", "status": "SUBMITTED"}),
        "polls": [(200, {
            "id": "failed-7",
            "name": "failed certificate",
            "status": "FAILED",
            "error_message": "certificate rejected",
        })],
    },
    "submit_http": {
        "submit": (409, {"code": 409, "message": "conflict"}),
        "polls": [],
    },
    "missing_submit_id": {
        "submit": (202, {"status": "SUBMITTED"}),
        "polls": [],
    },
    "nested_submit_id": {
        "submit": (202, {
            "metadata": {"id": "not-a-top-level-id"},
            "status": "SUBMITTED",
        }),
        "polls": [],
    },
    "missing_submit_status": {
        "submit": (202, {"id": "missing-status"}),
        "polls": [],
    },
    "unknown_submit_status": {
        "submit": (202, {"id": "unknown-submit", "status": "PAUSED"}),
        "polls": [],
    },
    "poll_http": {
        "submit": (202, {"id": "poll-http", "status": "SUBMITTED"}),
        "polls": [(503, {"code": 503, "message": "unavailable"})],
    },
    "missing_poll_id": {
        "submit": (202, {"id": "missing-poll-id", "status": "SUBMITTED"}),
        "polls": [(200, {"status": "SUCCESS"})],
    },
    "missing_poll_status": {
        "submit": (202, {"id": "missing-poll-status", "status": "SUBMITTED"}),
        "polls": [(200, {"id": "missing-poll-status"})],
    },
    "unknown_poll_status": {
        "submit": (202, {"id": "unknown-poll", "status": "SUBMITTED"}),
        "polls": [(200, {"id": "unknown-poll", "status": "WAITING"})],
    },
    "interrupt": {
        "submit": (202, {"id": "interrupt-1", "status": "SUBMITTED"}),
        "polls": [(200, {"id": "interrupt-1", "status": "IN_PROGRESS"})],
    },
}


def _template_pattern(template: str) -> re.Pattern[str]:
    pieces = re.split(r"(\{[^}]+\})", template)
    expression = "".join(
        r"[^/?]+" if piece.startswith("{") else re.escape(piece)
        for piece in pieces
    )
    return re.compile("^" + expression + "$")


class ContractMock:
    def __init__(
        self,
        contract_path: Path,
        request_log_path: Path,
        scenario: str = "happy",
    ):
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        server_prefix = contract["servers"][0]["url"].rstrip("/")
        routes: list[tuple[str, re.Pattern[str], str]] = []
        for path, path_item in contract["paths"].items():
            for method, operation in path_item.items():
                operation_id = operation.get("operationId")
                if operation_id:
                    routes.append(
                        (
                            method.upper(),
                            _template_pattern(server_prefix + path),
                            operation_id,
                        )
                    )
        if {route[2] for route in routes} != EXPECTED_OPERATION_IDS:
            raise ValueError("contract does not contain the pinned operation set")
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown mock scenario: {scenario}")

        self._routes = routes
        self._scenario = SCENARIOS[scenario]
        self._request_log_path = request_log_path
        self._request_log_path.write_text("", encoding="utf-8")
        self._log_lock = threading.Lock()
        self._poll_count = 0
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_type())
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def api_base_uri(self) -> str:
        host, port = self._httpd.server_address
        return f"http://{host}:{port}/api/ni"

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._httpd.shutdown()
        self._thread.join(timeout=5)
        self._httpd.server_close()

    def _match(self, method: str, raw_path: str, query: str) -> str | None:
        if query:
            return None
        for route_method, pattern, operation_id in self._routes:
            if route_method == method and pattern.fullmatch(raw_path):
                return operation_id
        return None

    def _append_log(self, entry: dict[str, object]) -> None:
        encoded = json.dumps(entry, sort_keys=True, separators=(",", ":"))
        with self._log_lock:
            with self._request_log_path.open("a", encoding="utf-8") as stream:
                stream.write(encoded + "\n")

    def _response_for(self, operation_id: str) -> tuple[int, dict[str, object]]:
        if operation_id == "updateCertificate":
            response = self._scenario["submit"]
            assert isinstance(response, tuple)
            return response

        polls = self._scenario["polls"]
        assert isinstance(polls, list)
        with self._log_lock:
            self._poll_count += 1
            index = self._poll_count - 1
        if not polls:
            return 500, {"code": 500, "message": "unexpected poll"}
        response = polls[min(index, len(polls) - 1)]
        assert isinstance(response, tuple)
        return response

    def _handler_type(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                self._dispatch()

            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                self._dispatch()

            def do_POST(self) -> None:  # noqa: N802 - log unexpected methods too
                self._dispatch()

            def do_DELETE(self) -> None:  # noqa: N802 - log unexpected methods too
                self._dispatch()

            def _dispatch(self) -> None:
                content_length = int(self.headers.get("Content-Length", "0"))
                body_bytes = self.rfile.read(content_length)
                parsed = urlsplit(self.path)
                operation_id = outer._match(self.command, parsed.path, parsed.query)
                outer._append_log(
                    {
                        "method": self.command,
                        "target": self.path,
                        "operationId": operation_id,
                        "headers": {
                            key.lower(): self.headers.get_all(key)
                            for key in self.headers.keys()
                        },
                        "body": body_bytes.decode("utf-8"),
                    }
                )

                if operation_id in EXPECTED_OPERATION_IDS:
                    status_code, payload = outer._response_for(operation_id)
                    self._json_response(status_code, payload)
                else:
                    self._json_response(404, {"code": 404, "message": "not found"})

            def _json_response(self, status_code: int, payload: dict[str, object]) -> None:
                body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *args: object) -> None:
                return

        return Handler
