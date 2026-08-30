"""Contract-pinned loopback VCF Operations Log Management service."""

from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
EXPECTED_OPERATIONS = {
    ("GET", "/api/v2/agent/groups", "getAllAgentGroupConfig"),
}


def _contract_routes() -> dict[tuple[str, str], str]:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    discovered = {
        (item["method"], item["path"], item["operationId"])
        for item in contract["operations"]
    }
    if discovered != EXPECTED_OPERATIONS:
        raise RuntimeError(f"focused contract drift: {sorted(discovered)!r}")
    routes: dict[tuple[str, str], str] = {}
    for method, path, operation_id in discovered:
        key = (method, path)
        if key in routes:
            raise RuntimeError("duplicate method/path in focused contract")
        routes[key] = operation_id
    return routes


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, fault: str | None) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.routes = _contract_routes()
        self.request_log: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.fault = fault
        self.inventory_index = -1
        self.next_page = 0
        nonce = secrets.token_hex(8)
        self.token = f"log-token-{secrets.token_urlsafe(24)}"
        ids = [f"group-{nonce}-{part}" for part in ("40", "50", "30", "20", "10")]
        names = [
            f"zeta-{nonce}",
            f"Alpha-{nonce}",
            f"alpha-{nonce}",
            f"Echo-{nonce}",
            f"Echo-{nonce}",
        ]
        self.groups: list[dict[str, Any]] = []
        for index, (group_id, name) in enumerate(
            zip(ids, names, strict=True)
        ):
            self.groups.append(
                {
                    "id": group_id,
                    "name": name,
                    "autoUpdate": index % 2 == 0,
                    "info": f"runtime-info-{nonce}-{index}",
                    "agentConfig": f"runtime-config-{nonce}-{index}",
                    "constraints": {
                        "text": f"runtime-query-{nonce}-{index}"
                    },
                    "mpId": f"runtime-mp-{nonce}-{index}",
                }
            )
        self.layouts = (
            ((4, 1), (3, 0), (2,)),
            ((2, 0), (4, 3), (1,)),
        )

    def append_request(self, item: dict[str, Any]) -> None:
        with self.lock:
            self.request_log.append(item)


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def _capture(self, method: str) -> tuple[str, list[tuple[str, str]], bytes]:
        parsed = urlsplit(self.path)
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length else 0
        except ValueError:
            length = 0
        body = self.rfile.read(length) if length else b""
        query = parse_qsl(parsed.query, keep_blank_values=True)
        operation_id = self.server.routes.get((method, parsed.path))
        self.server.append_request(
            {
                "method": method,
                "target": self.path,
                "path": parsed.path,
                "query": query,
                "headers": [
                    (name.lower(), value)
                    for name, value in self.headers.raw_items()
                ],
                "body": body,
                "operationId": operation_id,
            }
        )
        return parsed.path, query, body

    def _dispatch(self, method: str) -> None:
        path, query, body = self._capture(method)
        operation_id = self.server.routes.get((method, path))
        if operation_id is None:
            self._json(
                404,
                {
                    "errorCode": "OUTSIDE_FOCUSED_CONTRACT",
                    "errorMessage": "method and path are not in contract",
                },
            )
            return
        if operation_id != "getAllAgentGroupConfig":
            self._json(
                500,
                {"errorCode": "UNHANDLED_CONTRACT_OPERATION"},
            )
            return
        if self.headers.get_all("X-JWT-Token") != [self.server.token]:
            self._json(
                403,
                {
                    "errorCode": "SECURITY_ERROR",
                    "errorMessage": "X-JWT-Token required",
                },
            )
            return
        if body:
            self._json(
                400,
                {
                    "errorCode": "UNEXPECTED_BODY",
                    "errorMessage": "GET must be bodyless",
                },
            )
            return
        if len(query) != 2 or [name for name, _ in query] != ["page", "size"]:
            self._json(
                400,
                {
                    "errorCode": "MALFORMED_PAGEABLE",
                    "errorMessage": "page and size are required",
                },
            )
            return
        try:
            page_number = int(query[0][1])
            page_size = int(query[1][1])
        except ValueError:
            self._json(400, {"errorCode": "MALFORMED_PAGEABLE"})
            return
        if page_number < 0 or page_size < 1:
            self._json(400, {"errorCode": "INVALID_PAGEABLE"})
            return

        if page_number == 0:
            self.server.inventory_index += 1
            self.server.next_page = 0
        if page_number != self.server.next_page:
            self._json(
                409,
                {
                    "errorCode": "NON_PROGRESSING_PAGE",
                    "errorMessage": "pages must be requested once in order",
                },
            )
            return

        layout = self.server.layouts[
            self.server.inventory_index % len(self.server.layouts)
        ]
        if page_number >= len(layout):
            self._json(400, {"errorCode": "PAGE_OUT_OF_RANGE"})
            return
        if self.server.fault == "late_http" and page_number == 1:
            self._json(
                500,
                {
                    "errorCode": "RUNTIME_PAGE_FAILURE",
                    "errorMessage": "later page failed",
                },
            )
            return

        indexes = layout[page_number]
        content = [self.server.groups[index] for index in indexes]
        if self.server.fault == "duplicate" and page_number == 1:
            content = [self.server.groups[layout[0][0]], *content[1:]]

        self.server.next_page += 1
        page = {
            "content": content,
            "empty": not content,
            "first": page_number == 0,
            "last": page_number == len(layout) - 1,
            "number": page_number,
            "numberOfElements": len(content),
            "pageable": {
                "offset": page_number * page_size,
                "pageNumber": page_number,
                "pageSize": page_size,
                "paged": True,
                "sort": {
                    "empty": True,
                    "sorted": False,
                    "unsorted": True,
                },
                "unpaged": False,
            },
            "size": page_size,
            "sort": {
                "empty": True,
                "sorted": False,
                "unsorted": True,
            },
            "totalElements": len(self.server.groups),
            "totalPages": len(layout),
        }
        self._json(200, [page])

    def _json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)


class VCFLogManagementMock:
    """Context manager exposing a loopback origin and captured request log."""

    def __init__(self, *, fault: str | None = None) -> None:
        if fault not in {None, "duplicate", "late_http"}:
            raise ValueError("unknown mock fault")
        self._server = _Server(fault)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="vcf-log-management-mock",
            daemon=True,
        )

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    @property
    def token(self) -> str:
        return self._server.token

    @property
    def groups(self) -> list[dict[str, Any]]:
        return self._server.groups

    @property
    def requests(self) -> list[dict[str, Any]]:
        return self._server.request_log

    def __enter__(self) -> "VCFLogManagementMock":
        self._thread.start()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)
