#!/usr/bin/env python3
"""Loopback mock of the vSAN Data Protection Snapshot Appliance API.

The callable route table is built exclusively from docs/contract.json: an
operation that the contract does not name is not served. Every received
request is appended to a JSON Lines log that the verifier reads.

Usage:
    contract_mock.py --contract <path> --log <path> --port-file <path>

Binds 127.0.0.1 on an ephemeral port and writes the chosen port to
--port-file once it is accepting connections.
"""

import argparse
import base64
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

USERNAME = "administrator@vsphere.local"
PASSWORD = "VMw@re123!Snap"
SESSION_TOKEN = "c9f1a4be6d0e47b8a2f35c7d1e0b9a64"

# Fixtures. Each protection group drives a fixed task-status progression or a
# fixed HTTP error, so every expected response is fully deterministic.
SCENARIOS = {
    "pg-2001": {
        "task_id": "task-9001",
        # BLOCKED is non-terminal: a client that stops there has not finished.
        "statuses": ["PENDING", "RUNNING", "BLOCKED", "SUCCEEDED"],
        "result": "snap-4f0c1e77",
    },
    "pg-2002": {
        "task_id": "task-9002",
        "statuses": ["RUNNING", "SUCCEEDED"],
        "result": "snap-8b31d90a",
    },
    "pg-2003": {
        "task_id": "task-9003",
        "statuses": ["RUNNING", "FAILED"],
        "result": None,
    },
    # The create is accepted, but retrieving the resulting task fails. This
    # proves that the client checks the HTTP status of the polling operation.
    "pg-2004": {
        "task_id": "task-9004",
        "statuses": [],
        "result": None,
        "poll_http_error": 503,
    },
    # This task never reaches a terminal state and exercises the caller's
    # one-second timeout without relying on any external state.
    "pg-2005": {
        "task_id": "task-9005",
        "statuses": ["PENDING"],
        "result": None,
    },
}

CLUSTER = "domain-c1013"


def load_routes(contract_path):
    with open(contract_path, encoding="utf-8") as handle:
        contract = json.load(handle)
    base = contract["base_path"].rstrip("/")
    routes = []
    for op in contract["operations"]:
        template = base + op["path_template"]
        pattern = "^" + re.sub(r"\{([A-Za-z_][A-Za-z0-9_]*)\}",
                               lambda m: "(?P<%s>[^/]+)" % m.group(1),
                               template) + "$"
        routes.append({
            "operation_id": op["operation_id"],
            "method": op["method"].upper(),
            "regex": re.compile(pattern),
            "query": op.get("query") or {},
            "auth": op.get("authentication"),
        })
    return contract, routes


class Recorder:
    def __init__(self, path):
        self._path = path
        self._lock = threading.Lock()
        open(self._path, "w", encoding="utf-8").close()

    def write(self, entry):
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())


def build_handler(routes, recorder):
    poll_counts = {}
    poll_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        # -- plumbing ---------------------------------------------------
        def _body(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return b""
            return self.rfile.read(length)

        def _send(self, status, payload):
            body = b"" if payload is None else json.dumps(payload).encode("utf-8")
            self.send_response(status)
            if body:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _record(self, entry):
            recorder.write(entry)

        def _error(self, status, error_type, message):
            return status, {
                "error_type": error_type,
                "messages": [{"id": "com.vmware.snapservice." + error_type.lower(),
                              "default_message": message,
                              "args": []}],
            }

        # -- dispatch ---------------------------------------------------
        def _handle(self, method):
            parsed = urlparse(self.path)
            raw_body = self._body()
            query = {k: v[0] if len(v) == 1 else v
                     for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}

            entry = {
                "method": method,
                "path": parsed.path,
                "raw_path": self.path,
                "query": query,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": raw_body.decode("utf-8", "replace"),
            }

            matched = None
            path_params = {}
            for route in routes:
                if route["method"] != method:
                    continue
                m = route["regex"].match(parsed.path)
                if not m:
                    continue
                if any(query.get(k) != v for k, v in route["query"].items()):
                    continue
                matched = route
                path_params = m.groupdict()
                break

            if matched is None:
                entry["operation_id"] = None
                entry["unknown_route"] = True
                status, payload = self._error(404, "NOT_FOUND",
                                              "No contract operation serves this request.")
                entry["response_status"] = status
                self._record(entry)
                self._send(status, payload)
                return

            entry["operation_id"] = matched["operation_id"]
            entry["unknown_route"] = False
            entry["path_parameters"] = path_params

            status, payload = self._dispatch(matched, path_params, entry, raw_body)
            entry["response_status"] = status
            self._record(entry)
            self._send(status, payload)

        def _dispatch(self, route, path_params, entry, raw_body):
            headers = entry["headers"]
            op = route["operation_id"]

            if route["auth"] == "basic_auth":
                supplied = headers.get("authorization", "")
                expected = "Basic " + base64.b64encode(
                    ("%s:%s" % (USERNAME, PASSWORD)).encode("utf-8")).decode("ascii")
                if supplied != expected:
                    return self._error(401, "UNAUTHENTICATED",
                                       "Session creation requires HTTP Basic credentials.")
            elif route["auth"] == "api_key_auth":
                if headers.get("vmware-api-session-id") != SESSION_TOKEN:
                    return self._error(401, "UNAUTHENTICATED",
                                       "A valid vmware-api-session-id header is required.")

            if op == "Snapservice.Sessions_create":
                return 201, SESSION_TOKEN

            if op == "Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task":
                return self._create_snapshot(path_params, raw_body, headers)

            if op == "Snapservice.Tasks_get":
                return self._get_task(path_params["task"])

            return self._error(500, "ERROR", "Unhandled contract operation.")

        def _create_snapshot(self, path_params, raw_body, headers):
            media_type = (headers.get("content-type") or "").split(";")[0].strip().lower()
            if media_type != "application/json":
                return self._error(400, "INVALID_ARGUMENT",
                                   "Request body must be application/json.")
            try:
                spec = json.loads(raw_body.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return self._error(400, "INVALID_ARGUMENT", "Request body is not valid JSON.")
            if not isinstance(spec, dict):
                return self._error(400, "INVALID_ARGUMENT", "Request body must be an object.")
            if not isinstance(spec.get("name"), str) or not spec["name"]:
                return self._error(400, "INVALID_ARGUMENT",
                                   "CreateSpec.name is required and must be a non-empty string.")

            if path_params["cluster"] != CLUSTER:
                return self._error(404, "NOT_FOUND", "Unknown cluster.")
            scenario = SCENARIOS.get(path_params["pg"])
            if scenario is None:
                return self._error(404, "NOT_FOUND", "Unknown protection group.")

            with poll_lock:
                poll_counts[scenario["task_id"]] = 0
            return 202, scenario["task_id"]

        def _get_task(self, task_id):
            scenario = None
            for value in SCENARIOS.values():
                if value["task_id"] == task_id:
                    scenario = value
                    break
            if scenario is None:
                return self._error(404, "NOT_FOUND", "Unknown task.")

            if "poll_http_error" in scenario:
                return self._error(scenario["poll_http_error"], "UNAVAILABLE",
                                   "Task information is temporarily unavailable.")

            with poll_lock:
                index = poll_counts.get(task_id, 0)
                poll_counts[task_id] = index + 1

            statuses = scenario["statuses"]
            status_value = statuses[min(index, len(statuses) - 1)]

            info = {
                "cancelable": False,
                "service": "com.vmware.snapservice.clusters.protection_groups.snapshots",
                "operation": "create",
                "status": status_value,
                "description": {
                    "id": "com.vmware.snapservice.protection_group.snapshot.create",
                    "default_message": "Create protection group snapshot",
                    "args": [],
                },
            }
            if status_value in ("RUNNING", "BLOCKED"):
                info["progress"] = {"total": 100, "completed": 40,
                                    "message": {"id": "progress", "default_message": "Working", "args": []}}
            if status_value == "SUCCEEDED":
                info["progress"] = {"total": 100, "completed": 100,
                                    "message": {"id": "progress", "default_message": "Done", "args": []}}
                info["result"] = scenario["result"]
            if status_value == "FAILED":
                info["error"] = {
                    "error_type": "ERROR",
                    "messages": [{"id": "com.vmware.snapservice.snapshot.quiesce_failed",
                                  "default_message": "Snapshot quiescing failed on a member virtual machine.",
                                  "args": []}],
                }
            return 200, info

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

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--port-file", required=True)
    args = parser.parse_args()

    _contract, routes = load_routes(args.contract)
    recorder = Recorder(args.log)
    server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(routes, recorder))
    port = server.server_address[1]

    tmp = args.port_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(str(port))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, args.port_file)

    sys.stderr.write("contract mock listening on 127.0.0.1:%d\n" % port)
    sys.stderr.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
