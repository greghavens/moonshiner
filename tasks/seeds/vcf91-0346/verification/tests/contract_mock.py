from __future__ import annotations

import copy
import json
import re
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
EXPECTED_OPERATION_IDS = {
    "getRequest",
    "getRequestEvents",
    "getEventLogs",
    "getEventLogsContent",
}


@dataclass
class RequestRecord:
    method: str
    path: str
    query: str
    authorization: str | None
    operation_id: str | None


@dataclass
class MockState:
    request_log: list[RequestRecord] = field(default_factory=list)
    response_counts: dict[str, int] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def log(self, record: RequestRecord) -> None:
        with self.lock:
            self.request_log.append(record)

    def flip_collection(self, operation_id: str, values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with self.lock:
            count = self.response_counts.get(operation_id, 0)
            self.response_counts[operation_id] = count + 1
        result = copy.deepcopy(values)
        if count % 2 == 1:
            result.reverse()
        return result


REQUEST_ID = "req-failed-42"
DOWNLOAD_FAILURE_REQUEST_ID = "req-download-failure"

REQUEST = {
    "id": REQUEST_ID,
    "name": "Create application VM",
    "status": "FAILED",
    "details": "Provisioning request failed. Inspect request events and logs.",
    "completedTasks": 2,
    "totalTasks": 3,
}

EVENTS = [
    {
        "id": "evt-allocate",
        "name": "Allocate network",
        "resourceName": "payments-vm",
        "resourceType": "Cloud.vSphere.Machine",
        "details": "Allocation failed in the provider task.",
        "timestamp": "2026-08-15T14:03:00Z",
        "userEvent": False,
        "hasLogs": True,
    },
    {
        "id": "evt-allocate-z",
        "name": "Allocation checkpoint",
        "resourceName": "payments-vm",
        "resourceType": "Cloud.vSphere.Machine",
        "details": "Checkpoint recorded at the allocation timestamp.",
        "timestamp": "2026-08-15T14:03:00Z",
        "userEvent": False,
        "hasLogs": False,
    },
    {
        "id": "evt-start",
        "name": "Request started",
        "resourceName": "payments-vm",
        "resourceType": "Cloud.vSphere.Machine",
        "details": "Request accepted for provisioning.",
        "timestamp": "2026-08-15T14:00:00Z",
        "userEvent": True,
        "hasLogs": False,
    },
    {
        "id": "evt-cleanup",
        "name": "Cleanup",
        "resourceName": "payments-vm",
        "resourceType": "Cloud.vSphere.Machine",
        "details": "Cleanup ran after the provider failure.",
        "timestamp": "2026-08-15T14:05:00Z",
        "userEvent": False,
        "hasLogs": True,
    },
]

LOGS = {
    "evt-allocate": [
        {
            "id": "alloc-log-3",
            "rownum": 30,
            "timestamp": "2026-08-15T14:03:03Z",
            "message": "Provider task terminated after allocation error",
            "eof": True,
        },
        {
            "id": "alloc-log-1",
            "rownum": 10,
            "timestamp": "2026-08-15T14:03:01Z",
            "message": "Starting network allocation for payments-vm",
            "eof": False,
        },
        {
            "id": "alloc-log-2c",
            "rownum": 20,
            "timestamp": "2026-08-15T14:03:02Z",
            "message": "IP address 10.20.0.17 is already allocated",
            "eof": False,
        },
        {
            "id": "alloc-log-2b",
            "rownum": 20,
            "timestamp": "2026-08-15T14:03:02Z",
            "message": "Allocation conflict confirmed",
            "eof": False,
        },
        {
            "id": "alloc-log-2a",
            "rownum": 20,
            "timestamp": "2026-08-15T14:03:01.500Z",
            "message": "Checking address availability",
            "eof": False,
        },
    ],
    "evt-cleanup": [
        {
            "id": "cleanup-log-2",
            "rownum": 2,
            "timestamp": "2026-08-15T14:05:02Z",
            "message": "Cleanup completed",
            "eof": True,
        },
        {
            "id": "cleanup-log-1",
            "rownum": 1,
            "timestamp": "2026-08-15T14:05:01Z",
            "message": "Releasing partial allocation",
            "eof": False,
        },
    ],
}

DOWNLOADS = {
    "evt-allocate": (
        "2026-08-15T14:03:01Z INFO Starting network allocation for payments-vm\n"
        "2026-08-15T14:03:02Z ERROR IP address 10.20.0.17 is already allocated\n"
        "2026-08-15T14:03:03Z ERROR Provider task terminated after allocation error\n"
    ),
    "evt-cleanup": (
        "2026-08-15T14:05:01Z INFO Releasing partial allocation\n"
        "2026-08-15T14:05:02Z INFO Cleanup completed\n"
    ),
}


def _compile_path(path_template: str) -> re.Pattern[str]:
    pieces: list[str] = []
    cursor = 0
    for match in re.finditer(r"\{([A-Za-z][A-Za-z0-9]*)\}", path_template):
        pieces.append(re.escape(path_template[cursor : match.start()]))
        pieces.append(fr"(?P<{match.group(1)}>[^/]+)")
        cursor = match.end()
    pieces.append(re.escape(path_template[cursor:]))
    return re.compile("^" + "".join(pieces) + "$")


def load_routes() -> list[tuple[dict[str, Any], re.Pattern[str]]]:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    operations = contract.get("operations", [])
    ids = {operation.get("id") for operation in operations}
    if ids != EXPECTED_OPERATION_IDS:
        raise RuntimeError(f"contract operation IDs changed: {sorted(ids)}")
    if any(operation.get("method") != "GET" for operation in operations):
        raise RuntimeError("the diagnostic fixture contract must remain read-only")
    return [(operation, _compile_path(operation["path"])) for operation in operations]


ROUTES = load_routes()


class ContractMockServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        self.state = MockState()
        super().__init__(("127.0.0.1", 0), ContractHandler)

    @property
    def base_url(self) -> str:
        host, port = self.server_address
        return f"http://{host}:{port}"


class ContractHandler(BaseHTTPRequestHandler):
    server: ContractMockServer

    def do_GET(self) -> None:
        split = urlsplit(self.path)
        operation: dict[str, Any] | None = None
        parameters: dict[str, str] = {}
        for candidate, pattern in ROUTES:
            match = pattern.fullmatch(split.path)
            if match:
                operation = candidate
                parameters = {key: unquote(value) for key, value in match.groupdict().items()}
                break

        operation_id = operation["id"] if operation else None
        self.server.state.log(
            RequestRecord(
                method="GET",
                path=split.path,
                query=split.query,
                authorization=self.headers.get("Authorization"),
                operation_id=operation_id,
            )
        )

        if operation is None:
            self._json(404, {"message": "operation is not in docs/contract.json"})
            return
        if self.headers.get("Authorization") != "Bearer fixture-token":
            self._json(401, {"message": "missing or incorrect bearer token"})
            return
        request_id = parameters.get("requestId")
        if request_id not in {REQUEST_ID, DOWNLOAD_FAILURE_REQUEST_ID}:
            self._json(404, {"message": "request not found"})
            return

        if request_id == DOWNLOAD_FAILURE_REQUEST_ID:
            if operation_id == "getRequest":
                request = copy.deepcopy(REQUEST)
                request["id"] = DOWNLOAD_FAILURE_REQUEST_ID
                self._json(200, request)
                return
            if operation_id == "getRequestEvents":
                event = copy.deepcopy(EVENTS[0])
                event["id"] = "evt-download-failure"
                self._json(200, self._page([event]))
                return
            if parameters.get("eventId") != "evt-download-failure":
                self._json(404, {"message": "event log not found"})
                return
            if operation_id == "getEventLogs":
                self._json(200, self._slice([LOGS["evt-allocate"][0]]))
                return
            if operation_id == "getEventLogsContent":
                self._json(503, {"message": "download service unavailable"})
                return

        if operation_id == "getRequest":
            self._json(200, REQUEST)
            return
        if operation_id == "getRequestEvents":
            content = self.server.state.flip_collection(operation_id, EVENTS)
            self._json(200, self._page(content))
            return

        event_id = parameters.get("eventId", "")
        if event_id not in LOGS:
            self._json(404, {"message": "event log not found"})
            return
        if operation_id == "getEventLogs":
            content = self.server.state.flip_collection(f"{operation_id}:{event_id}", LOGS[event_id])
            self._json(200, self._slice(content))
            return
        if operation_id == "getEventLogsContent":
            self._bytes(200, DOWNLOADS[event_id].encode("utf-8"), "application/octet-stream")
            return
        self._json(500, {"message": "unhandled contract operation"})

    def do_POST(self) -> None:
        self._unsupported_method()

    def do_PUT(self) -> None:
        self._unsupported_method()

    def do_PATCH(self) -> None:
        self._unsupported_method()

    def do_DELETE(self) -> None:
        self._unsupported_method()

    def _unsupported_method(self) -> None:
        split = urlsplit(self.path)
        self.server.state.log(
            RequestRecord(
                method=self.command,
                path=split.path,
                query=split.query,
                authorization=self.headers.get("Authorization"),
                operation_id=None,
            )
        )
        self._json(405, {"message": "only contract GET operations are served"})

    @staticmethod
    def _page(content: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "content": content,
            "empty": not content,
            "first": True,
            "last": True,
            "number": 0,
            "numberOfElements": len(content),
            "size": len(content),
            "totalElements": len(content),
            "totalPages": 1,
        }

    @staticmethod
    def _slice(content: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "content": content,
            "empty": not content,
            "first": True,
            "last": True,
            "number": 0,
            "numberOfElements": len(content),
            "size": len(content),
        }

    def _json(self, status: int, value: Any) -> None:
        body = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self._bytes(status, body, "application/json")

    def _bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass
