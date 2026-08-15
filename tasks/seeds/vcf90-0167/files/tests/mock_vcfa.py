"""Contract-pinned loopback fixture for the VCF Automation operation under test."""

from __future__ import annotations

import json
import re
import threading
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote


class ContractMock:
    """Serves only the operations named by contract.json and exposes an in-memory log."""

    def __init__(self, contract_path: Path):
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        operations = contract["operations"]
        if len(operations) != 1:
            raise ValueError("this fixture expects exactly one named operation")

        operation = operations[0]
        self._method = operation["method"]
        template = operation["pathTemplate"]
        marker = "{deploymentId}"
        if template.count(marker) != 1:
            raise ValueError("contract path must contain one deploymentId parameter")
        prefix, suffix = template.split(marker)
        self._route = re.compile(
            "^" + re.escape(prefix) + r"(?P<deploymentId>[^/?]+)" + re.escape(suffix) + "$"
        )
        self._log: list[dict[str, object]] = []
        self._descriptions: dict[str, object] = {}
        self._effect_count = 0
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_uri(self) -> str:
        if self._server is None:
            raise RuntimeError("mock is not running")
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def request_log(self) -> list[dict[str, object]]:
        """Return a stable copy for the verifier; no log HTTP operation is exposed."""
        with self._lock:
            return deepcopy(self._log)

    @property
    def effect_count(self) -> int:
        with self._lock:
            return self._effect_count

    def start(self) -> None:
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _handle(self) -> None:
                target = self.path
                path = target.split("?", 1)[0]
                match = fixture._route.fullmatch(path)
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self.send_error(400)
                    return
                raw_body = self.rfile.read(length)
                header_log: dict[str, list[str]] = {}
                for key in self.headers.keys():
                    header_log[key.lower()] = self.headers.get_all(key) or []

                deployment_id = (
                    unquote(match.group("deploymentId")) if match is not None else None
                )
                with fixture._lock:
                    fixture._log.append(
                        {
                            "method": self.command,
                            "target": target,
                            "headers": header_log,
                            "body": raw_body,
                            "deploymentId": deployment_id,
                        }
                    )

                if self.command != fixture._method or match is None:
                    self.send_error(404)
                    return

                try:
                    body = json.loads(raw_body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self.send_error(400)
                    return
                if not isinstance(body, dict) or any(
                    not isinstance(key, str) or not isinstance(value, str)
                    for key, value in body.items()
                ):
                    self.send_error(400)
                    return

                with fixture._lock:
                    if "description" in body:
                        previous = fixture._descriptions.get(deployment_id, object())
                        if previous != body["description"]:
                            fixture._descriptions[deployment_id] = body["description"]
                            fixture._effect_count += 1

                response = json.dumps(
                    {
                        "id": deployment_id,
                        "name": "contract-fixture",
                        "description": body.get("description", ""),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("X-Contract-Mock", "patch-deployment")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                self._handle()

            def do_GET(self) -> None:  # noqa: N802
                self._handle()

            def do_POST(self) -> None:  # noqa: N802
                self._handle()

            def do_PUT(self) -> None:  # noqa: N802
                self._handle()

            def do_DELETE(self) -> None:  # noqa: N802
                self._handle()

            def log_message(self, _format: str, *args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None

    def __enter__(self) -> "ContractMock":
        self.start()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.stop()
