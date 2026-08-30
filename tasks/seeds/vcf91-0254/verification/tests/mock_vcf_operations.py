#!/usr/bin/env python3
"""Loopback VCF Operations mock, pinned to docs/contract.json.

The mock binds 127.0.0.1 only and serves exactly the operations named by the
contract. Anything else answers 404 and is logged with ``"contract": false`` so
the verifier can prove no off-contract endpoint was reached.

Every request is appended to a JSONL request log as a single object:

    {"seq", "operationId", "method", "path", "query", "headers",
     "body", "status", "contract"}

State is held in memory and starts empty; it only ever changes because of the
requests the module under test makes.

Usage: mock_vcf_operations.py --port N --contract PATH --log PATH [--ready PATH]
"""

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

LOGGED_HEADERS = ("authorization", "content-type", "accept", "user-agent")

# Deterministic identifiers handed out by createMaintenanceSchedules, in order.
ID_POOL = [
    "3f2a1c40-0001-4a10-9c31-9a0f5b7d1001",
    "3f2a1c40-0002-4a10-9c31-9a0f5b7d1002",
    "3f2a1c40-0003-4a10-9c31-9a0f5b7d1003",
    "3f2a1c40-0004-4a10-9c31-9a0f5b7d1004",
    "3f2a1c40-0005-4a10-9c31-9a0f5b7d1005",
    "3f2a1c40-0006-4a10-9c31-9a0f5b7d1006",
    "3f2a1c40-0007-4a10-9c31-9a0f5b7d1007",
    "3f2a1c40-0008-4a10-9c31-9a0f5b7d1008",
]

TOKEN = "ops-mock-token-0d4f19c7"


class State:
    def __init__(self, contract, log_path):
        self.lock = threading.Lock()
        self.seq = 0
        self.schedules = []  # list of maintenance-schedule dicts
        self.issued = 0
        self.log_path = log_path
        self.routes = {}
        for op_id, op in contract["operations"].items():
            self.routes[(op["method"], op["url"])] = op_id
        sched = contract["schemas"]["schedule"]
        self.schedule_required = set(sched["required"])
        self.schedule_properties = set(sched["properties"])
        ms = contract["schemas"]["maintenance-schedule"]
        self.ms_required = set(ms["required"])
        self.ms_properties = set(ms["properties"])

    def record(self, rec):
        with self.lock:
            self.seq += 1
            rec["seq"] = self.seq
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, sort_keys=True) + "\n")

    def next_id(self):
        if self.issued >= len(ID_POOL):
            raise RuntimeError("mock identifier pool exhausted")
        value = ID_POOL[self.issued]
        self.issued += 1
        return value


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "VcfOpsMock/1.0"

        def log_message(self, *args):  # silence stderr access logging
            pass

        def _read_body(self):
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return ""
            return self.rfile.read(length).decode("utf-8")

        def _send(self, status, payload):
            body = b"" if payload is None else json.dumps(payload).encode("utf-8")
            self.send_response(status)
            if body:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def handle_one_request(self):
            try:
                BaseHTTPRequestHandler.handle_one_request(self)
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True

        def _dispatch(self):
            parts = urlsplit(self.path)
            body = self._read_body()
            op_id = state.routes.get((self.command, parts.path))
            rec = {
                "operationId": op_id,
                "method": self.command,
                "path": parts.path,
                "query": parse_qs(parts.query, keep_blank_values=True),
                "headers": {
                    k.lower(): v
                    for k, v in self.headers.items()
                    if k.lower() in LOGGED_HEADERS
                },
                "body": body,
                "contract": op_id is not None,
            }
            try:
                if op_id is None:
                    status, payload = 404, {
                        "message": "operation not named by docs/contract.json",
                        "method": self.command,
                        "path": parts.path,
                    }
                else:
                    status, payload = getattr(self, "op_" + op_id)(rec, body)
            except Exception as exc:  # pragma: no cover - defensive
                status, payload = 500, {"message": "mock failure: %s" % exc}
            rec["status"] = status
            state.record(rec)
            self._send(status, payload)

        do_GET = do_POST = do_PUT = do_DELETE = _dispatch

        # -- operations named by the contract ---------------------------------

        def op_acquireToken(self, rec, body):
            try:
                payload = json.loads(body) if body else {}
            except ValueError:
                return 401, {"message": "malformed credential document"}
            if not payload.get("username") or not payload.get("password"):
                return 401, {"message": "username and password are required"}
            return 200, {
                "token": TOKEN,
                "validity": 4102444800000,
                "expiresAt": "Wednesday, January 1, 2100",
                "roles": ["Administrator"],
            }

        def op_getCurrentVersionOfServer(self, rec, body):
            if not self._authorized():
                return 401, {"message": "missing or invalid token"}
            return 200, {
                "major": 9,
                "minor": 1,
                "patch": 0,
                "minorMinor": 0,
                "buildNumber": 24000000,
                "description": "VMware Cloud Foundation Operations",
                "humanlyReadableReleaseDate": "May 13, 2026",
                "releasedDate": 1778630400000,
                "releaseName": "9.1.0.0",
            }

        def op_getMaintenanceSchedules(self, rec, body):
            if not self._authorized():
                return 401, {"message": "missing or invalid token"}
            query = rec["query"]
            names = query.get("name")
            ids = query.get("id")
            with state.lock:
                found = list(state.schedules)
            if names is not None:
                found = [s for s in found if s["key"] in names]
            if ids is not None:
                found = [s for s in found if s["id"] in ids]
            return 200, {
                "schedules": [json.loads(json.dumps(s)) for s in found],
                "pageInfo": {
                    "totalCount": len(found),
                    "page": 0,
                    "pageSize": len(found),
                },
            }

        def op_createMaintenanceSchedules(self, rec, body):
            if not self._authorized():
                return 401, {"message": "missing or invalid token"}
            parsed, error = self._parse_schedule_document(body)
            if error:
                return error
            key = parsed["key"]
            with state.lock:
                if any(s["key"] == key for s in state.schedules):
                    return 422, {
                        "message": "A maintenance schedule with key '%s' already exists"
                        % key
                    }
                stored = {
                    "id": state.next_id(),
                    "key": key,
                    "schedule": parsed["schedule"],
                }
                state.schedules.append(stored)
            return 201, json.loads(json.dumps(stored))

        def op_updateMaintenanceSchedules(self, rec, body):
            if not self._authorized():
                return 401, {"message": "missing or invalid token"}
            parsed, error = self._parse_schedule_document(body)
            if error:
                return error
            identifier = parsed.get("id")
            if not identifier:
                return 400, {"message": "id is required when updating a schedule"}
            with state.lock:
                for stored in state.schedules:
                    if stored["id"] == identifier:
                        if any(
                            s["key"] == parsed["key"] and s["id"] != identifier
                            for s in state.schedules
                        ):
                            return 422, {
                                "message": "A maintenance schedule with key '%s' already exists"
                                % parsed["key"]
                            }
                        stored["key"] = parsed["key"]
                        stored["schedule"] = parsed["schedule"]
                        # docs/contract.json records no response schema for a
                        # successful update. The function must retain the id it
                        # read before the write instead of relying on a response
                        # document the real operation does not promise.
                        return 200, None
            return 404, {"message": "The maintenance schedule does not exist"}

        # -- helpers -----------------------------------------------------------

        def _authorized(self):
            return self.headers.get("Authorization") == "OpsToken " + TOKEN

        def _parse_schedule_document(self, body):
            try:
                payload = json.loads(body) if body else None
            except ValueError:
                return None, (400, {"message": "request body is not valid JSON"})
            if not isinstance(payload, dict):
                return None, (400, {"message": "request body must be an object"})
            unknown = set(payload) - state.ms_properties
            if unknown:
                return None, (
                    400,
                    {"message": "unknown maintenance-schedule fields: %s"
                     % ", ".join(sorted(unknown))},
                )
            for field in sorted(state.ms_required):
                if payload.get(field) is None:
                    return None, (
                        400,
                        {"message": "maintenance-schedule field '%s' is required" % field},
                    )
            schedule = payload["schedule"]
            if not isinstance(schedule, dict):
                return None, (400, {"message": "schedule must be an object"})
            unknown = set(schedule) - state.schedule_properties
            if unknown:
                return None, (
                    400,
                    {"message": "unknown schedule fields: %s" % ", ".join(sorted(unknown))},
                )
            for field in sorted(state.schedule_required):
                if schedule.get(field) is None:
                    return None, (
                        400,
                        {"message": "schedule field '%s' is required" % field},
                    )
            if not isinstance(payload["key"], str) or not payload["key"]:
                return None, (400, {"message": "key must be a non-empty string"})
            return payload, None

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--ready")
    args = parser.parse_args()

    with open(args.contract, encoding="utf-8") as fh:
        contract = json.load(fh)

    open(args.log, "w", encoding="utf-8").close()
    state = State(contract, args.log)
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(state))
    httpd.daemon_threads = True
    if args.ready:
        with open(args.ready, "w", encoding="utf-8") as fh:
            fh.write(str(httpd.server_address[1]))
    sys.stderr.write("mock listening on 127.0.0.1:%d\n" % httpd.server_address[1])
    sys.stderr.flush()
    try:
        httpd.serve_forever(poll_interval=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    sys.exit(main() or 0)
