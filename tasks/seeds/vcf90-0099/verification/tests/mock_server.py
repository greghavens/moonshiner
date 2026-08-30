#!/usr/bin/env python3
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
OPERATION_ID = CONTRACT["operationIds"][0]
OPERATION = CONTRACT["operations"][OPERATION_ID]
ALLOWED_TARGET = CONTRACT["serverBasePath"] + OPERATION["path"]
ALLOWED_PROPERTIES = set(OPERATION["requestBody"]["schema"]["properties"])
LOG_PATH = Path(sys.argv[1]).resolve()
MODE = sys.argv[2] if len(sys.argv) > 2 else "retry"
VALID_MODES = {"retry", "direct", "bad-request", "persistent-500"}


class ContractServer(ThreadingHTTPServer):
    current_representation = None
    request_count = 0
    mutation_count = 0


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_PUT(self):
        if OPERATION["method"] != "PUT" or self.path != ALLOWED_TARGET:
            self._reply(404, {"errorMessage": "operation not in pinned contract"})
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length).decode("utf-8")
        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError:
            self._reply(400, {"errorMessage": "invalid JSON"})
            return

        if not isinstance(body, dict) or not set(body).issubset(ALLOWED_PROPERTIES):
            self._reply(400, {"errorMessage": "body does not match pinned contract"})
            return
        if not isinstance(body.get("URLs"), list) or not all(
                isinstance(value, str) for value in body["URLs"]
        ):
            self._reply(400, {"errorMessage": "URLs must be an array of strings"})
            return
        if self.headers.get("Authorization", "").startswith("Bearer ") is False:
            self._reply(401, {"errorMessage": "missing bearer session"})
            return
        if self.headers.get_content_type() != OPERATION["requestBody"]["contentType"]:
            self._reply(400, {"errorMessage": "wrong media type"})
            return

        server = self.server
        server.request_count += 1
        if MODE == "bad-request":
            status = 400
        elif MODE == "persistent-500":
            status = 500
        elif MODE == "retry":
            status = 500 if server.request_count == 1 else 200
        else:
            status = 200

        if status in {200, 500} and server.current_representation != body:
            server.current_representation = body
            server.mutation_count += 1
        record = {
            "operationId": OPERATION_ID,
            "method": self.command,
            "target": self.path,
            "headers": {key.lower(): value for key, value in self.headers.items()},
            "body": raw_body,
            "status": status,
            "mutationCount": server.mutation_count,
        }
        with LOG_PATH.open("a", encoding="utf-8") as request_log:
            request_log.write(json.dumps(record, separators=(",", ":")) + "\n")

        if status == 500:
            self._reply(500, {"errorMessage": "operation outcome was not returned"})
        elif status == 400:
            self._reply(400, {"errorMessage": "request rejected by scenario"})
        else:
            self._reply(200, {"URLs": server.current_representation["URLs"]})

    def do_GET(self):
        self._reply(404, {"errorMessage": "operation not in pinned contract"})

    def do_POST(self):
        self._reply(404, {"errorMessage": "operation not in pinned contract"})

    def do_PATCH(self):
        self._reply(404, {"errorMessage": "operation not in pinned contract"})

    def do_DELETE(self):
        self._reply(404, {"errorMessage": "operation not in pinned contract"})

    def _reply(self, status, body):
        payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format, *_args):
        return


def main():
    if len(sys.argv) != 3 or MODE not in VALID_MODES:
        raise RuntimeError("usage: mock_server.py <request-log> <mode>")
    if len(CONTRACT["operationIds"]) != 1 or set(CONTRACT["operations"]) != {OPERATION_ID}:
        raise RuntimeError("mock requires a contract containing exactly one named operation")
    server = ContractServer(("127.0.0.1", 0), Handler)
    print(json.dumps({"port": server.server_address[1]}), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
