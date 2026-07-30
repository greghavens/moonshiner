"""Contract-pinned loopback vCenter mock used by the protected verifier."""

from __future__ import annotations

import base64
from contextlib import AbstractContextManager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import threading
from typing import Any


CREATE_OPERATION = (
    "Vcenter.NamespaceManagement.Supervisors.Recovery.Backup.Jobs_create"
)
GET_TASK_OPERATION = "Cis.Tasks_get"


def _compile_path(base_path: str, template: str, parameter: str) -> re.Pattern[str]:
    marker = re.escape("{" + parameter + "}")
    escaped = re.escape(base_path + template)
    expression = escaped.replace(marker, rf"(?P<{parameter}>[^/?]+)")
    return re.compile(rf"^{expression}$")


class ContractMock(AbstractContextManager["ContractMock"]):
    """Serve only the two operations present in the focused contract."""

    def __init__(
        self,
        contract_path: Path,
        request_log: Path,
        *,
        task_id: str,
        states: list[str],
        result: Any,
        failed_info: Any = None,
    ) -> None:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        operations = {
            operation["operationId"]: operation
            for operation in contract["operations"]
        }
        required = {CREATE_OPERATION, GET_TASK_OPERATION}
        if set(operations) != required:
            raise ValueError("focused contract operation set does not match the mock")
        if not states:
            raise ValueError("states must not be empty")

        self._base_path = contract["server_base_path"]
        self._create = operations[CREATE_OPERATION]
        self._get = operations[GET_TASK_OPERATION]
        self._create_path = _compile_path(
            self._base_path, self._create["path"], "supervisor"
        )
        self._get_path = _compile_path(
            self._base_path, self._get["path"], "task"
        )
        self._request_log = request_log
        self._task_id = task_id
        self._states = list(states)
        self._result = result
        self._failed_info = failed_info
        self._poll_index = 0
        self._submitted = False
        self._sequence = 0
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802
                owner._handle(self)

            def do_GET(self) -> None:  # noqa: N802
                owner._handle(self)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> "ContractMock":
        self._request_log.parent.mkdir(parents=True, exist_ok=True)
        self._request_log.write_text("", encoding="utf-8")
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="contract-vcenter-mock",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server.server_close()

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        length_text = handler.headers.get("Content-Length")
        try:
            length = int(length_text) if length_text is not None else 0
        except ValueError:
            length = 0
        body = handler.rfile.read(length) if length else b""
        raw_target = handler.path

        operation_id: str | None = None
        status = 404
        payload: Any = {"error": "route not present in focused contract"}

        if (
            handler.command == self._create["method"]
            and self._create_path.fullmatch(raw_target)
        ):
            operation_id = CREATE_OPERATION
            with self._lock:
                if not self._submitted:
                    self._submitted = True
                    status = self._create["success"]["status"]
                    payload = self._task_id
                else:
                    status = 409
                    payload = {"error": "backup was submitted more than once"}
        elif (
            handler.command == self._get["method"]
            and self._get_path.fullmatch(raw_target)
        ):
            operation_id = GET_TASK_OPERATION
            with self._lock:
                state = self._states[min(self._poll_index, len(self._states) - 1)]
                self._poll_index += 1
            task_info: dict[str, Any] = {
                "description": {
                    "id": "com.vmware.vcenter.supervisor.backup",
                    "default_message": "Supervisor backup",
                    "args": [],
                },
                "service": (
                    "com.vmware.vcenter.namespace_management.supervisors."
                    "recovery.backup.jobs"
                ),
                "operation": "create",
                "status": state,
                "cancelable": False,
            }
            if state == "SUCCEEDED":
                task_info["result"] = self._result
            if state == "FAILED":
                task_info["error"] = self._failed_info
            status = self._get["success"]["status"]
            payload = task_info

        self._record(
            handler=handler,
            operation_id=operation_id,
            raw_target=raw_target,
            body=body,
        )
        self._respond(handler, status, payload)

    def _record(
        self,
        *,
        handler: BaseHTTPRequestHandler,
        operation_id: str | None,
        raw_target: str,
        body: bytes,
    ) -> None:
        with self._lock:
            self._sequence += 1
            record = {
                "sequence": self._sequence,
                "operation_id": operation_id,
                "method": handler.command,
                "raw_target": raw_target,
                "headers": list(handler.headers.items()),
                "body_length": len(body),
                "body_base64": base64.b64encode(body).decode("ascii"),
            }
            encoded = (
                json.dumps(record, separators=(",", ":"), ensure_ascii=False)
                + "\n"
            )
            with self._request_log.open("a", encoding="utf-8") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())

    @staticmethod
    def _respond(
        handler: BaseHTTPRequestHandler, status: int, payload: Any
    ) -> None:
        raw = json.dumps(
            payload, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(raw)))
        handler.end_headers()
        handler.wfile.write(raw)
        handler.wfile.flush()
