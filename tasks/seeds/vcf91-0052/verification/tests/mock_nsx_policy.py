"""Contract-pinned loopback NSX Policy service for protected verification.

Only the operation declared in docs/contract.json is routed. The first valid
request is applied and then its connection is closed without a response. Every
contract request is appended to a JSONL file for out-of-band verification; the
service exposes no HTTP control, state, readiness, or log routes.
"""

from __future__ import annotations

import base64
import json
import os
import re
import socket
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


def compile_template(template: str):
    pieces: list[str] = []
    parameter_names: list[str] = []
    cursor = 0
    for index, match in enumerate(re.finditer(r"\{([^{}]+)\}", template)):
        pieces.append(re.escape(template[cursor : match.start()]))
        pieces.append(f"(?P<p{index}>[^/]+)")
        parameter_names.append(match.group(1))
        cursor = match.end()
    pieces.append(re.escape(template[cursor:]))
    return re.compile("^" + "".join(pieces) + "$"), parameter_names


def load_routes(contract_path: Path):
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    base_path = contract["basePath"].rstrip("/")
    routes = []
    for operation in contract["operations"]:
        template = base_path + operation["path"]
        pattern, parameter_names = compile_template(template)
        success_codes = sorted(
            int(code)
            for code in operation["responses"]
            if code.isdigit() and 200 <= int(code) < 300
        )
        if len(success_codes) != 1:
            raise ValueError("each mock operation must have one success response")
        routes.append(
            {
                "operation_id": operation["operationId"],
                "method": operation["method"],
                "pattern": pattern,
                "parameter_names": parameter_names,
                "success": success_codes[0],
            }
        )
    if not routes:
        raise ValueError("contract contains no operations")
    return routes


class ContractServer(HTTPServer):
    def __init__(self, address, handler, routes, log_path: Path, mode: str):
        super().__init__(address, handler)
        self.routes = routes
        self.log_path = log_path
        self.mode = mode
        self.request_count = 0
        self.resources: dict[tuple[tuple[str, str], ...], object] = {}
        self.effect_count = 0

    def append_log(self, entry):
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())


class Handler(BaseHTTPRequestHandler):
    server: ContractServer

    def log_message(self, *_args):
        pass

    def send_empty(self, status: int):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def dispatch(self):
        target = self.path
        split_target = urlsplit(target)
        route = None
        route_match = None
        if not split_target.query:
            for candidate in self.server.routes:
                match = candidate["pattern"].fullmatch(split_target.path)
                if candidate["method"] == self.command and match is not None:
                    route = candidate
                    route_match = match
                    break
        if route is None or route_match is None:
            self.send_empty(404)
            return

        length = int(self.headers.get("Content-Length", "0"))
        body_bytes = self.rfile.read(length)
        try:
            body_text = body_bytes.decode("utf-8", errors="strict")
            body_value = json.loads(body_text)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_empty(400)
            return

        parameter_values = [
            unquote(route_match.group(f"p{index}"))
            for index in range(len(route["parameter_names"]))
        ]
        resource_key = tuple(
            sorted(zip(route["parameter_names"], parameter_values))
        )
        self.server.request_count += 1
        first_request = self.server.request_count == 1
        should_drop = (
            self.server.mode == "drop-after-apply" and first_request
        )
        if self.server.mode == "always-400":
            status = 400
            changed = False
        elif self.server.mode == "transient-503" and first_request:
            status = 503
            changed = False
        else:
            status = None if should_drop else route["success"]
            changed = self.server.resources.get(resource_key) != body_value
            self.server.resources[resource_key] = body_value
            if changed:
                self.server.effect_count += 1
        outcome = "connection_dropped_after_apply" if should_drop else str(status)
        self.server.append_log(
            {
                "operationId": route["operation_id"],
                "method": self.command,
                "target": target,
                "path": split_target.path,
                "query": split_target.query,
                "authorization": self.headers.get("Authorization"),
                "accept": self.headers.get("Accept"),
                "content_type": self.headers.get("Content-Type"),
                "content_length": length,
                "body_utf8": body_text,
                "body_base64": base64.b64encode(body_bytes).decode("ascii"),
                "changed": changed,
                "resource_count": len(self.server.resources),
                "effect_count": self.server.effect_count,
                "status": status,
                "outcome": outcome,
            }
        )

        if should_drop:
            self.close_connection = True
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.connection.close()
            return
        self.send_empty(status)

    def do_PATCH(self):
        self.dispatch()

    def do_GET(self):
        self.send_empty(404)

    def do_POST(self):
        self.send_empty(404)

    def do_PUT(self):
        self.send_empty(404)

    def do_DELETE(self):
        self.send_empty(404)


def main():
    if len(sys.argv) not in (4, 5):
        raise SystemExit(
            "usage: mock_nsx_policy.py CONTRACT_JSON PORT_FILE LOG_FILE [MODE]"
        )
    contract_path, port_path, log_path = map(Path, sys.argv[1:4])
    mode = sys.argv[4] if len(sys.argv) == 5 else "drop-after-apply"
    if mode not in {"drop-after-apply", "transient-503", "always-400"}:
        raise SystemExit(f"unknown mock mode: {mode}")
    routes = load_routes(contract_path)
    log_path.write_text("", encoding="utf-8")
    server = ContractServer(
        ("127.0.0.1", 0), Handler, routes, log_path, mode
    )
    temporary = port_path.with_suffix(port_path.suffix + ".tmp")
    temporary.write_text(str(server.server_address[1]), encoding="ascii")
    os.replace(temporary, port_path)
    server.serve_forever()


if __name__ == "__main__":
    main()
