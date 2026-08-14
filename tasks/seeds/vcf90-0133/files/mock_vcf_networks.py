"""Contract-pinned loopback VCF Operations for Networks mock."""

from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from typing import Iterator
from urllib.parse import parse_qsl, urlsplit


_EXPECTED_OPERATION_ID = "listTroubleshootingIncidents"
_EXPECTED_METHOD = "GET"
_EXPECTED_PATH = "/gnt/troubleshoot/incidents"
_EXPECTED_BASE_PATH = "/api/ni"

_INCIDENTS = [
    {
        "entity_id": "incident-zeta",
        "start_entity_id": "vm-50",
        "name": "Zeta path",
        "status": "COMPLETED",
    },
    {
        "entity_id": "incident-alpha",
        "start_entity_id": "vm-10",
        "name": "Alpha path",
        "status": "RUNNING",
    },
    {
        "entity_id": "incident-delta",
        "start_entity_id": "vm-40",
        "name": "Delta path",
        "status": "COMPLETED",
    },
    {
        "entity_id": "incident-beta",
        "start_entity_id": "vm-20",
        "name": "Beta path",
        "status": "FAILED",
    },
    {
        "entity_id": "incident-gamma",
        "start_entity_id": "vm-30",
        "name": "Gamma path",
        "status": "COMPLETED",
    },
]

_PAGE_CURSORS = [None, "MTA=", "MjA=", "MzA=", "NDA="]


class ContractMismatch(ValueError):
    """Raised when the mock is pointed at a different API contract."""


def _load_route(contract_path: Path) -> tuple[str, str, str]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    operations = contract.get("operations")
    if not isinstance(operations, dict) or list(operations) != [_EXPECTED_OPERATION_ID]:
        raise ContractMismatch("mock requires exactly listTroubleshootingIncidents")
    operation = operations[_EXPECTED_OPERATION_ID]
    method = operation.get("method")
    path = operation.get("path")
    base_path = contract.get("server_base_path")
    if (method, path, base_path) != (
        _EXPECTED_METHOD,
        _EXPECTED_PATH,
        _EXPECTED_BASE_PATH,
    ):
        raise ContractMismatch("operation wire route does not match the pinned mock")
    return method, base_path + path, _EXPECTED_OPERATION_ID


class MockVcfServer(ThreadingHTTPServer):
    """A loopback-only server whose request log is readable by the test."""

    daemon_threads = True

    def __init__(self, contract_path: Path):
        method, path, operation_id = _load_route(contract_path)
        self.route = (method, path)
        self.operation_id = operation_id
        self.request_log: list[dict[str, object]] = []
        self._log_lock = threading.Lock()
        super().__init__(("127.0.0.1", 0), _handler_type())

    @property
    def appliance_url(self) -> str:
        host, port = self.server_address
        return f"http://{host}:{port}"


def _handler_type() -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            split = urlsplit(self.path)
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            entry = {
                "method": self.command,
                "path": split.path,
                "raw_query": split.query,
                "headers": {key.lower(): value for key, value in self.headers.items()},
                "body": body.decode("utf-8"),
            }
            with self.server._log_lock:  # type: ignore[attr-defined]
                self.server.request_log.append(entry)  # type: ignore[attr-defined]

            if (self.command, split.path) != self.server.route:  # type: ignore[attr-defined]
                self._json_response(404, {"error": "operation not in contract"})
                return

            pairs = parse_qsl(split.query, keep_blank_values=True)
            names = [name for name, _ in pairs]
            allowed = {"size", "cursor", "start_entity_id"}
            query = dict(pairs)
            size_text = query.get("size", "")
            if (
                any(name not in allowed for name in names)
                or names.count("size") != 1
                or any(names.count(name) > 1 for name in allowed)
                or not size_text.isdigit()
                or int(size_text) < 1
                or any(value == "" for _, value in pairs)
            ):
                self._json_response(400, {"error": "query does not match contract"})
                return

            cursor = query.get("cursor")
            try:
                page_number = _PAGE_CURSORS.index(cursor)
            except ValueError:
                self._json_response(400, {"error": "unknown cursor"})
                return

            size = int(size_text)
            start = page_number * size
            if start >= len(_INCIDENTS) and cursor is not None:
                self._json_response(400, {"error": "unknown cursor"})
                return
            end = min(start + size, len(_INCIDENTS))
            page: dict[str, object] = {
                "results": _INCIDENTS[start:end],
                "total_count": len(_INCIDENTS),
            }
            if end < len(_INCIDENTS):
                page["cursor"] = _PAGE_CURSORS[page_number + 1]
            elif size == 3:
                # Exercise the contract's alternate terminal-cursor representation.
                page["cursor"] = ""
            self._json_response(200, page)

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            self._json_response(404, {"error": "operation not in contract"})

        def do_PUT(self) -> None:  # noqa: N802 - stdlib handler API
            self._json_response(404, {"error": "operation not in contract"})

        def do_DELETE(self) -> None:  # noqa: N802 - stdlib handler API
            self._json_response(404, {"error": "operation not in contract"})

        def _json_response(self, status: int, value: object) -> None:
            encoded = json.dumps(value, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


@contextmanager
def running_mock(contract_path: Path | None = None) -> Iterator[MockVcfServer]:
    if contract_path is None:
        contract_path = Path(__file__).resolve().parent / "docs" / "contract.json"
    server = MockVcfServer(contract_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
