#!/usr/bin/env python3
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit


TASKS = (
    {
        "id": "task-z",
        "name": "Prepare \"VCF\"",
        "status": "IN_PROGRESS",
        "creationTimestamp": "2025-04-10T08:00:00Z",
        "isRetryable": False,
    },
    {
        "id": "task-a",
        "name": "Prepare hosts",
        "status": "SUCCESSFUL",
        "creationTimestamp": "2025-04-10T08:00:00Z",
    },
    {
        "id": "task-m",
        "name": "Validate network",
        "status": "SUCCESSFUL",
        "creationTimestamp": "2025-04-10T09:30:00Z",
    },
    {
        "id": "task-b",
        "name": "Deploy management domain",
        "status": "FAILED",
        "creationTimestamp": "2025-04-10T10:15:00Z",
    },
    {
        "id": "task-y",
        "name": "Finalize",
        "status": "SUCCESSFUL",
        "creationTimestamp": "2025-04-10T11:45:00Z",
    },
    {
        "id": "task-c",
        "name": "Archive logs",
        "status": "SUCCESSFUL",
        "creationTimestamp": "2025-04-10T12:00:00Z",
    },
)


def load_operation(contract_path):
    contract = json.loads(Path(contract_path).read_text(encoding="utf-8"))
    named = []
    selected = None
    for path, path_item in contract["paths"].items():
        for method, operation in path_item.items():
            if isinstance(operation, dict) and "operationId" in operation:
                named.append(operation["operationId"])
                selected = (method.upper(), path, operation)
    if named != ["getTasks"] or selected is None:
        raise ValueError("mock contract must name only getTasks")
    return selected


def make_handler(method, route, request_log):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format, *_args):
            return

        def _record(self, body):
            entry = {
                "method": self.command,
                "target": self.path,
                "headers": [[name, value] for name, value in self.headers.items()],
                "bodyLength": len(body),
            }
            with request_log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, separators=(",", ":")) + "\n")

        def _send_json(self, status, value):
            data = json.dumps(value, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            content_length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(content_length) if content_length else b""
            self._record(body)
            split = urlsplit(self.path)
            if method != "GET" or split.path != route:
                self._send_json(404, {"message": "not found"})
                return

            pairs = parse_qsl(split.query, keep_blank_values=True)
            values = {}
            for name, value in pairs:
                values.setdefault(name, []).append(value)
            required = {"pageNumber", "pageSize", "orderBy", "orderDirection"}
            if set(values) != required or any(len(item) != 1 for item in values.values()):
                self._send_json(400, {"message": "unexpected query"})
                return
            if (
                values["orderBy"] != ["creationTimestamp"]
                or values["orderDirection"] != ["ASC"]
            ):
                self._send_json(400, {"message": "unexpected query value"})
                return
            try:
                page_number = int(values["pageNumber"][0])
                page_size = int(values["pageSize"][0])
            except ValueError:
                self._send_json(400, {"message": "invalid pagination value"})
                return
            if page_number < 0 or page_size <= 0:
                self._send_json(400, {"message": "invalid pagination value"})
                return
            total_pages = (len(TASKS) + page_size - 1) // page_size
            start = page_number * page_size
            elements = TASKS[start : start + page_size]
            self._send_json(
                200,
                {
                    "elements": elements,
                    "pageMetadata": {
                        "pageNumber": page_number,
                        "pageSize": len(elements),
                        "totalPages": total_pages,
                    },
                },
            )

        def do_POST(self):
            content_length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(content_length) if content_length else b""
            self._record(body)
            self._send_json(405, {"message": "method not allowed"})

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True)
    parser.add_argument("--ready-file", required=True)
    parser.add_argument("--request-log", required=True)
    args = parser.parse_args()

    method, route, _operation = load_operation(args.contract)
    request_log = Path(args.request_log)
    request_log.write_text("", encoding="utf-8")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_handler(method, route, request_log)
    )
    Path(args.ready_file).write_text(str(server.server_port), encoding="ascii")
    server.serve_forever()


if __name__ == "__main__":
    main()
