"""Contract-pinned loopback fixture for the VCF Installer getTasks operation."""

from __future__ import annotations

from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
from threading import Thread
from typing import Any
from urllib.parse import parse_qsl, urlsplit


_ROOT = Path(__file__).resolve().parent
_CONTRACT = json.loads((_ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
_OPERATIONS = {item["operationId"]: item for item in _CONTRACT["operations"]}
_GET_TASKS = _OPERATIONS["getTasks"]
_ALLOWED_QUERY = {item["name"] for item in _GET_TASKS["parameters"]}

_TASKS = (
    {
        "id": "task-c",
        "name": "Configure management domain",
        "type": "SDDC_CONFIGURE",
        "status": "IN_PROGRESS",
        "creationTimestamp": "2026-01-03T00:00:00Z",
    },
    {
        "id": "task-a",
        "name": "Discover hosts",
        "type": "HOST_DISCOVERY",
        "status": "SUCCESSFUL",
        "creationTimestamp": "2026-01-01T00:00:00Z",
        "completionTimestamp": "2026-01-01T00:05:00Z",
        "resources": [
            {"resourceId": "rack/A + west&blue", "type": "ESXI"},
        ],
    },
    {
        "id": "task-e",
        "name": "Validate deployment",
        "type": "SDDC_VALIDATE",
        "status": "FAILED",
        "creationTimestamp": "2026-01-05T00:00:00Z",
        "completionTimestamp": "2026-01-05T00:04:00Z",
    },
    {
        "id": "task-b",
        "name": "Prepare binaries",
        "type": "BUNDLE_DOWNLOAD",
        "status": "SUCCESSFUL",
        "creationTimestamp": "2026-01-02T00:00:00Z",
        "completionTimestamp": "2026-01-02T00:20:00Z",
    },
    {
        "id": "task-d",
        "name": "Deploy vCenter",
        "type": "VCENTER_DEPLOY",
        "status": "SUCCESSFUL",
        "creationTimestamp": "2026-01-04T00:00:00Z",
        "completionTimestamp": "2026-01-04T00:40:00Z",
    },
)


def _epoch_milliseconds(timestamp: str) -> int:
    return int(datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp() * 1000)


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, scripted_responses: list[tuple[int, bytes]] | None = None) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.request_log: list[dict[str, Any]] = []
        self.scripted_responses = list(scripted_responses or [])


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def log_message(self, format: str, *args: object) -> None:
        return

    def _record(self) -> tuple[Any, list[tuple[str, str]], bytes]:
        parsed = urlsplit(self.path)
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        self.server.request_log.append(
            {
                "method": self.command,
                "path": parsed.path,
                "rawQuery": parsed.query,
                "queryPairs": pairs,
                "headers": {key.lower(): value for key, value in self.headers.items()},
                "body": body,
            }
        )
        return parsed, pairs, body

    def _json(self, status: int, value: object) -> None:
        payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self._raw(status, payload)

    def _raw(self, status: int, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        parsed, pairs, body = self._record()
        if parsed.path != _GET_TASKS["path"] or _GET_TASKS["method"] != "GET":
            self._json(404, {"error": "operation not served"})
            return
        if body:
            self._json(400, {"error": "GET body is not permitted"})
            return
        if self.server.scripted_responses:
            status, payload = self.server.scripted_responses.pop(0)
            if status < 0:
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            self._raw(status, payload)
            return
        names = [key for key, _ in pairs]
        if len(names) != len(set(names)) or any(name not in _ALLOWED_QUERY for name in names):
            self._json(400, {"error": "query does not match contract"})
            return
        query = dict(pairs)
        try:
            page_number = int(query.get("pageNumber", "0"))
            page_size = int(query.get("pageSize", "100"))
            completed_after = int(query["completedAfter"]) if "completedAfter" in query else None
        except ValueError:
            self._json(400, {"error": "invalid integer query"})
            return
        if page_number < 0 or page_size < 1 or page_size > 100:
            self._json(400, {"error": "invalid page"})
            return
        if "doLiveRefresh" in query and query["doLiveRefresh"] not in {"true", "false"}:
            self._json(400, {"error": "invalid boolean query"})
            return

        tasks = list(_TASKS)
        filters = {
            "taskStatus": "status",
            "taskType": "type",
        }
        for query_name, field_name in filters.items():
            if query_name in query:
                tasks = [task for task in tasks if task.get(field_name) == query[query_name]]
        resource_filters = {
            "resourceId": "resourceId",
            "resourceType": "type",
        }
        for query_name, field_name in resource_filters.items():
            if query_name in query:
                tasks = [
                    task
                    for task in tasks
                    if any(
                        resource.get(field_name) == query[query_name]
                        for resource in task.get("resources", [])
                    )
                ]
        if "taskName" in query:
            tasks = [task for task in tasks if query["taskName"] in task["name"]]
        if completed_after is not None:
            tasks = [
                task
                for task in tasks
                if "completionTimestamp" in task
                and _epoch_milliseconds(task["completionTimestamp"]) > completed_after
            ]

        order_by = query.get("orderBy")
        if order_by:
            if order_by not in {"id", "name", "creationTimestamp", "status", "type"}:
                self._json(400, {"error": "unsupported ordering field"})
                return
            reverse = query.get("orderDirection", "ASC") == "DESC"
            tasks.sort(key=lambda task: task[order_by], reverse=reverse)

        total_elements = len(tasks)
        total_pages = (total_elements + page_size - 1) // page_size
        start = page_number * page_size
        elements = tasks[start : start + page_size]
        self._json(
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

    def _unsupported(self) -> None:
        self._record()
        self._json(405, {"error": "operation not served"})

    do_POST = _unsupported
    do_PUT = _unsupported
    do_PATCH = _unsupported
    do_DELETE = _unsupported


class MockVCFInstaller:
    """Context manager exposing the loopback base URL and captured wire log."""

    def __init__(self, scripted_responses: list[tuple[int, bytes]] | None = None) -> None:
        self._server: _Server | None = None
        self._thread: Thread | None = None
        self._scripted_responses = scripted_responses

    def __enter__(self) -> "MockVCFInstaller":
        self._server = _Server(self._scripted_responses)
        self._thread = Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        assert self._server is not None
        self._server.shutdown()
        self._server.server_close()
        assert self._thread is not None
        self._thread.join(timeout=2)

    @property
    def base_url(self) -> str:
        assert self._server is not None
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    @property
    def request_log(self) -> list[dict[str, Any]]:
        assert self._server is not None
        return self._server.request_log
