"""Contract-pinned loopback VCF Automation server for protected verification."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
from threading import Lock, Thread
from typing import Iterator
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"


@dataclass(frozen=True, slots=True)
class RecordedRequest:
    method: str
    target: str
    headers: tuple[tuple[str, str], ...]
    body: bytes


@dataclass(frozen=True, slots=True)
class ResponseStep:
    operation_key: str
    status: int
    payload: object | bytes | None


DEFAULT_SCRIPT = (
    ResponseStep(
        "retrieveAuthToken",
        200,
        {"tokenType": "Bearer", "token": "expiring-access"},
    ),
    ResponseStep(
        "requestCatalogItemInstances_1",
        200,
        [
            {
                "deploymentId": "90010000-0000-4000-8000-000000000001",
                "deploymentName": "edge-cache-01",
            }
        ],
    ),
    ResponseStep(
        "getDeploymentById_1",
        401,
        {"message": "access token expired"},
    ),
    ResponseStep(
        "retrieveAuthToken",
        200,
        {"tokenType": "Bearer", "token": "fresh-access"},
    ),
    ResponseStep(
        "getDeploymentById_1",
        200,
        {
            "id": "90010000-0000-4000-8000-000000000001",
            "name": "edge-cache-01",
            "projectId": "project-42",
            "status": "CREATE_INPROGRESS",
        },
    ),
    ResponseStep(
        "getDeploymentById_1",
        200,
        {
            "id": "90010000-0000-4000-8000-000000000001",
            "name": "edge-cache-01",
            "projectId": "project-42",
            "status": "CREATE_SUCCESSFUL",
        },
    ),
)


def _template_pattern(template: str) -> re.Pattern[str]:
    pieces: list[str] = []
    position = 0
    for match in re.finditer(r"\{[^{}]+\}", template):
        pieces.append(re.escape(template[position : match.start()]))
        pieces.append(r"([^/?]+)")
        position = match.end()
    pieces.append(re.escape(template[position:]))
    return re.compile("^" + "".join(pieces) + "$")


class Scenario:
    def __init__(
        self,
        script: tuple[ResponseStep, ...] = DEFAULT_SCRIPT,
        contract_path: Path = CONTRACT_PATH,
    ) -> None:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        self._routes = tuple(
            (
                operation["key"],
                operation["method"],
                _template_pattern(operation["path"]),
            )
            for operation in contract["operations"]
        )
        self._lock = Lock()
        self._requests: list[RecordedRequest] = []
        self._script = script
        self._step = 0

    @property
    def allowed_operation_keys(self) -> tuple[str, ...]:
        return tuple(route[0] for route in self._routes)

    def record(self, request: RecordedRequest) -> None:
        with self._lock:
            self._requests.append(request)

    def requests(self) -> tuple[RecordedRequest, ...]:
        with self._lock:
            return tuple(self._requests)

    def match(self, method: str, path: str) -> tuple[str, tuple[str, ...]] | None:
        for key, expected_method, pattern in self._routes:
            match = pattern.fullmatch(path)
            if method == expected_method and match is not None:
                return key, match.groups()
        return None

    def response(
        self,
        operation_key: str,
        path_values: tuple[str, ...],
    ) -> tuple[int, object | bytes | None]:
        del path_values
        with self._lock:
            if self._step >= len(self._script):
                raise AssertionError(
                    f"unexpected request for exhausted scenario: {operation_key}"
                )
            step = self._script[self._step]
            self._step += 1
        if step.operation_key != operation_key:
            raise AssertionError(
                f"expected {step.operation_key}, received {operation_key}"
            )
        return step.status, step.payload

    def assert_consumed(self) -> None:
        with self._lock:
            consumed = self._step
        if consumed != len(self._script):
            raise AssertionError(
                f"scenario consumed {consumed} of {len(self._script)} responses"
            )


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        self._handle()

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        self._handle()

    def do_PUT(self) -> None:  # noqa: N802 - reject methods outside the contract
        self._handle()

    def do_PATCH(self) -> None:  # noqa: N802 - reject methods outside the contract
        self._handle()

    def do_DELETE(self) -> None:  # noqa: N802 - reject methods outside the contract
        self._handle()

    def _handle(self) -> None:
        size_text = self.headers.get("Content-Length", "0")
        try:
            size = int(size_text)
        except ValueError:
            size = 0
        body = self.rfile.read(max(0, size))
        recorded = RecordedRequest(
            self.command,
            self.path,
            tuple(self.headers.raw_items()),
            body,
        )
        self.server.scenario.record(recorded)  # type: ignore[attr-defined]

        parsed = urlsplit(self.path)
        matched = self.server.scenario.match(  # type: ignore[attr-defined]
            self.command, parsed.path
        )
        if matched is None:
            self._write(404, {"message": "operation is outside the focused contract"})
            return
        operation_key, path_values = matched
        status, payload = self.server.scenario.response(  # type: ignore[attr-defined]
            operation_key, path_values
        )
        self._write(status, payload)

    def _write(self, status: int, payload: object | bytes | None) -> None:
        body = (
            b""
            if payload is None
            else payload
            if isinstance(payload, bytes)
            else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        )
        self.send_response(status)
        if body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@contextmanager
def run_mock(
    script: tuple[ResponseStep, ...] = DEFAULT_SCRIPT,
) -> Iterator[tuple[str, Scenario]]:
    scenario = Scenario(script)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.scenario = scenario  # type: ignore[attr-defined]
    thread = Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}", scenario
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
