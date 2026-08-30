#!/usr/bin/env python3
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


def load_routes(contract_path: Path):
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    operations = contract["operations"]
    expected_ids = {
        "POST_deployment-join",
        "POST_deployment-waitUntilStarted",
    }
    actual_ids = {operation["operationId"] for operation in operations}
    if actual_ids != expected_ids or len(operations) != len(expected_ids):
        raise RuntimeError("mock contract must name exactly the two deployment operations")
    base = contract["serverBasePath"]
    return {
        (operation["method"], base + operation["path"]): operation["operationId"]
        for operation in operations
    }


def make_handler(routes, request_log: Path, scenario: str):
    class Handler(BaseHTTPRequestHandler):
        wait_count = 0

        def do_POST(self):
            parsed = urlsplit(self.path)
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            entry = {
                "method": "POST",
                "path": parsed.path,
                "query": parsed.query,
                "headers": {key.lower(): value for key, value in self.headers.items()},
                "body": body.decode("utf-8"),
                "bodyLength": len(body),
            }
            with request_log.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, sort_keys=True) + "\n")

            operation_id = routes.get(("POST", parsed.path))
            if operation_id == "POST_deployment-join":
                if scenario == "join-error":
                    self._json_response(409, {
                        "errorMessage": "server is already configured"
                    })
                elif scenario == "success-escaped":
                    self._json_response(200, {
                        "masterAddress": 'primary "alpha" \\ path \u2603',
                        "workerAddress": "worker-\u03b2",
                        "workerPort": 16521,
                        "workerToken": 'token \\ slash " quote',
                        "masterUiPort": 8443,
                    })
                else:
                    self._json_response(200, {
                        "masterAddress": "192.0.2.10",
                        "workerAddress": "192.0.2.11",
                        "workerPort": 16520,
                        "workerToken": "worker-token-9-0",
                        "masterUiPort": 443,
                    })
                return
            if operation_id == "POST_deployment-waitUntilStarted":
                Handler.wait_count += 1
                if scenario == "wait-error":
                    self._json_response(503, {
                        "errorMessage": "unexpected downstream failure"
                    })
                elif scenario == "success-retries" and Handler.wait_count < 3:
                    self._json_response(500, {
                        "errorMessage": "server has not started yet"
                    })
                else:
                    self.send_response(200)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                return
            self._json_response(404, {"errorMessage": "operation not in contract"})

        def do_GET(self):
            self._json_response(404, {"errorMessage": "operation not in contract"})

        def do_PUT(self):
            self._json_response(404, {"errorMessage": "operation not in contract"})

        def do_DELETE(self):
            self._json_response(404, {"errorMessage": "operation not in contract"})

        def _json_response(self, status, payload):
            encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, _format, *_args):
            pass

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--request-log", required=True, type=Path)
    parser.add_argument("--port-file", required=True, type=Path)
    parser.add_argument(
        "--scenario",
        required=True,
        choices=("success-retries", "success-escaped", "join-error", "wait-error"),
    )
    args = parser.parse_args()

    routes = load_routes(args.contract)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(routes, args.request_log, args.scenario),
    )
    args.port_file.write_text(str(server.server_port), encoding="ascii")
    server.serve_forever()


if __name__ == "__main__":
    main()
