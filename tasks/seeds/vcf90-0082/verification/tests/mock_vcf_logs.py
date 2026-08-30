#!/usr/bin/env python3
import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


class ContractServer(HTTPServer):
    def __init__(
        self, address, handler, contract, request_log, failure_status, expire_after
    ):
        super().__init__(address, handler)
        base = contract["basePath"]
        self.operations = {
            (operation["method"], base + operation["path"]): operation
            for operation in contract["operations"]
        }
        self.forwarder_response_properties = set(
            contract["schemas"]["forwarders.get.response"]["properties"]
        )
        self.request_log = Path(request_log)
        self.log_lock = threading.Lock()
        self.session_number = 0
        self.first_token_successes = 0
        self.failure_status = failure_status
        self.expire_after = expire_after
        self.forwarders = []

    def record(self, entry):
        with self.log_lock:
            with self.request_log.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, separators=(",", ":"), sort_keys=True) + "\n")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format, *args):
        return

    def _body(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        return raw, json.loads(raw) if raw else None

    def _reply(self, status, payload):
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle(self):
        split = urlsplit(self.path)
        operation = self.server.operations.get((self.command, split.path))
        raw_body, body = self._body()
        entry = {
            "method": self.command,
            "target": self.path,
            "path": split.path,
            "query": parse_qs(split.query, keep_blank_values=True),
            "contentType": self.headers.get("Content-Type"),
            "authorization": self.headers.get("Authorization"),
            "bodyText": raw_body.decode("utf-8"),
            "body": body,
        }

        if operation is None:
            entry["operationId"] = None
            entry["responseStatus"] = 404
            self.server.record(entry)
            self._reply(404, {"errorMessage": "operation is outside the pinned contract"})
            return

        operation_id = operation["operationId"]
        entry["operationId"] = operation_id

        if operation_id == "POST_sessions":
            required = set(operation["request"]["required"])
            if not isinstance(body, dict) or not required.issubset(body):
                status, response = 400, {"errorMessage": "invalid session request"}
            else:
                self.server.session_number += 1
                status = operation["success"]["status"]
                response = {
                    "userId": "11111111-1111-4111-8111-111111111111",
                    "sessionId": f"session-{self.server.session_number}",
                    "ttl": 1800,
                }
            entry["responseStatus"] = status
            self.server.record(entry)
            self._reply(status, response)
            return

        token = self.headers.get("Authorization")
        if token == "Bearer session-1":
            if self.server.first_token_successes >= self.server.expire_after:
                entry["responseStatus"] = self.server.failure_status
                self.server.record(entry)
                self._reply(
                    self.server.failure_status,
                    {"errorMessage": "session expired or unauthorized"},
                )
                return
            self.server.first_token_successes += 1
        elif token != "Bearer session-2":
            entry["responseStatus"] = 401
            self.server.record(entry)
            self._reply(401, {"errorMessage": "not authenticated"})
            return

        if operation_id == "POST_log-forwarder":
            required = set(operation["request"]["required"])
            if not isinstance(body, dict) or not required.issubset(body):
                status, response = 400, {"errorMessage": "invalid forwarder request"}
            else:
                response = {
                    key: value
                    for key, value in body.items()
                    if key in self.server.forwarder_response_properties
                }
                response.update(
                    {
                        "workerCount": body.get("workerCount", 4),
                        "diskCacheSize": body.get("diskCacheSize", 1000000000),
                        "tags": body.get("tags", {}),
                        "filter": body.get("filter", ""),
                        "forwardComplementaryFields": body.get(
                            "forwardComplementaryFields", False
                        ),
                        "id": f"forwarder-{len(self.server.forwarders) + 1:03d}",
                    }
                )
                self.server.forwarders.append(response)
                status = operation["success"]["status"]
            entry["responseStatus"] = status
            self.server.record(entry)
            self._reply(status, response)
            return

        if operation_id == "GET_log-forwarder":
            entry["responseStatus"] = operation["success"]["status"]
            self.server.record(entry)
            self._reply(operation["success"]["status"], self.server.forwarders)
            return

        raise AssertionError(f"unimplemented contract operation: {operation_id}")

    do_GET = _handle
    do_POST = _handle


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True)
    parser.add_argument("--request-log", required=True)
    parser.add_argument("--port-file", required=True)
    parser.add_argument("--failure-status", required=True, type=int, choices=(401, 440))
    parser.add_argument("--expire-after", required=True, type=int, choices=(1, 2))
    args = parser.parse_args()

    contract = json.loads(Path(args.contract).read_text(encoding="utf-8"))
    expected = {"POST_sessions", "POST_log-forwarder", "GET_log-forwarder"}
    actual = {operation["operationId"] for operation in contract["operations"]}
    if actual != expected:
        raise SystemExit("contract operation set does not match the fixture")

    Path(args.request_log).write_text("", encoding="utf-8")
    server = ContractServer(
        ("127.0.0.1", 0),
        Handler,
        contract,
        args.request_log,
        args.failure_status,
        args.expire_after,
    )
    Path(args.port_file).write_text(str(server.server_port), encoding="ascii")
    server.serve_forever()


if __name__ == "__main__":
    main()
