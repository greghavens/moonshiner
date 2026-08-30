"""TLS loopback VCF Installer mock constrained by docs/contract.json."""

from __future__ import annotations

import hashlib
import json
import math
import ssl
import threading
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


EXPECTED_OPERATIONS = {
    "createToken": ("POST", "/v1/tokens"),
    "getTasks": ("GET", "/v1/tasks"),
}
# Connect-VcfInstallerServer 13.5.0 performs this product/version probe after
# createToken. It is SDK connectivity behavior rather than an operation used by
# the submission, so it is kept separate from the spec-derived contract subset.
SDK_CONNECTION_OPERATION = ("sdkConnectionProbe", "GET", "/v1/sddc-manager")
SDK_DISCONNECT_OPERATION = (
    "sdkDisconnect",
    "DELETE",
    "/v1/tokens/refresh-token",
)
ACCESS_TOKEN = "moonshiner-loopback-access-token"


def build_tasks() -> list[dict[str, object]]:
    """Create deterministic, deliberately cross-page-unsorted task records."""
    base = datetime(2026, 5, 13, 9, 0, tzinfo=timezone.utc)
    records: list[dict[str, object]] = []
    for index in range(7):
        digest = hashlib.sha256(f"vcf91-getTasks-{index}".encode()).hexdigest()[:12]
        created = base + timedelta(minutes=(index * 3) % 4)
        completed = created + timedelta(minutes=8 + index)
        records.append(
            {
                "id": f"task-{digest}",
                "name": f"Contract task {index}",
                "type": "HOST_COMMISSION",
                "status": "FAILED",
                "creationTimestamp": created.isoformat(timespec="seconds").replace("+00:00", "Z"),
                "completionTimestamp": completed.isoformat(timespec="seconds").replace("+00:00", "Z"),
                "isCancellable": False,
                "isRetryable": True,
            }
        )
    return records


class _ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, *, contract: dict, request_log: Path):
        super().__init__(address, handler)
        self.contract = contract
        self.request_log = request_log
        self.log_lock = threading.Lock()
        self.tasks = build_tasks()
        self.routes = {
            value: operation_id for operation_id, value in EXPECTED_OPERATIONS.items()
        }
        for operation_id, method, path in (
            SDK_CONNECTION_OPERATION,
            SDK_DISCONNECT_OPERATION,
        ):
            self.routes[(method, path)] = operation_id

    def append_request(self, record: dict[str, object]) -> None:
        with self.log_lock:
            with self.request_log.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, sort_keys=True) + "\n")


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "VCFInstallerContractMock/1.0"

    def _handle(self) -> None:
        split = urlsplit(self.path)
        operation_id = self.server.routes.get((self.command, split.path))
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length) if content_length else b""
        self.server.append_request(
            {
                "method": self.command,
                "rawTarget": self.path,
                "path": split.path,
                "query": parse_qs(split.query, keep_blank_values=True),
                "headers": {key.casefold(): value for key, value in self.headers.items()},
                "body": body.decode("utf-8", errors="replace"),
                "operationId": operation_id,
            }
        )

        if operation_id is None:
            self._json(404, {"error": "operation is not present in the pinned contract"})
            return
        if operation_id == "createToken":
            self._json(
                201,
                {
                    "accessToken": ACCESS_TOKEN,
                    "refreshToken": {"id": "loopback-refresh-token"},
                },
            )
            return
        if operation_id == SDK_CONNECTION_OPERATION[0]:
            self._json(200, {"version": "9.1.0.0.25372366"})
            return
        if operation_id == SDK_DISCONNECT_OPERATION[0]:
            self._empty(204)
            return
        query = parse_qs(split.query, keep_blank_values=True)
        task_name = query.get("taskName", [""])[0]
        if task_name == "MOONSHINER_MISSING_METADATA":
            self._json(200, {"elements": []})
            return
        if task_name == "MOONSHINER_WRONG_PAGE":
            self._json(
                200,
                {
                    "elements": [],
                    "pageMetadata": {
                        "pageNumber": 1,
                        "pageSize": 0,
                        "totalElements": 0,
                        "totalPages": 1,
                    },
                },
            )
            return
        try:
            page_number = int(query.get("pageNumber", ["0"])[0])
            page_size = int(query.get("pageSize", ["100"])[0])
            if page_number < 0 or page_size < 1 or page_size > 100:
                raise ValueError
        except (TypeError, ValueError):
            self._json(400, {"error": "invalid pagination"})
            return
        first = page_number * page_size
        elements = self.server.tasks[first : first + page_size]
        total_pages = math.ceil(len(self.server.tasks) / page_size)
        self._json(
            200,
            {
                "elements": elements,
                "pageMetadata": {
                    "pageNumber": page_number,
                    "pageSize": len(elements),
                    "totalElements": len(self.server.tasks),
                    "totalPages": total_pages,
                },
            },
        )

    def _json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _empty(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle
    do_PATCH = _handle
    do_DELETE = _handle

    def log_message(self, _format: str, *args: object) -> None:
        return


class ContractPinnedVcfInstaller(AbstractContextManager):
    """Run the contract-pinned mock and retain its JSONL request log."""

    def __init__(self, contract_path: Path, request_log: Path, cert: Path, key: Path):
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        actual = {
            operation_id: (operation["method"], operation["path"])
            for operation_id, operation in contract["operations"].items()
        }
        if actual != EXPECTED_OPERATIONS:
            raise ValueError(f"contract operation mismatch: {actual!r}")
        request_log.write_text("", encoding="utf-8")
        self.server = _ContractServer(
            ("127.0.0.1", 0), _Handler, contract=contract, request_log=request_log
        )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=cert, keyfile=key)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.request_log = request_log

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        return False

    def requests(self) -> list[dict[str, object]]:
        return [
            json.loads(line)
            for line in self.request_log.read_text(encoding="utf-8").splitlines()
            if line
        ]
