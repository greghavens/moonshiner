#!/usr/bin/env python3
"""Contract-pinned loopback mock for the protected integration check."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit


EXPECTED_OPERATIONS = {
    "createToken": ("POST", "/v1/tokens"),
    "startBundleDownloadByID": ("PATCH", "/v1/bundles/{id}"),
    "getTask": ("GET", "/v1/tasks/{id}"),
    "refreshAccessToken": ("PATCH", "/v1/tokens/access-token/refresh"),
}


def load_contract(path: Path) -> dict[str, Any]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    actual = {
        operation["operationId"]: (operation["method"], operation["path"])
        for operation in contract["operations"]
    }
    if actual != EXPECTED_OPERATIONS:
        raise ValueError(f"mock contract operation set differs: {actual!r}")
    if contract.get("apiVersion") != "9.0.0.0":
        raise ValueError("mock requires the VCF Installer 9.0.0.0 contract")
    return contract


class ScenarioState:
    def __init__(self, request_log: Path, terminal_status: str) -> None:
        self.request_log = request_log
        self.terminal_status = terminal_status
        self.log_lock = threading.Lock()
        self.access_token: str | None = None
        self.refreshed_access_token: str | None = None
        self.refresh_token_id: str | None = None
        self.bundle_id: str | None = None
        self.task_id: str | None = None
        self.task_poll_count = 0
        self.initial_poll_expired = False

    def append_request(self, record: dict[str, Any]) -> None:
        encoded = json.dumps(record, sort_keys=True, separators=(",", ":"))
        with self.log_lock:
            with self.request_log.open("a", encoding="utf-8") as stream:
                stream.write(encoded + "\n")
                stream.flush()


class ContractHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "VcfInstallerContractMock/9.0"
    sys_version = ""

    @property
    def scenario(self) -> ScenarioState:
        return self.server.scenario  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch()

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch()

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch()

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch()

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        return self.rfile.read(length) if length > 0 else b""

    def _dispatch(self) -> None:
        body = self._read_body()
        parsed = urlsplit(self.path)
        headers = {name.lower(): value for name, value in self.headers.items()}
        self.scenario.append_request(
            {
                "method": self.command,
                "path": parsed.path,
                "query": parsed.query,
                "headers": headers,
                "body": body.decode("utf-8", errors="strict"),
                "bodyLength": len(body),
            }
        )

        if self.command == "POST" and parsed.path == "/v1/tokens":
            self._create_token(body)
            return
        if (
            self.command == "PATCH"
            and parsed.path == "/v1/tokens/access-token/refresh"
        ):
            self._refresh_access_token(body)
            return

        bundle_match = re.fullmatch(r"/v1/bundles/([^/]+)", parsed.path)
        if self.command == "PATCH" and bundle_match:
            self._start_bundle(unquote(bundle_match.group(1)), body)
            return

        task_match = re.fullmatch(r"/v1/tasks/([^/]+)", parsed.path)
        if self.command == "GET" and task_match:
            self._get_task(unquote(task_match.group(1)))
            return

        self._json_response(
            404,
            {"errorCode": "OPERATION_NOT_SERVED", "message": "Operation not served"},
        )

    def _create_token(self, body: bytes) -> None:
        try:
            document = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json_response(400, {"errorCode": "BAD_JSON", "message": "Bad JSON"})
            return
        if not isinstance(document, dict):
            self._json_response(400, {"errorCode": "BAD_SPEC", "message": "Bad spec"})
            return
        token_seed = hashlib.sha256(
            json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self.scenario.access_token = f"access-{token_seed[:20]}"
        self.scenario.refresh_token_id = f"refresh-{token_seed[20:40]}"
        self._json_response(
            201,
            {
                "accessToken": self.scenario.access_token,
                "refreshToken": {"id": self.scenario.refresh_token_id},
            },
        )

    def _start_bundle(self, bundle_id: str, body: bytes) -> None:
        if self.headers.get("Authorization") != f"Bearer {self.scenario.access_token}":
            self._json_response(
                401, {"errorCode": "TOKEN_EXPIRED", "message": "Access token expired"}
            )
            return
        try:
            json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json_response(400, {"errorCode": "BAD_JSON", "message": "Bad JSON"})
            return
        if self.scenario.bundle_id is not None:
            self._json_response(
                409, {"errorCode": "ALREADY_STARTED", "message": "Already started"}
            )
            return
        self.scenario.bundle_id = bundle_id
        task_seed = hashlib.sha256(bundle_id.encode("utf-8")).hexdigest()
        self.scenario.task_id = f"task-{task_seed[:20]}"
        self._json_response(202, self._task("PENDING"))

    def _get_task(self, task_id: str) -> None:
        if self.scenario.bundle_id is None or task_id != self.scenario.task_id:
            self._json_response(
                404, {"errorCode": "TASK_NOT_FOUND", "message": "Task not found"}
            )
            return
        authorization = self.headers.get("Authorization")
        if authorization == f"Bearer {self.scenario.access_token}":
            self.scenario.initial_poll_expired = True
            self._json_response(
                401, {"errorCode": "TOKEN_EXPIRED", "message": "Access token expired"}
            )
            return
        if authorization != f"Bearer {self.scenario.refreshed_access_token}":
            self._json_response(
                401, {"errorCode": "UNAUTHORIZED", "message": "Unauthorized"}
            )
            return
        self.scenario.task_poll_count += 1
        status = (
            "IN_PROGRESS"
            if self.scenario.task_poll_count == 1
            else self.scenario.terminal_status
        )
        self._json_response(200, self._task(status))

    def _refresh_access_token(self, body: bytes) -> None:
        if not self.scenario.initial_poll_expired:
            self._json_response(
                400, {"errorCode": "NOT_EXPIRED", "message": "Token is not expired"}
            )
            return
        try:
            refresh_id = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json_response(400, {"errorCode": "BAD_JSON", "message": "Bad JSON"})
            return
        if refresh_id != self.scenario.refresh_token_id:
            self._json_response(
                404, {"errorCode": "REFRESH_NOT_FOUND", "message": "Not found"}
            )
            return
        refresh_seed = hashlib.sha256(refresh_id.encode("utf-8")).hexdigest()
        self.scenario.refreshed_access_token = f"access-{refresh_seed[:20]}"
        self._json_response(200, self.scenario.refreshed_access_token)

    def _task(self, status: str) -> dict[str, str]:
        if self.scenario.task_id is None:
            raise RuntimeError("task response requested before work was accepted")
        task = {
            "id": self.scenario.task_id,
            "name": "Bundle download",
            "status": status,
            "creationTimestamp": "2026-01-02T00:00:00Z",
        }
        if status == "SUCCESSFUL":
            task["completionTimestamp"] = "2026-01-02T00:00:01Z"
        return task

    def _json_response(self, status: int, payload: Any) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--request-log", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument(
        "--terminal-status",
        choices=(
            "SUCCESSFUL",
            "FAILED",
            "CANCELLED",
            "COMPLETED_WITH_WARNING",
            "SKIPPED",
        ),
        required=True,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_contract(args.contract)
    args.request_log.parent.mkdir(parents=True, exist_ok=True)
    args.request_log.write_text("", encoding="utf-8")

    server = ThreadingHTTPServer(("127.0.0.1", 0), ContractHandler)
    server.scenario = ScenarioState(  # type: ignore[attr-defined]
        args.request_log,
        args.terminal_status,
    )
    host, port = server.server_address
    ready_document = {"baseUri": f"http://{host}:{port}"}
    args.ready_file.write_text(json.dumps(ready_document), encoding="utf-8")
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
