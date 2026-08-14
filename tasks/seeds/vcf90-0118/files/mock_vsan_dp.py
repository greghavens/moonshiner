"""Loopback vSAN Data Protection 9.0 fixture.

The server binds only 127.0.0.1 and exposes exactly the four operations in
docs/contract.json. Every request is written to the JSON request-log path
passed as argv[2]. The first session expires immediately after the protected
protection-group read, forcing the snapshot run to refresh in place.
"""

import base64
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit


USERNAME = "svc-vsandp"
PASSWORD = "dummy-pass-0118"
CLUSTER = "domain-c8"
PROTECTION_GROUP = "pg-nightly"
SNAPSHOT_NAME = "pre-upgrade"
TASK_ID = "task-77"
SNAPSHOT_ID = "snapshot-88"

EXPECTED_BASIC = "Basic " + base64.b64encode(
    f"{USERNAME}:{PASSWORD}".encode("utf-8")
).decode("ascii")

STATE = {
    "session_number": 0,
    "valid_token": None,
    "expired_after_read": False,
    "accepted_snapshot_count": 0,
    "task_reads": 0,
}
REQUESTS = []
REQUEST_LOG = None
SCENARIO = "success"


def write_json_atomic(path, value):
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, separators=(",", ":"), ensure_ascii=True)
        handle.write("\n")
    os.replace(temporary, path)


def unauthenticated():
    return {"error_type": "UNAUTHENTICATED", "messages": []}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def _read_and_record(self):
        parsed = urlsplit(self.path)
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length) if length else b""
        entry = {
            "method": self.command,
            "path": parsed.path,
            "raw_query": parsed.query,
            "accept": self.headers.get("Accept"),
            "authorization": self.headers.get("Authorization"),
            "session_id": self.headers.get("vmware-api-session-id"),
            "content_type": self.headers.get("Content-Type"),
            "content_length": length,
            "body": raw.decode("utf-8", "strict"),
            "body_hex": raw.hex(),
        }
        REQUESTS.append(entry)
        write_json_atomic(REQUEST_LOG, REQUESTS)
        return parsed, raw

    def _send(self, status, value=None):
        if value is None:
            body = b""
        else:
            body = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        if value is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _has_current_session(self):
        return (
            STATE["valid_token"] is not None
            and self.headers.get("vmware-api-session-id") == STATE["valid_token"]
        )

    def do_POST(self):
        parsed, raw = self._read_and_record()
        if parsed.path == "/api/snapservice/sessions" and parsed.query == "":
            if (
                self.headers.get("Authorization") != EXPECTED_BASIC
                or raw != b""
                or self.headers.get("Content-Type") is not None
            ):
                self._send(401, unauthenticated())
                return
            STATE["session_number"] += 1
            token = f"session-{STATE['session_number']}"
            STATE["valid_token"] = token
            self._send(201, token)
            return

        snapshot_path = (
            f"/api/snapservice/clusters/{CLUSTER}/protection-groups/"
            f"{PROTECTION_GROUP}/snapshots"
        )
        if parsed.path == snapshot_path and parsed.query == "vmw-task=true":
            if not self._has_current_session():
                self._send(401, unauthenticated())
                return
            try:
                body = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                self._send(400, {"error_type": "INVALID_ARGUMENT", "messages": []})
                return
            if body != {"name": SNAPSHOT_NAME}:
                self._send(400, {"error_type": "INVALID_ARGUMENT", "messages": []})
                return
            if SCENARIO == "sensitive_error":
                self._send(
                    500,
                    {
                        "error_type": "ERROR",
                        "messages": [
                            {
                                "default_message": (
                                    f"{PASSWORD} session-1 session-2"
                                )
                            }
                        ],
                    },
                )
                return
            STATE["accepted_snapshot_count"] += 1
            if STATE["accepted_snapshot_count"] != 1:
                self._send(409, {"error_type": "ALREADY_EXISTS", "messages": []})
                return
            self._send(202, TASK_ID)
            return

        self._send(404, {"error_type": "NOT_FOUND", "messages": []})

    def do_GET(self):
        parsed, raw = self._read_and_record()
        if raw != b"":
            self._send(400, {"error_type": "INVALID_ARGUMENT", "messages": []})
            return

        protection_group_path = (
            f"/api/snapservice/clusters/{CLUSTER}/protection-groups/"
            f"{PROTECTION_GROUP}"
        )
        if parsed.path == protection_group_path and parsed.query == "":
            if not self._has_current_session():
                self._send(401, unauthenticated())
                return
            self._send(
                200,
                {
                    "name": "nightly-critical",
                    "status": "ACTIVE",
                    "target_entities": {},
                    "snapshot_policies": [],
                    "vms": ["vm-21"],
                    "snapshots": [],
                    "locked": False,
                },
            )
            if not STATE["expired_after_read"]:
                STATE["expired_after_read"] = True
                STATE["valid_token"] = None
            return

        task_path = f"/api/snapservice/tasks/{TASK_ID}"
        if parsed.path == task_path and parsed.query == "":
            if not self._has_current_session():
                self._send(401, unauthenticated())
                return
            STATE["task_reads"] += 1
            if SCENARIO == "failed_task":
                status = "FAILED"
            else:
                status = "RUNNING" if STATE["task_reads"] == 1 else "SUCCEEDED"
            answer = {
                "description": {
                    "id": "com.vmware.snapservice.snapshot.create",
                    "default_message": "Create protection group snapshot",
                    "args": [],
                },
                "service": "com.vmware.snapservice",
                "operation": "snapshot.create",
                "status": status,
                "cancelable": False,
            }
            if status == "SUCCEEDED":
                answer["result"] = {"snapshot": SNAPSHOT_ID}
            elif status == "FAILED":
                answer["error"] = {
                    "error_type": "ERROR",
                    "messages": [{"default_message": "snapshot task failed"}],
                }
            self._send(200, answer)
            return

        self._send(404, {"error_type": "NOT_FOUND", "messages": []})


def main():
    global REQUEST_LOG, SCENARIO
    if len(sys.argv) not in (3, 4):
        raise SystemExit(
            "usage: mock_vsan_dp.py PORT_FILE REQUEST_LOG [SCENARIO]"
        )
    port_file, REQUEST_LOG = sys.argv[1:3]
    if len(sys.argv) == 4:
        SCENARIO = sys.argv[3]
    if SCENARIO not in {"success", "failed_task", "sensitive_error"}:
        raise SystemExit(f"unknown scenario: {SCENARIO}")
    write_json_atomic(REQUEST_LOG, [])
    server = HTTPServer(("127.0.0.1", 0), Handler)
    write_json_atomic(port_file, server.server_address[1])
    server.serve_forever()


if __name__ == "__main__":
    main()
