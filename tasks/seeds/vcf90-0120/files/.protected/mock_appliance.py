"""Contract-pinned loopback Snapshot Appliance mock with a JSONL request log."""

from __future__ import annotations

import json
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
OLD_ACCESS_TOKEN = "access-before-expiry"
FRESH_ACCESS_TOKEN = "access-after-refresh"
TASK_ID = "task-snapshot-72"
SNAPSHOT_ID = "snapshot-72"

EXPECTED_OPERATIONS = {
    (
        "POST",
        "/api/snapservice/clusters/{cluster}/protection-groups/{pg}/snapshots",
        "Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task",
    ),
    ("GET", "/api/snapservice/tasks/{task}", "Snapservice.Tasks_get"),
}
CONTRACT_OPERATIONS = {
    (operation["method"], operation["path"], operation["operationId"])
    for operation in CONTRACT["operations"]
}
if CONTRACT_OPERATIONS != EXPECTED_OPERATIONS:
    raise RuntimeError("mock and docs/contract.json operation sets differ")


class _State:
    def __init__(self, request_log):
        self.request_log = Path(request_log)
        self.lock = threading.Lock()
        self.task_created = False
        self.fresh_polls = 0

    def append(self, entry):
        with self.lock:
            with self.request_log.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, sort_keys=True) + "\n")


def _handler_for(state):
    class Handler(BaseHTTPRequestHandler):
        server_version = "VsanDpContractMock/1"
        sys_version = ""

        def log_message(self, _format, *_args):
            return

        def _read_body(self):
            size = int(self.headers.get("Content-Length", "0"))
            return self.rfile.read(size) if size else b""

        def _entry(self, body):
            parsed = urllib.parse.urlsplit(self.path)
            return {
                "method": self.command,
                "raw_target": self.path,
                "path": parsed.path,
                "raw_query": parsed.query,
                "query_pairs": [list(pair) for pair in urllib.parse.parse_qsl(parsed.query)],
                "headers": {name.lower(): value for name, value in self.headers.items()},
                "body_utf8": body.decode("utf-8"),
                "body_length": len(body),
            }

        def _answer(self, entry, status, payload=None):
            entry["response_status"] = status
            state.append(entry)
            body = b"" if payload is None else json.dumps(payload).encode("utf-8")
            self.send_response(status)
            if body:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _error(self, entry, status, error_type, message):
            self._answer(
                entry,
                status,
                {
                    "error_type": error_type,
                    "messages": [{"default_message": message}],
                },
            )

        def do_POST(self):
            body = self._read_body()
            entry = self._entry(body)
            parsed = urllib.parse.urlsplit(self.path)
            create_route = re.fullmatch(
                r"/api/snapservice/clusters/[^/]+/protection-groups/[^/]+/snapshots",
                parsed.path,
            )
            if not create_route or urllib.parse.parse_qsl(parsed.query) != [("vmw-task", "true")]:
                self._error(entry, 404, "Vapi.Std.Errors.NotFound", "operation not served")
                return
            if self.headers.get("vmware-api-session-id") != OLD_ACCESS_TOKEN:
                self._error(entry, 401, "Vapi.Std.Errors.Unauthenticated", "access token rejected")
                return
            if state.task_created:
                self._error(entry, 400, "Vapi.Std.Errors.InvalidArgument", "snapshot already started")
                return
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                self._error(entry, 400, "Vapi.Std.Errors.InvalidArgument", "invalid JSON")
                return
            allowed = {"name", "retention"}
            if not isinstance(payload, dict) or not isinstance(payload.get("name"), str):
                self._error(entry, 400, "Vapi.Std.Errors.InvalidArgument", "invalid CreateSpec")
                return
            if set(payload) - allowed:
                self._error(entry, 400, "Vapi.Std.Errors.InvalidArgument", "unknown CreateSpec property")
                return
            state.task_created = True
            self._answer(entry, 202, TASK_ID)

        def do_GET(self):
            body = self._read_body()
            entry = self._entry(body)
            parsed = urllib.parse.urlsplit(self.path)
            task_route = re.fullmatch(r"/api/snapservice/tasks/([^/]+)", parsed.path)
            if not task_route or parsed.query:
                self._error(entry, 404, "Vapi.Std.Errors.NotFound", "operation not served")
                return
            if self.headers.get("vmware-api-session-id") != FRESH_ACCESS_TOKEN:
                self._error(entry, 401, "Vapi.Std.Errors.Unauthenticated", "access token expired")
                return
            if not state.task_created or urllib.parse.unquote(task_route.group(1)) != TASK_ID:
                self._error(entry, 404, "Vapi.Std.Errors.NotFound", "task not found")
                return
            state.fresh_polls += 1
            status = "RUNNING" if state.fresh_polls == 1 else "SUCCEEDED"
            info = {
                "cancelable": False,
                "description": {"default_message": "Create protection group snapshot"},
                "operation": "create-snapshot",
                "service": "snapservice",
                "status": status,
            }
            if status == "SUCCEEDED":
                info["result"] = SNAPSHOT_ID
            self._answer(entry, 200, info)

    return Handler


class MockAppliance:
    """Context manager for a loopback-only HTTP server pinned to CONTRACT."""

    def __init__(self, request_log):
        self.request_log = Path(request_log)
        self._state = _State(self.request_log)
        self._server = None
        self._thread = None

    def __enter__(self):
        self.request_log.write_text("", encoding="utf-8")
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(self._state))
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        host, port = self._server.server_address
        self.base_url = "http://%s:%d/api" % (host, port)
        return self

    def __exit__(self, _type, _value, _traceback):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2.0)

    def requests(self):
        if not self.request_log.exists():
            return []
        return [
            json.loads(line)
            for line in self.request_log.read_text(encoding="utf-8").splitlines()
            if line
        ]
