"""A loopback-only HTTP fixture pinned to docs/contract.json.

This is an API fixture, not a replacement or interceptor for harness tools.  It
binds to 127.0.0.1 on an ephemeral port and exposes only the two operations in
the checked-in contract subset.
"""

from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
from threading import Thread
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit


@dataclass(frozen=True)
class RequestRecord:
    method: str
    raw_path: str
    path: str
    query: str
    headers: dict[str, str]
    raw_body: bytes


class ContractMock:
    """Context-managed VCF Installer mock with a readable request log."""

    _EXPECTED_OPERATIONS = {
        "startBundleDownloadByID": ("PATCH", "/v1/bundles/{id}"),
        "getTask": ("GET", "/v1/tasks/{id}"),
    }

    def __init__(
        self,
        poll_statuses: Iterable[str] = ("IN_PROGRESS", "SUCCESSFUL"),
        *,
        task_id: str = "task-bundle-download-91",
        start_response: tuple[int, Any] | None = None,
        task_responses: Iterable[tuple[int, Any]] | None = None,
    ):
        self.poll_statuses = tuple(poll_statuses)
        self.task_responses = (
            tuple(task_responses) if task_responses is not None else None
        )
        if not self.poll_statuses and self.task_responses is None:
            raise ValueError("poll_statuses must not be empty")
        if self.task_responses is not None and not self.task_responses:
            raise ValueError("task_responses must not be empty")
        self.start_response = start_response
        self.request_log: list[RequestRecord] = []
        self._poll_index = 0
        self._task_id = task_id
        self._server: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None
        self._load_and_check_contract()

    def _load_and_check_contract(self) -> None:
        contract_path = Path(__file__).resolve().parents[1] / "docs" / "contract.json"
        self.contract = json.loads(contract_path.read_text(encoding="utf-8"))
        actual = {
            operation["operationId"]: (operation["method"], operation["path"])
            for operation in self.contract["operations"]
        }
        if actual != self._EXPECTED_OPERATIONS:
            raise RuntimeError("mock and docs/contract.json operations are out of sync")

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("mock is not running")
        return f"http://127.0.0.1:{self._server.server_port}"

    def __enter__(self) -> "ContractMock":
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def _read_and_record(self) -> RequestRecord:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length) if length else b""
                parsed = urlsplit(self.path)
                record = RequestRecord(
                    method=self.command,
                    raw_path=self.path,
                    path=parsed.path,
                    query=parsed.query,
                    headers={key.lower(): value for key, value in self.headers.items()},
                    raw_body=body,
                )
                owner.request_log.append(record)
                return record

            def _json(self, status: int, value: dict[str, Any]) -> None:
                body = json.dumps(value, separators=(",", ":")).encode("utf-8")
                self._bytes(status, body)

            def _bytes(self, status: int, body: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _response(self, response: tuple[int, Any]) -> None:
                status, value = response
                if isinstance(value, bytes):
                    self._bytes(status, value)
                else:
                    self._json(status, value)

            def _not_found(self) -> None:
                self._read_and_record()
                self._json(404, {"message": "operation is not in the pinned contract"})

            def do_PATCH(self) -> None:  # noqa: N802 - stdlib handler API
                record = self._read_and_record()
                match = re.fullmatch(r"/v1/bundles/([^/]+)", record.path)
                if match is None or record.query:
                    self._json(404, {"message": "operation is not in the pinned contract"})
                    return
                try:
                    payload = json.loads(record.raw_body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._json(400, {"message": "request body must be JSON"})
                    return
                if not self._valid_bundle_update(payload):
                    self._json(400, {"message": "request does not match BundleUpdateSpec"})
                    return
                unquote(match.group(1))  # Exercise decoding without constraining fixture IDs.
                if owner.start_response is None:
                    self._json(202, owner._task("PENDING"))
                else:
                    self._response(owner.start_response)

            def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
                record = self._read_and_record()
                match = re.fullmatch(r"/v1/tasks/([^/]+)", record.path)
                if match is None or record.query or record.raw_body:
                    self._json(404, {"message": "operation is not in the pinned contract"})
                    return
                if unquote(match.group(1)) != owner._task_id:
                    self._json(404, {"message": "task not found"})
                    return
                if owner.task_responses is not None:
                    response = owner.task_responses[
                        min(owner._poll_index, len(owner.task_responses) - 1)
                    ]
                    owner._poll_index += 1
                    self._response(response)
                    return
                status = owner.poll_statuses[
                    min(owner._poll_index, len(owner.poll_statuses) - 1)
                ]
                owner._poll_index += 1
                self._json(200, owner._task(status))

            def _valid_bundle_update(self, value: Any) -> bool:
                if not isinstance(value, dict) or set(value) != {"bundleDownloadSpec"}:
                    return False
                inner = value["bundleDownloadSpec"]
                if not isinstance(inner, dict):
                    return False
                types = {
                    "scheduledTimestamp": str,
                    "downloadNow": bool,
                    "cancelNow": bool,
                }
                return all(key in types and type(item) is types[key] for key, item in inner.items())

            do_POST = _not_found
            do_PUT = _not_found
            do_DELETE = _not_found

            def log_message(self, format: str, *args: Any) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._server = None
        self._thread = None

    def _task(self, status: str) -> dict[str, Any]:
        task = {
            "id": self._task_id,
            "name": "Download bundle",
            "status": status,
            "creationTimestamp": "2026-05-13T10:00:00Z",
        }
        normalized = status.strip().upper().replace(" ", "_")
        if normalized not in {"PENDING", "IN_PROGRESS", "QUEUED"}:
            task["completionTimestamp"] = "2026-05-13T10:00:03Z"
        return task
