"""Contract-pinned loopback vCenter mock for protected verification."""

from __future__ import annotations

import base64
from contextlib import AbstractContextManager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
from typing import Any
from urllib.parse import quote


CLONE_OPERATION = "Vcenter.VM_clone$Task"
TASK_LIST_OPERATION = "Cis.Tasks_list"


def _target(base_path: str, operation: dict[str, Any]) -> str:
    query = "&".join(
        f"{quote(item['name'], safe='')}={quote(item['value'], safe='')}"
        for item in operation["fixed_query"]
    )
    return base_path + operation["path"] + ("?" + query if query else "")


class ContractMock(AbstractContextManager["ContractMock"]):
    """Serve only the two operations named by the focused contract."""

    def __init__(
        self,
        contract_path: Path,
        request_log: Path,
        *,
        task_ids: list[str],
        rounds: list[dict[str, str]],
        results: dict[str, Any],
        task_errors: dict[str, Any] | None = None,
        clone_error: Any = None,
    ) -> None:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        operations = {
            operation["operationId"]: operation
            for operation in contract["operations"]
        }
        if set(operations) != {CLONE_OPERATION, TASK_LIST_OPERATION}:
            raise ValueError("mock and focused contract operation sets differ")
        if not task_ids:
            raise ValueError("task_ids must not be empty")
        if not rounds and clone_error is None:
            raise ValueError("rounds must not be empty")

        self._clone = operations[CLONE_OPERATION]
        self._list = operations[TASK_LIST_OPERATION]
        base_path = contract["server_base_path"]
        self._clone_target = _target(base_path, self._clone)
        self._list_target = _target(base_path, self._list)
        self._request_log = request_log
        self._task_ids = list(task_ids)
        self._rounds = [dict(item) for item in rounds]
        self._results = dict(results)
        self._task_errors = dict(task_errors or {})
        self._clone_error = clone_error
        self._clone_index = 0
        self._poll_index = 0
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

            def do_PATCH(self) -> None:  # noqa: N802
                owner._handle(self)

            def do_PUT(self) -> None:  # noqa: N802
                owner._handle(self)

            def do_DELETE(self) -> None:  # noqa: N802
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
        response_order: list[str] | None = None
        status = 404
        payload: Any = {"error": "route not present in focused contract"}

        if (
            handler.command == self._clone["method"]
            and raw_target == self._clone_target
        ):
            operation_id = CLONE_OPERATION
            with self._lock:
                if self._clone_error is not None:
                    status = 503
                    payload = self._clone_error
                elif self._clone_index >= len(self._task_ids):
                    status = 409
                    payload = {"error": "too many clone submissions"}
                else:
                    status = self._clone["success"]["status"]
                    payload = self._task_ids[self._clone_index]
                    self._clone_index += 1
        elif (
            handler.command == self._list["method"]
            and raw_target == self._list_target
        ):
            operation_id = TASK_LIST_OPERATION
            with self._lock:
                if self._clone_index != len(self._task_ids):
                    status = 409
                    payload = {"error": "polling began before all submissions"}
                else:
                    round_index = min(
                        self._poll_index, len(self._rounds) - 1
                    )
                    states = self._rounds[round_index]
                    response_order = list(dict.fromkeys(self._task_ids))
                    if self._poll_index % 2 == 1:
                        response_order.reverse()
                    self._poll_index += 1
                    task_map: dict[str, Any] = {}
                    for task_id in response_order:
                        state = states[task_id]
                        info: dict[str, Any] = {
                            "description": {
                                "id": "com.vmware.vcenter.vm.clone",
                                "default_message": "Clone virtual machine",
                                "args": [task_id],
                            },
                            "service": "com.vmware.vcenter.vm",
                            "operation": "clone",
                            "status": state,
                            "cancelable": False,
                        }
                        if state == "SUCCEEDED":
                            info["result"] = self._results.get(task_id)
                        elif state == "FAILED":
                            info["error"] = self._task_errors.get(task_id)
                        task_map[task_id] = info
                    status = self._list["success"]["status"]
                    payload = task_map

        self._record(
            handler=handler,
            operation_id=operation_id,
            raw_target=raw_target,
            body=body,
            response_order=response_order,
        )
        self._respond(handler, status, payload)

    def _record(
        self,
        *,
        handler: BaseHTTPRequestHandler,
        operation_id: str | None,
        raw_target: str,
        body: bytes,
        response_order: list[str] | None,
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
                "response_element_order": response_order,
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
