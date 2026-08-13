#!/usr/bin/env python3
import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


EXPECTED_OPERATION_IDS = {
    "createAgentSecret",
    "listAgentSecrets",
    "createAgentSession",
}


def load_routes(contract_path: Path):
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    routes = {}
    operation_ids = set()
    for path, path_item in contract["paths"].items():
        for method, operation in path_item.items():
            if not isinstance(operation, dict) or "operationId" not in operation:
                continue
            key = (method.upper(), path)
            routes[key] = operation["operationId"]
            operation_ids.add(operation["operationId"])
    if operation_ids != EXPECTED_OPERATION_IDS:
        raise RuntimeError(f"contract operationIds changed: {sorted(operation_ids)}")
    return routes


class ContractServer(ThreadingHTTPServer):
    def __init__(self, address, handler, routes, request_log):
        super().__init__(address, handler)
        self.routes = routes
        self.request_log = request_log
        self.log_lock = threading.Lock()
        self.secret = None
        self.secret_name = None
        self.secret_id = None
        self.poll_count = 0

    def record(self, entry):
        with self.log_lock:
            with self.request_log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")


class Handler(BaseHTTPRequestHandler):
    server_version = "VcfContractMock/1.0"

    def log_message(self, _format, *_args):
        pass

    def _handle(self):
        parsed = urlsplit(self.path)
        operation_id = self.server.routes.get((self.command, parsed.path))
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length)
        self.server.record(
            {
                "operationId": operation_id,
                "method": self.command,
                "target": self.path,
                "path": parsed.path,
                "query": parsed.query,
                "headers": {
                    key.lower(): value for key, value in self.headers.items()
                },
                "body": raw_body.decode("utf-8"),
            }
        )

        if operation_id is None:
            self._json_response(404, {"errorCode": "API_ERROR", "errorMessage": "route not in contract"})
            return

        try:
            body = json.loads(raw_body) if raw_body else None
        except json.JSONDecodeError:
            self._json_response(400, {"errorCode": "JSON_FORMAT_ERROR", "errorMessage": "invalid JSON"})
            return

        if operation_id == "createAgentSecret":
            self.server.secret_name = body.get("name") if isinstance(body, dict) else None
            self.server.secret = "secret-once-42"
            self.server.secret_id = "secret-id-91"
            self.server.poll_count = 0
            self._json_response(
                201,
                {
                    "id": self.server.secret_id,
                    "name": self.server.secret_name,
                    "secret": self.server.secret,
                    "status": "PENDING",
                },
            )
            return

        if operation_id == "listAgentSecrets":
            if self.server.secret_id is None:
                self._json_response(400, {"errorCode": "AGENT_ERROR", "errorMessage": "no secret created"})
                return
            self.server.poll_count += 1
            status = "ACTIVE" if self.server.poll_count >= 2 else "PENDING"
            self._json_response(
                200,
                {
                    "id": self.server.secret_id,
                    "modificationTime": "2026-05-13T00:00:00Z",
                    "name": self.server.secret_name,
                    "status": status,
                },
            )
            return

        if operation_id == "createAgentSession":
            if not isinstance(body, dict) or body.get("secret") != self.server.secret:
                self._json_response(400, {"errorCode": "AGENT_ERROR", "errorMessage": "invalid secret"})
                return
            self._json_response(
                200,
                {
                    "access_token": "agent-access-91",
                    "name": self.server.secret_name,
                    "new_secret": "rotated-secret-43",
                    "ttl": 1_800_000,
                },
            )
            return

        self._json_response(500, {"errorCode": "INTERNAL_SERVER_ERROR"})

    def _json_response(self, status, body):
        payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle
    do_PATCH = _handle
    do_DELETE = _handle


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--request-log", type=Path, required=True)
    parser.add_argument("--port-file", type=Path, required=True)
    args = parser.parse_args()

    routes = load_routes(args.contract)
    args.request_log.write_text("", encoding="utf-8")
    server = ContractServer(("127.0.0.1", 0), Handler, routes, args.request_log)
    args.port_file.write_text(str(server.server_port), encoding="ascii")
    server.serve_forever(poll_interval=0.05)


if __name__ == "__main__":
    main()
