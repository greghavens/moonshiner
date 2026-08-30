"""Contract-pinned loopback server for the focused VCF Installer scenario."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
from threading import Thread
from typing import Any, Iterable
from urllib.parse import urlsplit


TASK_ID = "123e4567-e89b-42d3-a456-556642440000"


class ContractMockServer:
    """Serve exactly the operations declared by a focused contract."""

    def __init__(
        self,
        contract_path: str | Path,
        *,
        accepted_status: str = "IN_PROGRESS",
        poll_statuses: Iterable[str] = (
            "IN_PROGRESS",
            "COMPLETED_WITH_SUCCESS",
        ),
    ):
        contract = json.loads(Path(contract_path).read_text(encoding="utf-8"))
        operations = contract["operations"]
        self._routes = {
            operation_id: (entry["method"], entry["path"])
            for operation_id, entry in operations.items()
        }
        self.request_log: list[dict[str, Any]] = []
        self._accepted_status = accepted_status
        self._poll_statuses = tuple(poll_statuses)
        if not self._poll_statuses:
            raise ValueError("poll_statuses must not be empty")
        self._poll_count = 0
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None

    @property
    def base_url(self) -> str:
        if self._httpd is None:
            raise RuntimeError("server is not running")
        host, port = self._httpd.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> "ContractMockServer":
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def _record(self) -> bytes:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length) if length else b""
                owner.request_log.append(
                    {
                        "method": self.command,
                        "target": self.path,
                        "headers": {
                            key.lower(): value for key, value in self.headers.items()
                        },
                        "body": body,
                    }
                )
                return body

            def _json_response(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _not_found(self) -> None:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_POST(self) -> None:
                self._record()
                route = owner._routes.get("deploySddc")
                parsed = urlsplit(self.path)
                if route is None or route[0] != "POST" or parsed.path != route[1]:
                    self._not_found()
                    return
                self._json_response(
                    202,
                    {
                        "id": TASK_ID,
                        "status": owner._accepted_status,
                        "creationTimestamp": "2026-01-05T10:00:00Z",
                    },
                )

            def do_GET(self) -> None:
                self._record()
                route = owner._routes.get("getSddcTaskByID")
                if route is None or route[0] != "GET":
                    self._not_found()
                    return
                pattern = "^" + re.escape(route[1]).replace(re.escape("{id}"), "([^/]+)") + "$"
                match = re.fullmatch(pattern, urlsplit(self.path).path)
                if match is None or match.group(1) != TASK_ID:
                    self._not_found()
                    return
                status = owner._poll_statuses[
                    min(owner._poll_count, len(owner._poll_statuses) - 1)
                ]
                owner._poll_count += 1
                self._json_response(
                    200,
                    {
                        "id": TASK_ID,
                        "status": status,
                        "creationTimestamp": "2026-01-05T10:00:00Z",
                    },
                )

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = Thread(
            target=self._httpd.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        assert self._httpd is not None
        assert self._thread is not None
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=2)
        self._httpd = None
        self._thread = None
