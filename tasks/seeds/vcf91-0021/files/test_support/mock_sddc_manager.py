"""Contract-pinned loopback SDDC Manager mock used by protected verification.

The mock intentionally implements only the operationIds named in
``docs/contract.json``. Every request is appended and flushed to an NDJSON log
so verification observes the client's real HTTP traffic.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "docs" / "contract.json"


class MockSddcManager:
    """Start a loopback server pinned to the three extracted operations."""

    def __init__(
        self,
        log_path: str | Path,
        *,
        contract_path: str | Path = DEFAULT_CONTRACT,
        task_statuses: tuple[str, ...] = ("PENDING", "IN_PROGRESS", "SUCCESSFUL"),
    ) -> None:
        self.log_path = Path(log_path)
        self.contract_path = Path(contract_path)
        self.contract = json.loads(self.contract_path.read_text(encoding="utf-8"))
        self.task_statuses = task_statuses
        if not task_statuses:
            raise ValueError("task_statuses must not be empty")
        self._lock = threading.Lock()
        self._request_sequence = 0
        self._task_reads = 0
        self._collection_reads = 0
        self.collection_orders: list[list[str]] = []
        self._routes = self._compile_routes()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def _compile_routes(self) -> list[tuple[str, re.Pattern[str], str]]:
        routes = []
        for operation in self.contract["operations"]:
            template = operation["path"]
            pattern_parts = []
            cursor = 0
            for match in re.finditer(r"\{[^{}]+\}", template):
                pattern_parts.append(re.escape(template[cursor : match.start()]))
                pattern_parts.append(r"([^/]+)")
                cursor = match.end()
            pattern_parts.append(re.escape(template[cursor:]))
            routes.append(
                (
                    operation["method"],
                    re.compile("^" + "".join(pattern_parts) + "$"),
                    operation["operationId"],
                )
            )
        return routes

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("mock server is not running")
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> "MockSddcManager":
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text("", encoding="utf-8")
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
                owner._handle(self)

            def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
                owner._handle(self)

            def do_PUT(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
                owner._handle(self)

            def do_PATCH(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
                owner._handle(self)

            def do_DELETE(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
                owner._handle(self)

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._server = None
        self._thread = None

    def read_log(self) -> list[dict[str, Any]]:
        return [
            json.loads(line)
            for line in self.log_path.read_text(encoding="utf-8").splitlines()
            if line
        ]

    def _match(self, method: str, path: str) -> tuple[str, tuple[str, ...]] | None:
        for route_method, pattern, operation_id in self._routes:
            match = pattern.fullmatch(path)
            if method == route_method and match:
                return operation_id, match.groups()
        return None

    def _read_body(self, handler: BaseHTTPRequestHandler) -> tuple[bytes, Any]:
        try:
            length = int(handler.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        raw = handler.rfile.read(length) if length else b""
        if not raw:
            return raw, None
        try:
            return raw, json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return raw, {"_malformed_json": True}

    def _record(
        self,
        handler: BaseHTTPRequestHandler,
        parsed: object,
    ) -> None:
        split = urlsplit(handler.path)
        with self._lock:
            self._request_sequence += 1
            entry = {
                "sequence": self._request_sequence,
                "method": handler.command,
                "path": split.path,
                "query": split.query,
                "headers": {
                    key.lower(): value for key, value in handler.headers.items()
                },
                "json": parsed,
            }
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, sort_keys=True) + "\n")
                stream.flush()

    def _send(
        self,
        handler: BaseHTTPRequestHandler,
        status: int,
        payload: object,
    ) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(encoded)))
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.wfile.write(encoded)

    def _task(self, status: str) -> dict[str, Any]:
        task = {
            "id": "task-domain-001",
            "name": "Create workload domain",
            "type": "DOMAIN_CREATE",
            "status": status,
            "creationTimestamp": "2026-07-28T12:00:00Z",
        }
        if status in {
            "SUCCESSFUL",
            "COMPLETED_WITH_WARNING",
            "FAILED",
            "CANCELLED",
            "SKIPPED",
            "TIMED_OUT",
        }:
            task["completionTimestamp"] = "2026-07-28T12:00:03Z"
        if status == "FAILED":
            task["errors"] = [{"message": "fixture terminal failure"}]
        return task

    def _domains(self) -> dict[str, Any]:
        domains = [
            {"id": "domain-z", "name": "Zulu", "status": "ACTIVE", "type": "VI"},
            {"id": "domain-a", "name": "alpha", "status": "ACTIVE", "type": "VI"},
            {"id": "domain-m", "name": "Mike", "status": "ACTIVE", "type": "VI"},
        ]
        with self._lock:
            self._collection_reads += 1
            if self._collection_reads % 2 == 0:
                domains.reverse()
            self.collection_orders.append([item["name"] for item in domains])
        return {
            "elements": domains,
            "pageMetadata": {
                "pageNumber": 0,
                "pageSize": len(domains),
                "totalElements": len(domains),
                "totalPages": 1,
            },
        }

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        split = urlsplit(handler.path)
        _raw, parsed = self._read_body(handler)
        self._record(handler, parsed)
        matched = self._match(handler.command, split.path)
        if matched is None:
            self._send(handler, 404, {"message": "operation is not in contract"})
            return

        authorization = handler.headers.get("Authorization")
        accept = handler.headers.get("Accept", "")
        if authorization != "Bearer verifier-token":
            self._send(handler, 401, {"message": "unauthorized"})
            return
        if "application/json" not in accept:
            self._send(handler, 406, {"message": "application/json required"})
            return

        operation_id, parameters = matched
        if operation_id == "createDomain":
            if "application/json" not in handler.headers.get("Content-Type", ""):
                self._send(handler, 415, {"message": "application/json required"})
                return
            if not isinstance(parsed, dict):
                self._send(handler, 400, {"message": "JSON object required"})
                return
            self._send(handler, 202, self._task("PENDING"))
            return
        if operation_id == "getTask":
            if parameters != ("task-domain-001",):
                self._send(handler, 404, {"message": "task not found"})
                return
            with self._lock:
                index = min(self._task_reads, len(self.task_statuses) - 1)
                status = self.task_statuses[index]
                self._task_reads += 1
            self._send(handler, 200, self._task(status))
            return
        if operation_id == "getDomains":
            self._send(handler, 200, self._domains())
            return
        self._send(handler, 500, {"message": "unreachable contract operation"})
