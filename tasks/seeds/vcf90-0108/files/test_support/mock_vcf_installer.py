"""Contract-pinned loopback server for the focused VCF Installer scenario."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread
from typing import Any
from urllib.parse import urlsplit


PINNED_TAG = "9.0.0.0"
PINNED_COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
PINNED_SPEC_PATH = "specifications/vcf-installer/vcf-installer-openapi.json"
EXPECTED_OPERATION_IDS = ["updateDepotSettings"]


class ContractMockServer:
    """Serve only the operations declared by the focused contract."""

    def __init__(
        self,
        contract_path: str | Path,
        *,
        drop_first_response: bool = True,
        response_status: int = 202,
        response_payload: Any = None,
    ):
        contract = json.loads(Path(contract_path).read_text(encoding="utf-8"))
        source = contract.get("source", {})
        if (
            source.get("tag") != PINNED_TAG
            or source.get("repositoryCommitSha") != PINNED_COMMIT
            or source.get("specPath") != PINNED_SPEC_PATH
        ):
            raise RuntimeError("mock contract source is not the pinned VCF 9.0 spec")
        operations = contract.get("operations", [])
        if [entry.get("operationId") for entry in operations] != EXPECTED_OPERATION_IDS:
            raise RuntimeError("mock contract operation set changed")
        self._routes = {
            entry["operationId"]: (entry["method"], entry["path"])
            for entry in operations
        }
        self.request_log: list[dict[str, Any]] = []
        self.settings: dict[str, Any] | None = None
        self.effect_count = 0
        self._drop_response = drop_first_response
        self._response_status = response_status
        self._response_payload = response_payload
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None

    @property
    def base_url(self) -> str:
        if self._httpd is None:
            raise RuntimeError("server is not running")
        host, port = self._httpd.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> "ContractMockServer":
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def _record(self) -> bytes:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length) if length else b""
                owner.request_log.append(
                    {
                        "method": self.command,
                        "target": self.path,
                        "headers": {
                            key.lower(): value for key, value in self.headers.items()
                        },
                        "body": body,
                    }
                )
                return body

            def _json_response(self, status: int, payload: Any) -> None:
                body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _not_found(self) -> None:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_PUT(self) -> None:
                body = self._record()
                route = owner._routes.get("updateDepotSettings")
                if (
                    route is None
                    or route[0] != "PUT"
                    or urlsplit(self.path).path != route[1]
                    or urlsplit(self.path).query
                ):
                    self._not_found()
                    return

                try:
                    payload = json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self.send_response(400)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return

                if owner._response_status != 202:
                    owner._json_scripted_response(self)
                    return

                if payload != owner.settings:
                    owner.settings = payload
                    owner.effect_count += 1

                if owner._drop_response:
                    owner._drop_response = False
                    self.close_connection = True
                    return

                response_payload = (
                    owner.settings
                    if owner._response_payload is None
                    else owner._response_payload
                )
                self._json_response(202, response_payload)

            def _reject_other_method(self) -> None:
                self._record()
                self._not_found()

            do_DELETE = _reject_other_method
            do_GET = _reject_other_method
            do_PATCH = _reject_other_method
            do_POST = _reject_other_method

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        assert self._httpd is not None
        assert self._thread is not None
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=2)
        self._httpd = None
        self._thread = None

    def _json_scripted_response(self, handler: BaseHTTPRequestHandler) -> None:
        status = self._response_status
        payload = {"status": status}
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
        # A buggy retry receives 202 next, so the verifier can report the extra
        # request promptly instead of waiting for its overall process timeout.
        self._response_status = 202
