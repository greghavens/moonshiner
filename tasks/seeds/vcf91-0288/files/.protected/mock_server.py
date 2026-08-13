#!/usr/bin/env python3
"""Loopback mock of the VCF Operations for Networks /api/ni surface.

The mock is pinned to docs/contract.json: it serves ONLY the (method, fullPath)
pairs that the contract names, and answers anything else with 404 while still
recording the attempt in the request log.

Every request is appended to a JSONL log that is flushed and fsynced before the
response is written, so a reader can rely on the log being complete once the
client has seen its response.

Usage:
    python3 -B .protected/mock_server.py \
        --contract docs/contract.json \
        --log /tmp/requests.jsonl \
        --ready /tmp/ready.json \
        [--scenario /tmp/scenario.json] [--port 0]

Without --scenario a self-consistent default scenario is used, which makes the
mock usable for hand testing:

    python3 -B .protected/mock_server.py --port 8901 --log /tmp/req.jsonl

No live VMware endpoint is contacted.
"""

import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOGGED_HEADERS = ("authorization", "content-type", "accept")

DEFAULT_SCENARIO = {
    "token": "default-mock-token",
    "expiry": 1774000000000,
    "nodes": [
        {
            "id": "18230:901:1585583463",
            "entity_type": "Node",
            "node_type": "PLATFORM_VM",
            "name": "Platform-1",
            "ip_address": "10.10.0.10",
            "version": "9.1.0.0",
            "health": {"health_status": "HEALTHY", "health_details": []},
        },
        {
            "id": "18230:901:1706494033",
            "entity_type": "Node",
            "node_type": "PROXY_VM",
            "name": "Collector-A",
            "ip_address": "10.10.0.11",
            "version": "9.1.0.0",
            "health": {"health_status": "HEALTHY", "health_details": []},
        },
    ],
    "validate": {"status": 200, "body": {"code": 200, "message": "Validation successful."}},
    "add": {
        "status": 201,
        "body": {
            "entity_id": "18230:902:993642895",
            "entity_type": "VCenterDataSource",
            "proxy_id": "18230:901:1706494033",
            "nickname": "vc1",
            "enabled": True,
        },
    },
}


def load_contract(path):
    with open(path, "r", encoding="utf-8") as handle:
        contract = json.load(handle)
    allowed = {}
    for op in contract["operations"]:
        allowed[(op["method"].upper(), op["fullPath"])] = op["operationId"]
    return contract, allowed


class RequestLog:
    def __init__(self, path):
        self._path = path
        self._lock = threading.Lock()
        self._index = 0
        # Truncate any previous content so each run starts from a clean log.
        with open(self._path, "w", encoding="utf-8"):
            pass

    def append(self, entry):
        with self._lock:
            entry["index"] = self._index
            self._index += 1
            with open(self._path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return entry


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "VcfOpsNetworksMock/1.0"
    sys_version = ""

    # -- plumbing ---------------------------------------------------------
    def log_message(self, fmt, *args):  # silence stderr chatter
        return

    def _read_body(self):
        raw = b""
        length = self.headers.get("Content-Length")
        if length:
            try:
                raw = self.rfile.read(int(length))
            except (ValueError, OSError):
                raw = b""
        return raw

    def _send_json(self, status, payload):
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        if payload is None:
            self.send_header("Content-Length", "0")
        else:
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _authorized(self):
        expected = "NetworkInsight " + self.server.scenario["token"]
        return self.headers.get("Authorization") == expected

    # -- dispatch ---------------------------------------------------------
    def _handle(self, method):
        raw = self._read_body()
        path, _, query = self.path.partition("?")

        parsed = None
        parse_error = None
        if raw:
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                parse_error = str(exc)

        operation_id = self.server.allowed.get((method, path))
        entry = {
            "method": method,
            "path": path,
            "query": query,
            "raw_target": self.path,
            "operation_id": operation_id,
            "on_contract": operation_id is not None,
            "headers": {
                name: self.headers.get(name)
                for name in LOGGED_HEADERS
                if self.headers.get(name) is not None
            },
            "body_raw": raw.decode("utf-8", "replace"),
            "body_json": parsed,
            "body_parse_error": parse_error,
        }
        self.server.request_log.append(entry)

        if operation_id is None:
            self._send_json(
                404,
                {
                    "code": 404,
                    "message": "off-contract request; this mock serves only the "
                    "operations named by docs/contract.json",
                    "details": [{"code": 404, "message": "%s %s" % (method, path), "target": [path]}],
                },
            )
            return

        handler = getattr(self, "_op_" + operation_id)
        handler(parsed)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_DELETE(self):
        self._handle("DELETE")

    def do_PUT(self):
        self._handle("PUT")

    def do_PATCH(self):
        self._handle("PATCH")

    # -- operations -------------------------------------------------------
    def _op_create(self, body):
        scenario = self.server.scenario
        if not isinstance(body, dict) or not body.get("username"):
            self._send_json(400, {"code": 400, "message": "username is required"})
            return
        self._send_json(200, {"token": scenario["token"], "expiry": scenario["expiry"]})

    def _op_delete(self, body):
        if not self._authorized():
            self._send_json(401, {"code": 401, "message": "Unauthorized"})
            return
        self._send_json(204, None)

    def _op_listExpandedNodes(self, body):
        if not self._authorized():
            self._send_json(401, {"code": 401, "message": "Unauthorized"})
            return
        nodes = self.server.scenario["nodes"]
        self._send_json(200, {"results": nodes, "total_count": len(nodes)})

    def _op_validateVCenter(self, body):
        if not self._authorized():
            self._send_json(401, {"code": 401, "message": "Unauthorized"})
            return
        response = self.server.scenario["validate"]
        self._send_json(response["status"], response["body"])

    def _op_addVcenterDatasource(self, body):
        if not self._authorized():
            self._send_json(401, {"code": 401, "message": "Unauthorized"})
            return
        response = self.server.scenario["add"]
        self._send_json(response["status"], response["body"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", default="docs/contract.json")
    parser.add_argument("--scenario", default=None)
    parser.add_argument("--log", required=True)
    parser.add_argument("--ready", default=None)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)

    contract, allowed = load_contract(args.contract)

    scenario = dict(DEFAULT_SCENARIO)
    if args.scenario:
        with open(args.scenario, "r", encoding="utf-8") as handle:
            scenario.update(json.load(handle))

    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    httpd.daemon_threads = True
    httpd.allowed = allowed
    httpd.contract = contract
    httpd.scenario = scenario
    httpd.request_log = RequestLog(args.log)

    port = httpd.server_address[1]
    info = {"port": port, "base_url": "http://127.0.0.1:%d" % port, "log": args.log}
    if args.ready:
        tmp = args.ready + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(info, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, args.ready)
    else:
        sys.stderr.write(json.dumps(info) + "\n")
        sys.stderr.flush()

    try:
        httpd.serve_forever(poll_interval=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
