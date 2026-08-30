#!/usr/bin/env python3
"""Loopback mock of the VCF 9.1 SDDC LCM task API, pinned to docs/contract.json.

The routing table is derived *only* from the operations named in the contract projection; every
other method/path pair is rejected as out-of-contract. Each received request is appended to a JSONL
log that is flushed and fsynced before the response is written, so the verifier can read the exact
wire shape of everything the client sent.

Usage:
    mock_sddc_lcm.py <contract.json> <fixture.json> <requests.jsonl> <port-file>
"""

import base64
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

MAX_PAGE_SIZE = 50  # "Page Size. Maximum allowed is 50." -- pageSizeQueryParam in the spec


def build_routes(contract):
    """Turn contract operations into (method, compiled regex, operationId, param-names)."""
    routes = []
    for op in contract["operations"]:
        path = op["path"]
        names = []

        def sub(m):
            names.append(m.group(1))
            return r"(?P<%s>[^/]+)" % m.group(1)

        pattern = "^" + re.sub(r"\{([^}]+)\}", sub, re.sub(r"([.^$*+?()\[\]|\\])", r"\\\1", path)) + "$"
        allowed_query = [p["name"] for p in op["parameters"] if p["in"] == "query"]
        routes.append(
            {
                "method": op["method"],
                "regex": re.compile(pattern),
                "operationId": op["operationId"],
                "pathParams": names,
                "queryParams": allowed_query,
            }
        )
    return routes


class Recorder:
    def __init__(self, path):
        self._path = path
        self._lock = threading.Lock()
        self._seq = 0
        open(path, "w").close()

    def record(self, entry):
        with self._lock:
            self._seq += 1
            entry["seq"] = self._seq
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            return self._seq


def make_handler(contract, fixture, recorder):
    routes = build_routes(contract)
    expected_auth = "Bearer " + fixture["bearerToken"]
    tasks = fixture["tasks"]
    details = fixture["details"]

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "MockSddcLcm/9.1"
        sys_version = ""

        def log_message(self, *_args):
            pass

        # ------------------------------------------------------------ plumbing

        def _headers_multimap(self):
            out = {}
            for name, value in self.headers.items():
                out.setdefault(name.lower(), []).append(value)
            return out

        def _read_body(self):
            length = self.headers.get("Content-Length")
            if length:
                return self.rfile.read(int(length))
            return b""

        def _respond(self, status, payload, entry):
            body = json.dumps(payload).encode("utf-8") if payload is not None else b""
            entry["status"] = status
            recorder.record(entry)
            self.send_response(status)
            if body:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.close_connection = False
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _error(self, status, code, message, entry):
            self._respond(
                status,
                {
                    "code": code,
                    "message": {"id": code, "defaultMessage": message, "localizedMessage": message},
                    "resolution": {"id": code + ".resolution", "defaultMessage": message,
                                   "localizedMessage": message},
                    "referenceId": "mock-%d" % entry.get("seq", 0),
                    "timestamp": "2026-06-30T00:00:00.000Z",
                },
                entry,
            )

        # ------------------------------------------------------------- routing

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def do_PUT(self):
            self._handle("PUT")

        def do_PATCH(self):
            self._handle("PATCH")

        def do_DELETE(self):
            self._handle("DELETE")

        def _handle(self, method):
            split = urlsplit(self.path)
            body = self._read_body()
            entry = {
                "method": method,
                "target": self.path,
                "path": split.path,
                "rawQuery": split.query,
                "headers": self._headers_multimap(),
                "bodyLength": len(body),
                "bodyBase64": base64.b64encode(body).decode("ascii"),
                "operationId": None,
            }

            match = None
            for route in routes:
                if route["method"] != method:
                    continue
                m = route["regex"].match(split.path)
                if m:
                    match = (route, m)
                    break

            if match is None:
                self._error(404, "NOT_IN_CONTRACT",
                            "No contract operation serves %s %s" % (method, split.path), entry)
                return

            route, m = match
            entry["operationId"] = route["operationId"]

            auth = self.headers.get("Authorization")
            if auth != expected_auth:
                self._error(401, "UNAUTHORIZED", "Missing or invalid bearer token", entry)
                return

            # Raw query parsing: keep repeats and empty values exactly as sent.
            pairs = []
            if split.query:
                for chunk in split.query.split("&"):
                    if not chunk:
                        continue
                    if "=" in chunk:
                        k, v = chunk.split("=", 1)
                    else:
                        k, v = chunk, ""
                    pairs.append((_unquote(k), _unquote(v)))

            unknown = [k for k, _ in pairs if k not in route["queryParams"]]
            if unknown:
                self._error(400, "UNKNOWN_QUERY_PARAMETER",
                            "Parameters not declared by %s: %s"
                            % (route["operationId"], ", ".join(sorted(set(unknown)))), entry)
                return

            if route["operationId"] == "getTasks":
                self._get_tasks(pairs, entry)
            elif route["operationId"] == "getTask":
                self._get_task(_unquote(m.group("taskId")), entry)
            else:  # pragma: no cover - build_routes only yields contract operations
                self._error(404, "NOT_IN_CONTRACT", "Unhandled operation", entry)

        # ---------------------------------------------------------- operations

        def _get_tasks(self, pairs, entry):
            def last(name, default):
                found = [v for k, v in pairs if k == name]
                return found[-1] if found else default

            raw_number = last("pageNumber", "0")
            raw_size = last("pageSize", str(MAX_PAGE_SIZE))
            try:
                page_number = int(raw_number) if raw_number != "" else 0
                page_size = int(raw_size) if raw_size != "" else MAX_PAGE_SIZE
            except ValueError:
                self._error(400, "BAD_REQUEST", "pageNumber and pageSize must be integers", entry)
                return
            if page_size < 1 or page_size > MAX_PAGE_SIZE:
                self._error(400, "BAD_REQUEST",
                            "pageSize must be between 1 and %d" % MAX_PAGE_SIZE, entry)
                return
            if page_number < 0:
                self._error(400, "BAD_REQUEST", "pageNumber must not be negative", entry)
                return

            total = len(tasks)
            total_pages = (total + page_size - 1) // page_size if total else 0
            reported_total = fixture.get("reportedTotalElements", total)
            start = page_number * page_size
            elements = tasks[start:start + page_size]
            self._respond(
                200,
                {
                    "elements": elements,
                    "pageMetadata": {
                        "pageNumber": page_number,
                        "pageSize": page_size,
                        "totalElements": reported_total,
                        "totalPages": total_pages,
                    },
                },
                entry,
            )

        def _get_task(self, task_id, entry):
            detail = details.get(task_id)
            if detail is None:
                self._error(404, "TASK_NOT_FOUND", "No task with id %s" % task_id, entry)
                return
            self._respond(200, detail, entry)

    return Handler


def _unquote(text):
    out = bytearray()
    i = 0
    raw = text.encode("utf-8")
    while i < len(raw):
        c = raw[i]
        if c == 0x25 and i + 2 < len(raw):  # '%'
            try:
                out.append(int(raw[i + 1:i + 3], 16))
                i += 3
                continue
            except ValueError:
                pass
        out.append(c)
        i += 1
    return out.decode("utf-8", "replace")


def main():
    if len(sys.argv) != 5:
        print(__doc__, file=sys.stderr)
        return 2
    contract = json.load(open(sys.argv[1], encoding="utf-8"))
    fixture = json.load(open(sys.argv[2], encoding="utf-8"))
    recorder = Recorder(sys.argv[3])
    port_file = sys.argv[4]

    handler = make_handler(contract, fixture, recorder)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    with open(port_file, "w", encoding="utf-8") as fh:
        fh.write(str(port))
        fh.flush()
        os.fsync(fh.fileno())
    try:
        httpd.serve_forever(poll_interval=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
