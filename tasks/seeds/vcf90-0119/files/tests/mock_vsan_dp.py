"""Contract-driven loopback fixture for the selected vSAN DP operations."""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit


CREATE_OPERATION = "Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task"
TASK_OPERATION = "Snapservice.Tasks_get"


def _route_pattern(api_base_path, operation):
    template = api_base_path.rstrip("/") + operation["path"]
    pattern = re.escape(template)
    for parameter in operation["path_parameters"]:
        marker = re.escape("{" + parameter["name"] + "}")
        pattern = pattern.replace(marker, "(?P<%s>[^/]+)" % parameter["name"])
    return re.compile("^" + pattern + "$"), operation.get("query", {})


class VsanDataProtectionMock:
    """A loopback server whose entire route table comes from contract.json."""

    def __init__(self, contract_path, task_states=None):
        self.contract = json.loads(Path(contract_path).read_text(encoding="utf-8"))
        self.requests = []
        self.task_responses = []
        self.task_states = list(task_states or ("PENDING", "RUNNING", "SUCCEEDED"))
        if not self.task_states:
            raise ValueError("task_states must not be empty")
        self._task_reads = 0
        self._routes = []
        for operation in self.contract["operations"]:
            pattern, query = _route_pattern(self.contract["api_base_path"], operation)
            self._routes.append((operation, pattern, query))

        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self._dispatch()

            def do_POST(self):
                self._dispatch()

            def do_DELETE(self):
                self._dispatch()

            def do_PUT(self):
                self._dispatch()

            def log_message(self, _format, *_args):
                return

            def _dispatch(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length) if length else b""
                split = urlsplit(self.path)
                operation, path_values = fixture._match(self.command, split)
                entry = {
                    "method": self.command,
                    "target": self.path,
                    "path": split.path,
                    "query": split.query,
                    "headers": {key.lower(): value for key, value in self.headers.items()},
                    "body": body.decode("utf-8"),
                    "operation_id": operation["operation_id"] if operation else None,
                    "path_parameters": path_values,
                }
                fixture.requests.append(entry)
                if operation is None:
                    self._json_response(404, {"error": "operation not in contract"})
                    return
                if operation["operation_id"] == CREATE_OPERATION:
                    if not self._valid_create_body(operation, body):
                        self._json_response(400, {"error": "body violates contract"})
                        return
                    self._json_response(202, "task-42")
                    return
                if operation["operation_id"] == TASK_OPERATION:
                    if path_values.get("task") != "task-42":
                        self._json_response(404, {"error": "task not found"})
                        return
                    self._json_response(200, fixture._next_task())
                    return
                self._json_response(404, {"error": "operation not implemented"})

            def _valid_create_body(self, operation, body):
                try:
                    value = json.loads(body)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return False
                request = operation["request_body"]
                if not isinstance(value, dict):
                    return False
                required = set(request["required_fields"])
                allowed = set(request["properties"])
                return required <= set(value) <= allowed

            def _json_response(self, status, value):
                encoded = json.dumps(value, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        self._httpd = HTTPServer(("127.0.0.1", 0), Handler)
        host, port = self._httpd.server_address
        self.base_url = "http://%s:%d%s" % (
            host,
            port,
            self.contract["api_base_path"],
        )
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def _match(self, method, split):
        actual_query = sorted(parse_qsl(split.query, keep_blank_values=True))
        for operation, pattern, expected_query in self._routes:
            if operation["method"] != method:
                continue
            match = pattern.fullmatch(split.path)
            if match is None:
                continue
            if actual_query != sorted(expected_query.items()):
                continue
            return operation, match.groupdict()
        return None, {}

    def _next_task(self):
        index = min(self._task_reads, len(self.task_states) - 1)
        status = self.task_states[index]
        self._task_reads += 1
        task = {
            "cancelable": False,
            "description": {
                "id": "com.vmware.snapservice.snapshot.create",
                "default_message": "Create protection group snapshot",
            },
            "service": "com.vmware.snapservice",
            "operation": CREATE_OPERATION,
            "status": status,
        }
        if status == "SUCCEEDED":
            task["result"] = {"snapshot": "snapshot-007"}
        elif status == "FAILED":
            task["error"] = {"message": "snapshot creation failed"}
        self.task_responses.append(task)
        return task

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join()


__all__ = ["VsanDataProtectionMock"]
