"""Contract-pinned loopback NSX Policy service for the acceptance verifier.

The service reads the allowed method/path templates from docs/contract.json,
binds only to 127.0.0.1 on an ephemeral port, and appends every attempted
contract request to a JSON-lines file.  It intentionally exposes no log or
control route over HTTP.
"""

from __future__ import annotations

import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit


def compile_contract(contract_path: Path):
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    base = contract["basePath"].rstrip("/")
    routes = []
    for operation in contract["operations"]:
        template = base + operation["path"]
        pattern = re.escape(template)
        pattern = re.sub(r"\\\{[^{}]+\\\}", r"[^/]+", pattern)
        routes.append(
            {
                "operationId": operation["operationId"],
                "method": operation["method"],
                "success": operation["success_status"],
                "regex": re.compile(r"^" + pattern + r"$"),
            }
        )
    return routes


class ContractServer(HTTPServer):
    def __init__(self, address, handler, routes, log_path):
        super().__init__(address, handler)
        self.routes = routes
        self.log_path = log_path
        self.completed = []

    def append_log(self, entry):
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())


class Handler(BaseHTTPRequestHandler):
    server: ContractServer

    def log_message(self, *_args):
        pass

    def _send(self, status):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _dispatch(self):
        target = self.path
        path = urlsplit(target).path
        route = next(
            (
                item
                for item in self.server.routes
                if item["method"] == self.command and item["regex"].fullmatch(path)
            ),
            None,
        )
        if route is None:
            self._send(404)
            return

        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        operation_id = route["operationId"]
        authorization = self.headers.get("Authorization")

        if operation_id == "PatchGroupForDomain":
            status = route["success"] if authorization == "Bearer access-1" else 401
            if status == route["success"]:
                self.server.completed.append(operation_id)
        elif operation_id == "PatchSecurityPolicyForDomain":
            ready = self.server.completed == ["PatchGroupForDomain"]
            status = (
                route["success"]
                if ready and authorization == "Bearer access-2"
                else 401
            )
            if status == route["success"]:
                self.server.completed.append(operation_id)
        else:
            status = 404

        self.server.append_log(
            {
                "operationId": operation_id,
                "method": self.command,
                "target": target,
                "authorization": authorization,
                "accept": self.headers.get("Accept"),
                "content_type": self.headers.get("Content-Type"),
                "body": body,
                "status": status,
            }
        )
        self._send(status)

    def do_PATCH(self):
        self._dispatch()

    def do_GET(self):
        self._send(404)

    def do_POST(self):
        self._send(404)

    def do_PUT(self):
        self._send(404)

    def do_DELETE(self):
        self._send(404)


def main():
    if len(sys.argv) != 4:
        raise SystemExit(
            "usage: mock_nsx_policy.py CONTRACT_JSON PORT_FILE LOG_FILE"
        )
    contract_path, port_path, log_path = map(Path, sys.argv[1:])
    routes = compile_contract(contract_path)
    log_path.write_text("", encoding="utf-8")
    server = ContractServer(("127.0.0.1", 0), Handler, routes, log_path)
    temporary = port_path.with_suffix(port_path.suffix + ".tmp")
    temporary.write_text(str(server.server_address[1]), encoding="ascii")
    os.replace(temporary, port_path)
    server.serve_forever()


if __name__ == "__main__":
    main()
