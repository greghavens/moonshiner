"""Loopback-only mock for the operations in docs/contract.json."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Lock, Thread
from urllib.parse import urlsplit


_CONTRACT_PATH = Path(__file__).resolve().parent / "docs" / "contract.json"
_JOIN_RESPONSE = {
    "masterAddress": "192.0.2.10",
    "workerAddress": "192.0.2.11",
    "workerPort": 16520,
    "workerToken": "worker-token-0086",
    "masterUiPort": 443,
}


class VCFLogsMock:
    """Context-managed HTTP mock with an in-memory request log."""

    def __init__(
        self,
        retryable_polls: int = 2,
        join_status: int = 200,
        poll_error_status: int | None = None,
    ) -> None:
        self.retryable_polls = retryable_polls
        self.join_status = join_status
        self.poll_error_status = poll_error_status
        self._requests: list[dict] = []
        self._request_lock = Lock()
        self._polls = 0
        self._server: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None

        contract = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))
        prefix = contract["servers"][0]["url"].rstrip("/")
        self.operations = {
            operation["operationId"]: {
                "method": operation["method"],
                "path": prefix + operation["path"],
            }
            for operation in contract["operations"]
        }
        self._allowed = {
            (operation["method"], operation["path"])
            for operation in self.operations.values()
        }

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("mock server is not running")
        return f"http://127.0.0.1:{self._server.server_port}"

    @property
    def request_log(self) -> list[dict]:
        with self._request_lock:
            return [
                {**entry, "headers": dict(entry["headers"])}
                for entry in self._requests
            ]

    def __enter__(self) -> "VCFLogsMock":
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:
                self._dispatch()

            def do_POST(self) -> None:
                self._dispatch()

            def do_PUT(self) -> None:
                self._dispatch()

            def do_DELETE(self) -> None:
                self._dispatch()

            def log_message(self, format: str, *args: object) -> None:
                return

            def _dispatch(self) -> None:
                split = urlsplit(self.path)
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length) if length else b""
                record = {
                    "method": self.command,
                    "path": split.path,
                    "query": split.query,
                    "headers": {key.lower(): value for key, value in self.headers.items()},
                    "body": body,
                }
                with owner._request_lock:
                    owner._requests.append(record)

                if (self.command, split.path) not in owner._allowed:
                    self._send(404, {"errorMessage": "No such contract operation."})
                    return

                join = owner.operations["POST_deployment-join"]
                wait = owner.operations["POST_deployment-waitUntilStarted"]
                if split.path == join["path"]:
                    self._handle_join(body)
                elif split.path == wait["path"]:
                    self._handle_wait(body)

            def _handle_join(self, body: bytes) -> None:
                try:
                    payload = json.loads(body)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._send(400, {"errorMessage": "Invalid request body."})
                    return
                if not isinstance(payload, dict) or not isinstance(
                    payload.get("masterFQDN"), str
                ):
                    self._send(400, {"errorMessage": "Invalid request body."})
                    return
                if owner.join_status != 200:
                    self._send(
                        owner.join_status,
                        {"errorMessage": "The join request was rejected."},
                    )
                    return
                self._send(200, _JOIN_RESPONSE)

            def _handle_wait(self, body: bytes) -> None:
                if body:
                    self._send(400, {"errorMessage": "This operation has no body."})
                    return
                owner._polls += 1
                if owner.poll_error_status is not None and owner._polls == 1:
                    self._send(
                        owner.poll_error_status,
                        {"errorMessage": "The status request failed."},
                    )
                    return
                if owner._polls <= owner.retryable_polls:
                    self._send(
                        500,
                        {"errorMessage": "The server has not started yet."},
                    )
                    return
                self._send(200)

            def _send(self, status: int, payload: dict | None = None) -> None:
                raw = b"" if payload is None else json.dumps(
                    payload, separators=(",", ":"), sort_keys=True
                ).encode("utf-8")
                self.send_response(status)
                if payload is not None:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                if raw:
                    self.wfile.write(raw)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._server = None
        self._thread = None
