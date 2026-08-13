#!/usr/bin/env python3
"""Loopback mock of the vSphere Automation API for vCenter (VCF 9.0).

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
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

USERNAME = "administrator@vsphere.local"
PASSWORD = "VMw@re123!Clone"
SESSION_TOKEN = "b7d41f9e2c8a4051be36d7f0a91c5e28"
WRONG_STATUS_USERNAME = "wrong-status@vsphere.local"
WRONG_STATUS_PASSWORD = "ValidButWrongStatus!"

# Fixtures keyed by the CloneSpec.source virtual machine. Each source drives a
# fixed task-status progression, so the number of polls a client must perform
# is fully deterministic.
SCENARIOS = {
    "vm-101": {
        "task_id": "task-5001",
        # BLOCKED is non-terminal: a client that stops there has not finished.
        "statuses": ["PENDING", "RUNNING", "BLOCKED", "SUCCEEDED"],
        "result": "vm-2087",
    },
    "vm-102": {
        "task_id": "task-5002",
        "statuses": ["RUNNING", "SUCCEEDED"],
        "result": "vm-2088",
    },
    "vm-103": {
        "task_id": "task-5003",
        "statuses": ["RUNNING", "FAILED"],
        "result": None,
    },
    # The identifier deliberately contains reserved characters. The client
    # must percent-escape it as one path segment and then use it unchanged as
    # the logical task identifier in its result.
    "vm-104": {
        "task_id": "task 5004/blue%canary",
        "statuses": ["SUCCEEDED"],
        "result": "vm-2089",
    },
    # Contract-negative fixtures prove that clients enforce the operation's
    # exact success status rather than treating every 2xx response as success.
    "vm-105": {
        "task_id": "task-5005",
        "statuses": ["SUCCEEDED"],
        "result": "vm-2090",
        "clone_status": 200,
    },
    "vm-106": {
        "task_id": "task-5006",
        "statuses": ["RUNNING"],
        "result": None,
        "poll_status": 201,
    },
    # A response that takes comfortably longer than the one-second deadline
    # makes timeout verification deterministic without racing a sleep boundary.
    "vm-107": {
        "task_id": "task-5007",
        "statuses": ["RUNNING", "SUCCEEDED"],
        "result": "vm-2091",
        "first_poll_delay": 2.0,
    },
}


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
                "messages": [{"id": "com.vmware.vapi.std.errors." + error_type.lower(),
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
                wrong_status_credentials = "Basic " + base64.b64encode(
                    ("%s:%s" % (WRONG_STATUS_USERNAME, WRONG_STATUS_PASSWORD))
                    .encode("utf-8")).decode("ascii")
                if supplied not in (expected, wrong_status_credentials):
                    leaked_password = "unknown"
                    if supplied.startswith("Basic "):
                        try:
                            decoded = base64.b64decode(supplied[6:]).decode("utf-8")
                            leaked_password = decoded.partition(":")[2]
                        except (ValueError, UnicodeDecodeError):
                            pass
                    return self._error(401, "UNAUTHENTICATED",
                                       "Rejected password %s." % leaked_password)
            elif route["auth"] == "api_key_auth":
                if headers.get("vmware-api-session-id") != SESSION_TOKEN:
                    return self._error(401, "UNAUTHENTICATED",
                                       "A valid vmware-api-session-id header is required.")

            if op == "Cis.Session_create":
                if headers.get("authorization") == wrong_status_credentials:
                    return 200, SESSION_TOKEN
                return 201, SESSION_TOKEN

            if op == "Vcenter.VM_clone$Task":
                return self._clone_vm(raw_body, headers)

            if op == "Cis.Tasks_get":
                return self._get_task(path_params["task"])

            return self._error(500, "ERROR", "Unhandled contract operation.")

        def _clone_vm(self, raw_body, headers):
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
            for required in ("name", "source"):
                if not isinstance(spec.get(required), str) or not spec[required]:
                    return self._error(
                        400, "INVALID_ARGUMENT",
                        "CloneSpec.%s is required and must be a non-empty string." % required)

            scenario = SCENARIOS.get(spec["source"])
            if scenario is None:
                return self._error(404, "NOT_FOUND", "Unknown source virtual machine.")

            with poll_lock:
                poll_counts[scenario["task_id"]] = 0
            return scenario.get("clone_status", 202), scenario["task_id"]

        def _get_task(self, task_id):
            task_id = unquote(task_id)
            scenario = None
            for value in SCENARIOS.values():
                if value["task_id"] == task_id:
                    scenario = value
                    break
            if scenario is None:
                return self._error(404, "NOT_FOUND", "Unknown task.")

            with poll_lock:
                index = poll_counts.get(task_id, 0)
                poll_counts[task_id] = index + 1

            if index == 0 and scenario.get("first_poll_delay"):
                time.sleep(scenario["first_poll_delay"])
            if scenario.get("poll_status"):
                return self._error(
                    scenario["poll_status"], "ERROR",
                    "Task lookup rejected session %s." % SESSION_TOKEN)

            statuses = scenario["statuses"]
            status_value = statuses[min(index, len(statuses) - 1)]

            info = {
                "cancelable": False,
                "service": "com.vmware.vcenter.vm",
                "operation": "clone",
                "status": status_value,
                "description": {
                    "id": "com.vmware.vcenter.vm.clone",
                    "default_message": "Clone virtual machine",
                    "args": [],
                },
            }
            if status_value in ("RUNNING", "BLOCKED"):
                info["progress"] = {"total": 100, "completed": 45,
                                    "message": {"id": "progress", "default_message": "Copying disks", "args": []}}
            if status_value == "SUCCEEDED":
                info["progress"] = {"total": 100, "completed": 100,
                                    "message": {"id": "progress", "default_message": "Done", "args": []}}
                info["result"] = scenario["result"]
                info["target"] = {"type": "VirtualMachine", "id": scenario["result"]}
            if status_value == "FAILED":
                info["error"] = {
                    "error_type": "UNABLE_TO_ALLOCATE_RESOURCE",
                    "messages": [{"id": "com.vmware.vcenter.vm.clone.datastore_out_of_space",
                                  "default_message": "The destination datastore has insufficient free space to clone the virtual machine; session %s was rejected." % SESSION_TOKEN,
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
