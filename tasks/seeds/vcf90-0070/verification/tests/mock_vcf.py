"""Contract-pinned loopback VCF Operations mock used by protected verification."""

from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class RequestRecord:
    method: str
    raw_target: str
    headers: tuple[tuple[str, str], ...]
    body: bytes


class ContractMock:
    """Serve only method/path pairs named by the focused OpenAPI contract."""

    def __init__(
        self,
        contract_path: Path,
        *,
        fail_delete: bool = True,
        failure_operation_id: str | None = None,
        malformed_create: str | None = None,
        disconnect_operation_id: str | None = None,
    ) -> None:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        server_path = contract["servers"][0]["url"].rstrip("/")
        allowed: dict[tuple[str, str], str] = {}
        for path, path_item in contract["paths"].items():
            for method, operation in path_item.items():
                if isinstance(operation, dict) and "operationId" in operation:
                    allowed[(method.upper(), server_path + path)] = operation["operationId"]

        self.allowed_operations = dict(allowed)
        self.failure_operation_id = failure_operation_id
        if self.failure_operation_id is None and fail_delete:
            self.failure_operation_id = "deleteMaintenanceSchedules"
        self.malformed_create = malformed_create
        self.disconnect_operation_id = disconnect_operation_id
        nonce = secrets.token_hex(12)
        self.token = f"vRealizeOpsToken runtime-{nonce}"
        self.created_id = f"00000000-0000-4000-8000-{nonce[:12]}"
        self.update_id = f"10000000-0000-4000-8000-{nonce[:12]}"
        self.retire_ids = (
            f"20000000-0000-4000-8000-{nonce[:12]}",
            f"30000000-0000-4000-8000-{nonce[:12]}",
        )
        self.failure_marker = f"private-response-{nonce}"
        self._records: list[RequestRecord] = []
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("mock is not running")
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    @property
    def request_log(self) -> tuple[RequestRecord, ...]:
        with self._lock:
            return tuple(self._records)

    def __enter__(self) -> "ContractMock":
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, _format: str, *args: object) -> None:
                return

            def do_POST(self) -> None:  # noqa: N802
                self._dispatch()

            def do_PUT(self) -> None:  # noqa: N802
                self._dispatch()

            def do_DELETE(self) -> None:  # noqa: N802
                self._dispatch()

            def do_GET(self) -> None:  # noqa: N802
                self._dispatch()

            def _dispatch(self) -> None:
                raw_length = self.headers.get("Content-Length")
                try:
                    length = int(raw_length) if raw_length is not None else 0
                except ValueError:
                    length = 0
                body = self.rfile.read(max(0, length))
                record = RequestRecord(
                    method=self.command,
                    raw_target=self.path,
                    headers=tuple(self.headers.raw_items()),
                    body=body,
                )
                with owner._lock:
                    owner._records.append(record)

                path = urlsplit(self.path).path
                operation_id = owner.allowed_operations.get((self.command, path))
                if operation_id is None:
                    self._json_response(404, {"message": "operation not in focused contract"})
                    return

                if operation_id == owner.disconnect_operation_id:
                    self.close_connection = True
                    return

                if operation_id == owner.failure_operation_id:
                    if operation_id == "createMaintenanceSchedules":
                        status = 422
                    elif operation_id == "updateMaintenanceSchedules":
                        status = 404
                    else:
                        status = 503
                    self._json_response(
                        status,
                        {
                            "message": "configured operation failure",
                            "detail": owner.failure_marker,
                        },
                    )
                    return

                if operation_id == "createMaintenanceSchedules":
                    if owner.malformed_create == "invalid-json":
                        self._bytes_response(
                            201,
                            b'{"detail":"'
                            + owner.failure_marker.encode("ascii")
                            + b'",',
                        )
                        return
                    if owner.malformed_create == "invalid-id":
                        self._json_response(
                            201,
                            {"id": "  ", "detail": owner.failure_marker},
                        )
                        return
                    try:
                        request_value = json.loads(body.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        request_value = {}
                    response_value = {"id": owner.created_id}
                    if isinstance(request_value, dict):
                        response_value.update(request_value)
                    self._json_response(201, response_value)
                    return

                if operation_id == "updateMaintenanceSchedules":
                    self._empty_response(200)
                    return

                if operation_id == "deleteMaintenanceSchedules":
                    self._empty_response(204)
                    return

                self._json_response(500, {"message": "unhandled named operation"})

            def _empty_response(self, status: int) -> None:
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True

            def _json_response(self, status: int, value: object) -> None:
                payload = json.dumps(
                    value,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                self._bytes_response(status, payload)

            def _bytes_response(self, status: int, payload: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(payload)
                self.close_connection = True

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None
