"""Loopback HTTP mock whose routes are loaded from docs/contract.json."""

from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
from threading import Lock, Thread
from typing import Any, Callable
from urllib.parse import unquote, urlsplit


@dataclass(frozen=True)
class RequestRecord:
    operation_id: str | None
    method: str
    path: str
    query: str
    headers: dict[str, str]
    body: bytes
    path_parameters: dict[str, str]


Responder = Callable[[str, RequestRecord], tuple[int, Any]]


def _compile_path(path_template: str) -> re.Pattern[str]:
    cursor = 0
    pieces: list[str] = ["^"]
    for match in re.finditer(r"\{([^{}]+)\}", path_template):
        pieces.append(re.escape(path_template[cursor : match.start()]))
        pieces.append(f"(?P<{match.group(1)}>[^/]+)")
        cursor = match.end()
    pieces.append(re.escape(path_template[cursor:]))
    pieces.append("$")
    return re.compile("".join(pieces))


class _ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, contract: dict[str, Any], responder: Responder) -> None:
        self.routes = [
            (
                operation["operationId"],
                operation["method"],
                _compile_path(operation["path"]),
            )
            for operation in contract["operations"]
        ]
        self.responder = responder
        self.request_log: list[RequestRecord] = []
        self.log_lock = Lock()
        super().__init__(("127.0.0.1", 0), _ContractHandler)


class _ContractHandler(BaseHTTPRequestHandler):
    server: _ContractServer

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def _dispatch(self) -> None:
        split = urlsplit(self.path)
        operation_id: str | None = None
        path_parameters: dict[str, str] = {}
        for candidate_id, method, pattern in self.server.routes:
            match = pattern.fullmatch(split.path)
            if method == self.command and match is not None:
                operation_id = candidate_id
                path_parameters = {
                    key: unquote(value) for key, value in match.groupdict().items()
                }
                break

        length = int(self.headers.get("Content-Length", "0"))
        record = RequestRecord(
            operation_id=operation_id,
            method=self.command,
            path=split.path,
            query=split.query,
            headers={key.lower(): value for key, value in self.headers.items()},
            body=self.rfile.read(length),
            path_parameters=path_parameters,
        )
        with self.server.log_lock:
            self.server.request_log.append(record)

        if operation_id is None:
            self._write_json(404, {"error": "operation is not in contract"})
            return

        status, payload = self.server.responder(operation_id, record)
        self._write_json(status, payload)

    def _write_json(self, status: int, payload: Any) -> None:
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        return


class ContractMock:
    """Context-managed loopback server exposing exactly the contract routes."""

    def __init__(self, contract_path: Path, responder: Responder) -> None:
        with contract_path.open(encoding="utf-8") as handle:
            contract = json.load(handle)
        self._server = _ContractServer(contract, responder)
        self._thread: Thread | None = None

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    @property
    def request_log(self) -> list[RequestRecord]:
        with self._server.log_lock:
            return list(self._server.request_log)

    def __enter__(self) -> "ContractMock":
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join()
