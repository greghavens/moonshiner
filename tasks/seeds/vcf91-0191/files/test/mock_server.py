#!/usr/bin/env python3
import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit


EXPECTED_OPERATION_IDS = {"getAllAgentGroupConfig"}

PAGE_CONTENT = {
    0: [
        {"id": "ag-30", "name": "Zulu", "autoUpdate": False},
        {"id": "ag-10", "name": "Alpha", "autoUpdate": True},
    ],
    1: [
        {"id": "ag-40", "name": "Éclair", "autoUpdate": True},
        {"id": "ag-20", "name": "Alpha", "autoUpdate": False},
    ],
    2: [
        {"id": "ag-25", "name": "Kappa", "autoUpdate": False},
    ],
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
                "headers": {key.lower(): value for key, value in self.headers.items()},
                "body": raw_body.decode("utf-8"),
            }
        )

        if operation_id is None:
            self._json_response(
                404, {"errorCode": "API_ERROR", "errorMessage": "route not in contract"})
            return
        if raw_body:
            self._json_response(
                400, {"errorCode": "API_ERROR", "errorMessage": "GET body is forbidden"})
            return

        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        if len(pairs) != 2 or pairs[0][0] != "page" or pairs[1] != ("size", "2"):
            self._json_response(
                400, {"errorCode": "API_ERROR", "errorMessage": "wrong pageable wire shape"})
            return
        try:
            page = int(pairs[0][1])
        except ValueError:
            self._json_response(
                400, {"errorCode": "API_ERROR", "errorMessage": "page is not an integer"})
            return
        if page not in PAGE_CONTENT:
            self._json_response(
                400, {"errorCode": "API_ERROR", "errorMessage": "unexpected page"})
            return

        content = PAGE_CONTENT[page]
        last = page == max(PAGE_CONTENT)
        envelope = {
            "content": content,
            "empty": not content,
            "first": page == 0,
            "last": last,
            "number": page,
            "numberOfElements": len(content),
            "pageable": {
                "offset": page * 2,
                "pageNumber": page,
                "pageSize": 2,
                "paged": True,
                "sort": {"empty": True, "sorted": False, "unsorted": True},
                "unpaged": False,
            },
            "size": 2,
            "sort": {"empty": True, "sorted": False, "unsorted": True},
            "totalElements": sum(len(items) for items in PAGE_CONTENT.values()),
            "totalPages": len(PAGE_CONTENT),
        }
        self._json_response(200, [envelope])

    def _json_response(self, status, body):
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
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
