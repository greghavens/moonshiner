"""Contract-pinned loopback vCenter mock for the protected verifier."""

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


POWER_OPERATION = "Vcenter.Vm.Power_get"
UPDATE_OPERATION = "Vcenter.Vm.Hardware.Cpu_update"


def _compile_path(template: str) -> re.Pattern[str]:
    marker = re.escape("{vm}")
    expression = re.escape(template).replace(marker, r"(?P<vm>[^/?]+)")
    return re.compile(rf"^{expression}$")


class ContractMock(AbstractContextManager["ContractMock"]):
    """Serve only the two operations in the focused OpenAPI projection."""

    def __init__(
        self,
        contract_path: Path,
        request_log: Path,
        *,
        power_payload: Any,
        power_status: int = 200,
        update_status: int = 204,
        update_payload: Any = None,
    ) -> None:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        operations = {
            operation["operationId"]: operation
            for operation in contract["operations"]
        }
        if set(operations) != {POWER_OPERATION, UPDATE_OPERATION}:
            raise ValueError("mock operation set does not match focused contract")

        self._power = operations[POWER_OPERATION]
        self._update = operations[UPDATE_OPERATION]
        self._power_path = _compile_path(self._power["path"])
        self._update_path = _compile_path(self._update["path"])
        self._request_log = request_log
        self._power_payload = power_payload
        self._power_status = power_status
        self._update_status = update_status
        self._update_payload = update_payload
        self._gate_open = False
        self._mutation_count = 0
        self._sequence = 0
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802
                owner._handle(self)

            def do_PATCH(self) -> None:  # noqa: N802
                owner._handle(self)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    @property
    def mutation_count(self) -> int:
        with self._lock:
            return self._mutation_count

    def __enter__(self) -> "ContractMock":
        self._request_log.parent.mkdir(parents=True, exist_ok=True)
        self._request_log.write_text("", encoding="utf-8")
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="contract-vcenter-cpu-mock",
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
            handler.command == self._power["method"]
            and self._power_path.fullmatch(raw_target)
        ):
            operation_id = POWER_OPERATION
            status = self._power_status
            payload = self._power_payload
            with self._lock:
                self._gate_open = (
                    status == 200
                    and isinstance(payload, dict)
                    and payload.get("state") == "POWERED_OFF"
                )
        elif (
            handler.command == self._update["method"]
            and self._update_path.fullmatch(raw_target)
        ):
            operation_id = UPDATE_OPERATION
            with self._lock:
                self._mutation_count += 1
                gate_open = self._gate_open
            if gate_open:
                status = self._update_status
                payload = self._update_payload
            else:
                status = 409
                payload = {"error": "power-state precheck did not pass"}

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
        handler: BaseHTTPRequestHandler,
        status: int,
        payload: Any,
    ) -> None:
        if status == 204 and payload is None:
            raw = b""
        else:
            raw = json.dumps(
                payload,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")

        handler.send_response(status)
        if raw:
            handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(raw)))
        handler.end_headers()
        if raw:
            handler.wfile.write(raw)
            handler.wfile.flush()
