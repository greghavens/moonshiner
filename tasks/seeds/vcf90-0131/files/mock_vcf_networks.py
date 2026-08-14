"""Hermetic loopback server for the operations named in docs/contract.json."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


_ROOT = Path(__file__).resolve().parent
_CONTRACT = json.loads((_ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
_OPERATIONS = _CONTRACT["operations"]
_EXPECTED_OPERATION_IDS = {
    "updateCertificate",
    "fetchCertificateUpdateStatusForUpdateId",
}

if set(_OPERATIONS) != _EXPECTED_OPERATION_IDS:
    raise RuntimeError("loopback mock and pinned operation set disagree")


class _ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, terminal_status: str):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.request_log: list[dict[str, object]] = []
        self.update_id = "update 73/9"
        self.certificate_name = "proxy register/client.crt"
        self.statuses = ["IN_PROGRESS", terminal_status]
        self.status_index = 0


class _Handler(BaseHTTPRequestHandler):
    server: _ContractServer

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_PUT(self) -> None:
        body = self._read_body()
        self._record(body)
        operation = _OPERATIONS["updateCertificate"]
        prefix = _CONTRACT["server_url"] + operation["path"].split("{id}", 1)[0]
        parsed = urlsplit(self.path)
        encoded_id = parsed.path[len(prefix) :] if parsed.path.startswith(prefix) else ""
        if (
            parsed.query
            or not encoded_id
            or "/" in encoded_id
            or unquote(encoded_id) != self.server.certificate_name
        ):
            self._send_json(404, {"message": "operation not found"})
            return
        self._send_json(
            202,
            {
                "id": self.server.update_id,
                "name": self.server.certificate_name,
                "status": "SUBMITTED",
            },
        )

    def do_GET(self) -> None:
        body = self._read_body()
        self._record(body)
        operation = _OPERATIONS["fetchCertificateUpdateStatusForUpdateId"]
        prefix = _CONTRACT["server_url"] + operation["path"].split("{id}", 1)[0]
        parsed = urlsplit(self.path)
        encoded_id = parsed.path[len(prefix) :] if parsed.path.startswith(prefix) else ""
        if (
            parsed.query
            or not encoded_id
            or "/" in encoded_id
            or unquote(encoded_id) != self.server.update_id
        ):
            self._send_json(404, {"message": "operation not found"})
            return
        if self.server.status_index >= len(self.server.statuses):
            self._send_json(500, {"message": "poll continued after terminal state"})
            return
        status = self.server.statuses[self.server.status_index]
        self.server.status_index += 1
        response = {
            "id": self.server.update_id,
            "name": self.server.certificate_name,
            "status": status,
        }
        if status == "FAILED":
            response["error_message"] = "certificate update failed"
        self._send_json(200, response)

    def do_POST(self) -> None:
        self._unsupported()

    def do_PATCH(self) -> None:
        self._unsupported()

    def do_DELETE(self) -> None:
        self._unsupported()

    def _unsupported(self) -> None:
        body = self._read_body()
        self._record(body)
        self._send_json(404, {"message": "operation not found"})

    def _read_body(self) -> bytes:
        transfer_encoding = self.headers.get("Transfer-Encoding", "")
        if "chunked" in {
            item.strip().lower() for item in transfer_encoding.split(",")
        }:
            chunks: list[bytes] = []
            while True:
                size_line = self.rfile.readline()
                size = int(size_line.split(b";", 1)[0].strip(), 16)
                if size == 0:
                    while self.rfile.readline() not in {b"\r\n", b"\n", b""}:
                        pass
                    return b"".join(chunks)
                chunks.append(self.rfile.read(size))
                if self.rfile.read(2) != b"\r\n":
                    raise ValueError("malformed chunked request body")
        raw_length = self.headers.get("Content-Length")
        length = int(raw_length) if raw_length else 0
        return self.rfile.read(length) if length else b""

    def _record(self, body: bytes) -> None:
        self.server.request_log.append(
            {
                "method": self.command,
                "path": self.path,
                "headers": dict(self.headers.items()),
                "body": body,
            }
        )

    def _send_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class MockVcfOperationsForNetworks:
    """Context-managed mock exposing an in-process readable request log."""

    operation_ids = frozenset(_OPERATIONS)

    def __init__(self, *, terminal_status: str = "SUCCESS") -> None:
        terminal_states = set(
            _CONTRACT["schemas"]["CertificateUpdateStatus"]["properties"]["status"]["enum"]
        ) & {"SUCCESS", "FAILED"}
        if terminal_status not in terminal_states:
            raise ValueError("terminal_status must be SUCCESS or FAILED")
        self._server = _ContractServer(terminal_status)
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}{_CONTRACT['server_url']}"

    @property
    def request_log(self) -> list[dict[str, object]]:
        return self._server.request_log

    def __enter__(self) -> "MockVcfOperationsForNetworks":
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
