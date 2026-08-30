#!/usr/bin/env python3
"""Loopback mock of the four migration-upgrade operations in docs/contract.json.

The route table is built from the contract, so the mock answers only the
operations the contract names; anything else is a 404 Vapi.Std.Errors.NotFound.
Every request is appended to a JSON Lines request log that the scenario runner
reads back to assert the exact wire shape.

Protected fixture for vcf90-0040 - do not modify.
"""

from __future__ import annotations

import json
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qsl, urlsplit

GET_INIT_SPEC = "Vcenter.Lcm.Deployment.MigrationUpgrade_get"
APPLY = "Vcenter.Lcm.Deployment.MigrationUpgrade_apply"
GET_STATUS = "Vcenter.Lcm.Deployment.MigrationUpgrade.Status_get"
CANCEL = "Vcenter.Lcm.Deployment.MigrationUpgrade_cancel"

LOGGED_HEADERS = ("accept", "content-type", "content-length")


def message(identifier: str, text: str) -> dict[str, Any]:
    return {"id": identifier, "default_message": text, "args": []}


def error_envelope(error_type: str, identifier: str, text: str) -> dict[str, Any]:
    return {"error_type": error_type, "messages": [message(identifier, text)]}


class Scenario:
    """Scripted appliance state for one run."""

    def __init__(
        self,
        *,
        configured: bool = True,
        init_spec: dict[str, Any] | None = None,
        status_samples: list[dict[str, Any]] | None = None,
        post_cancel_samples: list[dict[str, Any]] | None = None,
    ) -> None:
        self.configured = configured
        self.init_spec = init_spec if init_spec is not None else default_init_spec()
        self.status_samples = list(status_samples or [])
        self.post_cancel_samples = list(post_cancel_samples or [])
        self.applied = False
        self.canceled = False
        self.applied_spec: Any = None
        self._index = 0

    def next_status(self) -> dict[str, Any]:
        samples = self.post_cancel_samples if self.canceled else self.status_samples
        if not samples:
            raise RuntimeError("scenario has no status samples")
        sample = samples[min(self._index, len(samples) - 1)]
        self._index += 1
        return sample

    def record_cancel(self) -> None:
        self.canceled = True
        self._index = 0


def default_init_spec() -> dict[str, Any]:
    """A Vcenter.Lcm.Deployment.MigrationUpgrade.InitSpec with masked password."""

    return {
        "version": "9.0.1.00000",
        "deployment": {
            "appliance_name": "vcenter-target-01",
            "root_password": "********",
        },
        "source_shutdown_policy": "ON_SUCCESSFUL_UPGRADE",
    }


def status_sample(
    status: str,
    current_state: str | None = None,
    *,
    identifier: str | None = "upg-8f21",
    upgrade_to: str = "9.0.1.00000",
    remaining_replication_data: int = 0,
    cancelable: bool = True,
    end_time: str | None = None,
    errors: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """Build a Vcenter.Lcm.Deployment.MigrationUpgrade.Status.Info body."""

    upgrade_info: dict[str, Any] = {
        "remaining_replication_data": remaining_replication_data,
    }
    if identifier is not None:
        upgrade_info["identifier"] = identifier
        upgrade_info["upgrade_to"] = upgrade_to

    sample: dict[str, Any] = {
        "status": status,
        "cancelable": cancelable,
        "description": message(
            "com.vmware.vcenter.lcm.deployment.migration_upgrade",
            "vCenter migration based upgrade",
        ),
        "last_update_time": "2026-03-14T01:15:00.000Z",
        "subtask_order": ["precheck", "deploy", "replicate", "switchover"],
        "subtasks": {},
        "upgrade_info": upgrade_info,
    }
    if current_state is not None:
        sample["current_state"] = current_state
    if end_time is not None:
        sample["end_time"] = end_time
    if errors:
        sample["notifications"] = {
            "errors": [
                {
                    "id": identifier_,
                    "message": message(identifier_, text),
                    "time": "2026-03-14T01:14:00.000Z",
                }
                for identifier_, text in errors
            ]
        }
    return sample


class MockVCenter:
    """A loopback HTTP server bound to the contract."""

    def __init__(
        self,
        contract_path: str | Path,
        log_path: str | Path,
        session_id: str,
        scenario: Scenario,
    ) -> None:
        self.contract = json.loads(Path(contract_path).read_text(encoding="utf-8"))
        self.log_path = Path(log_path)
        self.session_id = session_id
        self.scenario = scenario
        self.session_header = self.contract["security"]["name"].lower()
        self.routes = {
            (
                operation["method"].upper(),
                operation["path"],
                tuple(sorted((operation.get("query") or {}).items())),
            ): operation["operationId"]
            for operation in self.contract["operations"]
        }
        self._lock = threading.Lock()
        self._seq = 0
        self.log_path.write_text("", encoding="utf-8")
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> str:
        mock = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: Any) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802
                mock._handle(self, "GET")

            def do_POST(self) -> None:  # noqa: N802
                mock._handle(self, "POST")

            def do_PUT(self) -> None:  # noqa: N802
                mock._handle(self, "PUT")

            def do_DELETE(self) -> None:  # noqa: N802
                mock._handle(self, "DELETE")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def __enter__(self) -> "MockVCenter":
        self.base_url = self.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.stop()

    # -- request handling --------------------------------------------------

    def _handle(self, handler: BaseHTTPRequestHandler, method: str) -> None:
        parts = urlsplit(handler.path)
        query = tuple(sorted(parse_qsl(parts.query)))
        length = int(handler.headers.get("Content-Length") or 0)
        raw = handler.rfile.read(length) if length else b""
        operation_id = self.routes.get((method, parts.path, query))
        self._log(handler, method, parts.path, query, raw, operation_id)

        if handler.headers.get(self.session_header) != self.session_id:
            return self._respond(
                handler,
                401,
                error_envelope(
                    "UNAUTHENTICATED",
                    "com.vmware.vapi.endpoint.method.authentication.required",
                    "Authentication required.",
                ),
            )
        if operation_id is None:
            return self._respond(
                handler,
                404,
                error_envelope(
                    "NOT_FOUND",
                    "com.vmware.vapi.rest.uri.not.found",
                    f"No operation is served for {method} {handler.path}.",
                ),
            )
        return self._dispatch(handler, operation_id, raw)

    def _dispatch(
        self, handler: BaseHTTPRequestHandler, operation_id: str, raw: bytes
    ) -> None:
        scenario = self.scenario
        if operation_id == GET_INIT_SPEC:
            if not scenario.configured:
                return self._respond(
                    handler,
                    404,
                    error_envelope(
                        "NOT_FOUND",
                        "com.vmware.vcenter.lcm.deployment.upgrade.not.configured",
                        "There is no configured upgrade.",
                    ),
                )
            return self._respond(handler, 200, scenario.init_spec)

        if operation_id == APPLY:
            spec: Any = None
            if raw:
                try:
                    spec = json.loads(raw.decode("utf-8"))
                except ValueError:
                    return self._invalid(handler, "The request body is not valid JSON.")
                if not isinstance(spec, dict):
                    return self._invalid(handler, "ApplySpec must be a JSON object.")
                unknown = sorted(set(spec) - {"pause", "start_switchover"})
                if unknown:
                    return self._invalid(
                        handler, f"Unknown ApplySpec properties: {unknown}."
                    )
                pause = spec.get("pause")
                switchover = spec.get("start_switchover")
                if pause is not None and pause != "BEFORE_SWITCHOVER":
                    return self._invalid(handler, f"Unknown pause policy {pause!r}.")
                if pause is not None and switchover is not None:
                    return self._invalid(
                        handler,
                        "pause BEFORE_SWITCHOVER cannot be set with start_switchover.",
                    )
            scenario.applied = True
            scenario.applied_spec = spec
            return self._respond(handler, 204, None)

        if operation_id == GET_STATUS:
            return self._respond(handler, 200, scenario.next_status())

        if operation_id == CANCEL:
            if not scenario.applied:
                return self._respond(
                    handler,
                    400,
                    error_envelope(
                        "NOT_ALLOWED_IN_CURRENT_STATE",
                        "com.vmware.vcenter.lcm.deployment.upgrade.not.running",
                        "There is no upgrade process to cancel.",
                    ),
                )
            scenario.record_cancel()
            return self._respond(handler, 204, None)

        raise AssertionError(f"unhandled operation {operation_id}")

    def _invalid(self, handler: BaseHTTPRequestHandler, text: str) -> None:
        self._respond(
            handler,
            400,
            error_envelope(
                "INVALID_ARGUMENT",
                "com.vmware.vapi.std.errors.invalid_argument",
                text,
            ),
        )

    def _respond(
        self, handler: BaseHTTPRequestHandler, status: int, payload: Any
    ) -> None:
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        handler.send_response(status)
        if body:
            handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        if body:
            handler.wfile.write(body)

    def _log(
        self,
        handler: BaseHTTPRequestHandler,
        method: str,
        path: str,
        query: tuple[tuple[str, str], ...],
        raw: bytes,
        operation_id: str | None,
    ) -> None:
        text = raw.decode("utf-8", "replace")
        try:
            parsed = json.loads(text) if text else None
        except ValueError:
            parsed = None
        entry = {
            "operation_id": operation_id,
            "method": method,
            "path": path,
            "query": dict(query),
            "headers": {
                name: handler.headers.get(name) for name in LOGGED_HEADERS
            },
            "session_header": handler.headers.get(self.session_header),
            "body_bytes": len(raw),
            "body_text": text,
            "body_json": parsed,
        }
        with self._lock:
            entry["seq"] = self._seq
            self._seq += 1
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, sort_keys=True) + "\n")
                stream.flush()


def read_log(log_path: str | Path) -> list[dict[str, Any]]:
    """Read the request log back in arrival order."""

    lines = Path(log_path).read_text(encoding="utf-8").splitlines()
    entries = [json.loads(line) for line in lines if line.strip()]
    return sorted(entries, key=lambda entry: entry["seq"])
