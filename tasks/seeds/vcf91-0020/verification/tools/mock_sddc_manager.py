"""Contract-pinned loopback SDDC Manager for protected verification.

This is an API fixture, not a harness-tool replacement. It has no default
appliance state: the verifier scripts every response. The route table is built
only from operationIds in the protected contract, and every request is written
to a verifier-readable JSONL log.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent.parent
CONTRACT = json.loads(
    (ROOT / "docs" / "contract.json").read_text(encoding="utf-8")
)


def _path_pattern(template: str) -> re.Pattern[str]:
    parts: list[str] = []
    cursor = 0
    for match in re.finditer(r"\{([A-Za-z][A-Za-z0-9_]*)\}", template):
        parts.append(re.escape(template[cursor : match.start()]))
        parts.append(f"(?P<{match.group(1)}>[^/]+)")
        cursor = match.end()
    parts.append(re.escape(template[cursor:]))
    return re.compile("^" + "".join(parts) + "$")


class MockSddcManager:
    """Scriptable localhost server limited to the pinned contract routes."""

    def __init__(self, access_token: str, request_log_path: Path) -> None:
        self.access_token = access_token
        self.request_log_path = request_log_path
        self.request_log_path.write_text("", encoding="utf-8")
        self._routes = [
            (
                operation["method"],
                _path_pattern(operation["path"]),
                operation["operationId"],
            )
            for operation in CONTRACT["operations"]
        ]
        self._scripts: dict[
            str,
            list[tuple[int, Any, str | None]],
        ] = {}
        self._lock = threading.RLock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def operation_ids(self) -> frozenset[str]:
        return frozenset(route[2] for route in self._routes)

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("mock server is not running")
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def script(
        self,
        operation_id: str,
        responses: list[
            tuple[int, Any] | tuple[int, Any, str | None]
        ],
    ) -> None:
        if operation_id not in self.operation_ids:
            raise KeyError(f"operation is outside the contract: {operation_id}")
        if not responses:
            raise ValueError("at least one response is required")
        normalized = []
        for response in responses:
            if len(response) == 2:
                status, payload = response
                media_type = None
            elif len(response) == 3:
                status, payload, media_type = response
            else:
                raise ValueError("script response must have two or three items")
            normalized.append((status, payload, media_type))
        with self._lock:
            self._scripts[operation_id] = normalized

    def read_request_log(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                json.loads(line)
                for line in self.request_log_path.read_text(
                    encoding="utf-8"
                ).splitlines()
                if line.strip()
            ]

    def _match(
        self,
        method: str,
        path: str,
    ) -> tuple[str | None, dict[str, str]]:
        for route_method, pattern, operation_id in self._routes:
            match = pattern.fullmatch(path)
            if method == route_method and match:
                return operation_id, match.groupdict()
        return None, {}

    def _take_response(
        self,
        operation_id: str,
    ) -> tuple[int, Any, str | None]:
        with self._lock:
            responses = self._scripts.get(operation_id)
            if not responses:
                return (
                    500,
                    {
                        "errorCode": "MOCK_RESPONSE_NOT_SCRIPTED",
                        "message": f"No response scripted for {operation_id}",
                    },
                    "application/json",
                )
            return responses.pop(0)

    def _append_log(self, record: dict[str, Any]) -> None:
        encoded = json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._lock:
            with self.request_log_path.open(
                "a",
                encoding="utf-8",
                newline="\n",
            ) as stream:
                stream.write(encoded + "\n")

    def __enter__(self) -> "MockSddcManager":
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802
                self._handle()

            def do_POST(self) -> None:  # noqa: N802
                self._handle()

            def do_PUT(self) -> None:  # noqa: N802
                self._handle()

            def do_PATCH(self) -> None:  # noqa: N802
                self._handle()

            def do_DELETE(self) -> None:  # noqa: N802
                self._handle()

            def _handle(self) -> None:
                parsed = urlsplit(self.path)
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                body = self.rfile.read(max(0, length))
                operation_id, path_parameters = fixture._match(
                    self.command,
                    parsed.path,
                )
                fixture._append_log(
                    {
                        "operationId": operation_id,
                        "method": self.command,
                        "target": self.path,
                        "path": parsed.path,
                        "query": parsed.query,
                        "pathParameters": path_parameters,
                        "headers": {
                            key.lower(): value
                            for key, value in self.headers.items()
                        },
                        "body": body.decode("utf-8", errors="replace"),
                    }
                )

                if operation_id is None:
                    status, payload, media_type = (
                        404,
                        {
                            "errorCode": "MOCK_ROUTE_NOT_IN_CONTRACT",
                            "message": "The requested route is outside the contract",
                        },
                        "application/json",
                    )
                elif self.headers.get("Authorization") != (
                    f"Bearer {fixture.access_token}"
                ):
                    status, payload, media_type = (
                        401,
                        {
                            "errorCode": "UNAUTHORIZED",
                            "message": "Bearer token required",
                        },
                        "application/json",
                    )
                else:
                    status, payload, media_type = fixture._take_response(
                        operation_id
                    )

                if isinstance(payload, bytes):
                    encoded = payload
                    default_media_type = "application/octet-stream"
                elif payload is None:
                    encoded = b""
                    default_media_type = None
                else:
                    encoded = json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    default_media_type = "application/json"

                self.send_response(status)
                response_type = media_type or default_media_type
                if response_type:
                    self.send_header("Content-Type", response_type)
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Connection", "close")
                self.end_headers()
                if encoded:
                    self.wfile.write(encoded)

            def log_message(self, format: str, *args: Any) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        return False
