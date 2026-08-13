#!/usr/bin/env python3
"""Loopback mock of the four VCF Operations for Networks operations named by
docs/contract.json.

The route table is built from docs/contract.json at startup, so the mock serves
exactly the operations that contract names and nothing else. Every request is
appended to a JSON Lines log that the verifier reads.

Usage:
    python3 mock_vcf_ops_networks.py --scenario success --log requests.jsonl

The chosen port is written to stdout as "PORT <n>" followed by a newline, then
the server runs until it is terminated.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, unquote, urlparse

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT_PATH = os.path.join(REPO_ROOT, "docs", "contract.json")

# Fixed fixture data. Nothing here is random, so every run produces the same
# sequence of responses for a given scenario.
TOKEN = "Mgs2YX0ZSY+gHW6RYypeeA=="
TOKEN_EXPIRY = 1605201960327
AUTH_HEADER_VALUE = "NetworkInsight " + TOKEN

REQUEST_ID = "TASK_PROGRESS_application.APP_BULK_SAVE.1641371956491.0.007518507960020182"
CALLBACK_API = "api/ni/groups/task/progress/" + REQUEST_ID + "/"
TASK_NAME = "APP_BULK_SAVE"
TASK_START_TIME = 1641371956491

PAGE_ONE_CURSOR = "MTA="
PAGE_ONE = [
    {
        "entity_id": "18203:565:2854896465419091802",
        "entity_type": "Application",
        "entity_name": "support-app-web",
    },
    {
        "entity_id": "18203:565:3896568950496372144",
        "entity_type": "Application",
        "entity_name": "support-app-db",
    },
]
PAGE_TWO = [
    {
        "entity_id": "18203:565:7712445190380122731",
        "entity_type": "Application",
        "entity_name": "billing-app",
    }
]
TOTAL_COUNT = len(PAGE_ONE) + len(PAGE_TWO)

# Poll timelines per scenario. The submit response is only an acknowledgement;
# terminal state is always taken from a progress request, including scenarios
# where the first progress request is already terminal.
POLL_TIMELINES = {
    "success": [
        {"status": "RUNNING", "progress": 0.0},
        {"status": "RUNNING", "progress": 45.0},
        {
            "status": "FINISHED",
            "progress": 100.0,
            "app_save_response": [
                {
                    "entity_id": "18203:565:2854896465419091802",
                    "name": "support-app-web",
                    "response_code": "SUCCESS",
                },
                {
                    "entity_id": "18203:565:3896568950496372144",
                    "name": "support-app-db",
                    "response_code": "SUCCESS",
                },
                {
                    "entity_id": "18203:565:7712445190380122731",
                    "name": "billing-app",
                    "response_code": "ALREADY_SAVED_APPLICATION",
                    "error_message": "Application billing-app is already saved.",
                },
            ],
        },
    ],
    # FAILED is terminal even though progress never reaches 100.
    "failure": [
        {"status": "RUNNING", "progress": 0.0},
        {
            "status": "FAILED",
            "progress": 60.0,
            "app_save_response": [
                {
                    "entity_id": "18203:565:2854896465419091802",
                    "name": "support-app-web",
                    "response_code": "INTERNAL_ERROR",
                    "error_message": "Application save aborted by the platform.",
                }
            ],
        },
    ],
    # The task never reaches a terminal state, so the caller must give up on its
    # own timeout instead of polling forever.
    "stalled": [{"status": "RUNNING", "progress": 10.0}],
    # These isolate the two independent terminal conditions.
    "finished_below_100": [
        {
            "status": "FINISHED",
            "progress": 37.0,
            "app_save_response": [],
        }
    ],
    "progress_100_running": [{"status": "RUNNING", "progress": 100.0}],
    # A second poll would finish, but it cannot be issued after TimeoutSeconds.
    "late_finish": [
        {"status": "RUNNING", "progress": 20.0},
        {"status": "FINISHED", "progress": 100.0, "app_save_response": []},
    ],
    "empty": [
        {"status": "FINISHED", "progress": 100.0, "app_save_response": []}
    ],
    "authfail": [],
    "listfail": [],
    "savefail": [],
    "pollfail": [],
}
# The final entry of a timeline is repeated for any further poll, so a client
# that keeps polling past a terminal state still gets a well defined answer and
# the extra requests show up in the log.


def load_routes():
    with open(CONTRACT_PATH, "r", encoding="utf-8") as handle:
        contract = json.load(handle)

    base = contract["server"]["base_path"]
    routes = {}
    for operation_id, operation in contract["operations"].items():
        routes[(operation["method"], base + operation["path"])] = operation_id

    expected = {
        "create",
        "getDiscoveredApplications",
        "saveDiscoveredApplications",
        "getBulkApplicationTaskProgress",
    }
    if set(routes.values()) != expected:
        raise SystemExit(
            "contract.json does not name exactly the expected operations: "
            + repr(sorted(routes.values()))
        )
    return contract, routes


CONTRACT, ROUTES = load_routes()
PROGRESS_TEMPLATE = CONTRACT["server"]["base_path"] + CONTRACT["operations"][
    "getBulkApplicationTaskProgress"
]["path"]
PROGRESS_PREFIX = PROGRESS_TEMPLATE[: PROGRESS_TEMPLATE.index("{")]


class MockState:
    def __init__(self, scenario, log_path):
        self.scenario = scenario
        self.log_path = log_path
        self.lock = threading.Lock()
        self.sequence = 0
        self.poll_counts = {}

    def next_sequence(self):
        with self.lock:
            self.sequence += 1
            return self.sequence

    def next_poll(self, request_id):
        timeline = POLL_TIMELINES[self.scenario]
        with self.lock:
            index = self.poll_counts.get(request_id, 0)
            self.poll_counts[request_id] = index + 1
        if not timeline:
            return None
        return timeline[min(index, len(timeline) - 1)]

    def append_log(self, record):
        with self.lock:
            with open(self.log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
                handle.flush()


STATE = None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "vcf-ops-networks-mock/1.0"
    sys_version = ""

    def log_message(self, *args):  # keep stderr clean
        pass

    # -- plumbing ----------------------------------------------------------
    def _read_body(self):
        length = self.headers.get("Content-Length")
        if not length:
            return None
        try:
            count = int(length)
        except ValueError:
            return None
        if count <= 0:
            return ""
        return self.rfile.read(count).decode("utf-8", errors="replace")

    def _respond(self, status, payload):
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        if payload is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _error(self, status, message):
        self._respond(status, {"code": status, "message": message, "details": []})

    def _record(self, operation_id, raw_body, status):
        parsed = urlparse(self.path)
        STATE.append_log(
            {
                "seq": STATE.next_sequence(),
                "operation_id": operation_id,
                "method": self.command,
                "raw_path": self.path,
                "path": unquote(parsed.path),
                "encoded_path": parsed.path,
                "query_pairs": [
                    list(pair)
                    for pair in parse_qsl(parsed.query, keep_blank_values=True)
                ],
                "raw_query": parsed.query,
                "headers": {key: value for key, value in self.headers.items()},
                "body": raw_body,
                "response_status": status,
                "received_monotonic": time.monotonic(),
            }
        )

    def _authorized(self):
        return self.headers.get("Authorization") == AUTH_HEADER_VALUE

    # -- routing -----------------------------------------------------------
    def _resolve(self, path):
        """Return (operation_id, path_params) or (None, None)."""
        exact = ROUTES.get((self.command, path))
        if exact:
            return exact, {}
        if self.command == "GET" and path.startswith(PROGRESS_PREFIX):
            remainder = path[len(PROGRESS_PREFIX) :]
            if remainder and "/" not in remainder:
                return "getBulkApplicationTaskProgress", {"requestId": remainder}
        return None, None

    def _dispatch(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qsl(parsed.query, keep_blank_values=True)
        body = self._read_body()

        operation_id, path_params = self._resolve(path)
        if operation_id is None:
            self._record(None, body, 404)
            self._error(404, "No operation is served at %s %s" % (self.command, path))
            return

        handler = {
            "create": self._op_create,
            "getDiscoveredApplications": self._op_list,
            "saveDiscoveredApplications": self._op_save,
            "getBulkApplicationTaskProgress": self._op_progress,
        }[operation_id]

        status, payload = handler(query, body, path_params)
        self._record(operation_id, body, status)
        self._respond(status, payload)

    do_GET = _dispatch
    do_POST = _dispatch
    do_PUT = _dispatch
    do_DELETE = _dispatch
    do_PATCH = _dispatch

    # -- operations --------------------------------------------------------
    def _op_create(self, query, body, path_params):
        if STATE.scenario == "authfail":
            return 401, {
                "code": 401,
                "message": "The supplied credentials were rejected.",
                "details": [],
            }
        try:
            parsed = json.loads(body) if body else None
        except ValueError:
            return 400, {"code": 400, "message": "Body is not valid JSON", "details": []}
        if not isinstance(parsed, dict):
            return 400, {"code": 400, "message": "Body must be a JSON object", "details": []}
        if not parsed.get("username") or not parsed.get("password"):
            return 400, {
                "code": 400,
                "message": "username and password are required",
                "details": [],
            }
        return 200, {"token": TOKEN, "expiry": TOKEN_EXPIRY}

    def _op_list(self, query, body, path_params):
        if not self._authorized():
            return 401, {"code": 401, "message": "Invalid or missing token", "details": []}
        if STATE.scenario == "listfail":
            return 500, {"code": 500, "message": "List operation failed", "details": []}
        values = dict(query)
        if not values.get("discovery_type"):
            return 400, {
                "code": 400,
                "message": "discovery_type is a required query parameter",
                "details": [],
            }
        cursor = values.get("cursor")
        if STATE.scenario == "empty":
            return 200, {"results": [], "total_count": 0}
        if cursor == PAGE_ONE_CURSOR:
            return 200, {"results": PAGE_TWO, "total_count": TOTAL_COUNT}
        if cursor:
            return 400, {"code": 400, "message": "Unknown cursor", "details": []}
        return 200, {
            "results": PAGE_ONE,
            "cursor": PAGE_ONE_CURSOR,
            "total_count": TOTAL_COUNT,
        }

    def _op_save(self, query, body, path_params):
        if not self._authorized():
            return 401, {"code": 401, "message": "Invalid or missing token", "details": []}
        if STATE.scenario == "savefail":
            return 500, {"code": 500, "message": "Save operation failed", "details": []}
        try:
            parsed = json.loads(body) if body else None
        except ValueError:
            return 400, {"code": 400, "message": "Body is not valid JSON", "details": []}
        if not isinstance(parsed, dict):
            return 400, {"code": 400, "message": "Body must be a JSON object", "details": []}
        apps = parsed.get("discovered_apps")
        if not isinstance(apps, list):
            return 400, {
                "code": 400,
                "message": "discovered_apps must be an array",
                "details": [],
            }
        if not parsed.get("discovery_type"):
            return 400, {"code": 400, "message": "discovery_type is required", "details": []}
        return 200, {"request_id": REQUEST_ID, "callback_API": CALLBACK_API}

    def _op_progress(self, query, body, path_params):
        if not self._authorized():
            return 401, {"code": 401, "message": "Invalid or missing token", "details": []}
        if STATE.scenario == "pollfail":
            return 500, {"code": 500, "message": "Progress operation failed", "details": []}
        request_id = path_params.get("requestId")
        if request_id != REQUEST_ID:
            return 404, {
                "code": 404,
                "message": "No task with request id %s" % request_id,
                "details": [],
            }
        step = STATE.next_poll(request_id)
        if step is None:
            return 404, {"code": 404, "message": "No task timeline", "details": []}
        payload = {
            "request_id": REQUEST_ID,
            "task_name": TASK_NAME,
            "status": step["status"],
            "progress": step["progress"],
            "start_time": TASK_START_TIME,
        }
        if "app_save_response" in step:
            payload["app_save_response"] = step["app_save_response"]
        return 200, payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario",
        required=True,
        choices=sorted(POLL_TIMELINES.keys()),
    )
    parser.add_argument("--log", required=True)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()

    global STATE
    STATE = MockState(args.scenario, args.log)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    sys.stdout.write("PORT %d\n" % server.server_address[1])
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
