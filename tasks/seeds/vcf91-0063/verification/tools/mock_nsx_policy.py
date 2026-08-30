"""Contract-pinned loopback service for the NSX Policy acceptance verifier.

The server builds its complete route table from docs/contract.json, binds only
to 127.0.0.1 on an ephemeral port, and appends every attempted request to a
filesystem JSONL log. It intentionally exposes no HTTP control or log route.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit


def compile_contract(contract_path: Path) -> list[dict[str, object]]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    base_path = contract["basePath"].rstrip("/")
    routes: list[dict[str, object]] = []
    for operation in contract["operations"]:
        template = base_path + operation["path"]
        pattern = re.escape(template)
        pattern = re.sub(r"\\\{[^{}]+\\\}", r"[^/]+", pattern)
        routes.append(
            {
                "operationId": operation["operationId"],
                "method": operation["method"],
                "regex": re.compile(r"^" + pattern + r"$"),
                "success_status": int(
                    next(
                        status
                        for status in operation["responses"]
                        if status.startswith("2")
                    )
                ),
            }
        )
    return routes


class ContractServer(HTTPServer):
    routes: list[dict[str, object]]

    def __init__(
        self,
        address: tuple[str, int],
        routes: list[dict[str, object]],
        log_path: Path,
        expected_authorization: str,
    ) -> None:
        super().__init__(address, Handler)
        self.routes = routes
        self.log_path = log_path
        self.expected_authorization = expected_authorization
        self.group_committed = False
        self.request_number = 0

    def append_log(self, entry: dict[str, object]) -> None:
        self.request_number += 1
        entry["request_number"] = self.request_number
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())


class Handler(BaseHTTPRequestHandler):
    server: ContractServer
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args: object) -> None:
        pass

    def _send(
        self,
        status: int,
        envelope: dict[str, object] | None = None,
    ) -> None:
        if envelope is None:
            body = b""
        else:
            body = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        if body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if body:
            self.wfile.write(body)
        self.close_connection = True

    def _dispatch(self) -> None:
        target = self.path
        request_path = urlsplit(target).path
        route = next(
            (
                item
                for item in self.server.routes
                if item["method"] == self.command
                and item["regex"].fullmatch(request_path)  # type: ignore[union-attr]
            ),
            None,
        )

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        body = self.rfile.read(max(length, 0))
        authorization = self.headers.get("Authorization")
        operation_id = route["operationId"] if route is not None else None

        if route is None:
            status = 404
            envelope: dict[str, object] | None = {
                "error_code": 40400,
                "error_message": "operation is outside the pinned contract",
                "module_name": "Policy",
            }
        elif authorization != self.server.expected_authorization:
            status = 403
            envelope = {
                "error_code": 40301,
                "error_message": "authentication failed",
                "module_name": "Common",
            }
        elif operation_id == "PatchGroupForDomain":
            status = int(route["success_status"])
            envelope = None
            self.server.group_committed = True
        elif (
            operation_id == "PatchSecurityPolicyForDomain"
            and self.server.group_committed
        ):
            status = 503
            envelope = {
                "error_code": 73001,
                "error_message": "security policy application is unavailable",
                "module_name": "Policy",
                "details": "the source group change remains committed",
            }
        else:
            status = 412
            envelope = {
                "error_code": 41200,
                "error_message": "source group has not committed",
                "module_name": "Policy",
            }

        self.server.append_log(
            {
                "operationId": operation_id,
                "method": self.command,
                "target": target,
                "authorization": authorization,
                "accept": self.headers.get("Accept"),
                "content_type": self.headers.get("Content-Type"),
                "content_length": self.headers.get("Content-Length"),
                "body_base64": base64.b64encode(body).decode("ascii"),
                "status": status,
            }
        )
        self._send(status, envelope)

    do_DELETE = _dispatch
    do_GET = _dispatch
    do_PATCH = _dispatch
    do_POST = _dispatch
    do_PUT = _dispatch


def main() -> None:
    if len(sys.argv) != 6:
        raise SystemExit(
            "usage: mock_nsx_policy.py CONTRACT_JSON PORT_FILE LOG_FILE "
            "USERNAME PASSWORD"
        )
    contract_path = Path(sys.argv[1])
    port_path = Path(sys.argv[2])
    log_path = Path(sys.argv[3])
    username = sys.argv[4]
    password = sys.argv[5]
    credentials = f"{username}:{password}".encode("utf-8")
    expected_authorization = (
        "Basic " + base64.b64encode(credentials).decode("ascii")
    )

    routes = compile_contract(contract_path)
    log_path.write_text("", encoding="utf-8")
    server = ContractServer(
        ("127.0.0.1", 0),
        routes,
        log_path,
        expected_authorization,
    )
    temporary = port_path.with_suffix(port_path.suffix + ".tmp")
    temporary.write_text(str(server.server_address[1]), encoding="ascii")
    os.replace(temporary, port_path)
    server.serve_forever()


if __name__ == "__main__":
    main()
