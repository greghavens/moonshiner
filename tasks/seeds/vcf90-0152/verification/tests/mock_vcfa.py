"""Loopback-only HTTP fixture derived from docs/contract.json."""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"


class ContractMock:
    """Serve only the two operations named by the protected contract."""

    def __init__(self, statuses, request_id="req-314"):
        self.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        self.statuses = list(statuses)
        if not self.statuses:
            raise ValueError("at least one request status is required")
        self.request_id = request_id
        self.requests = []
        self.responses = []
        self._status_index = 0

        operations = self.contract["operations"]
        if {(item["method"], item["operation"]) for item in operations} != {
            ("POST", "Create Resource"),
            ("GET", "Get Request"),
        }:
            raise ValueError("mock contract must name exactly Create Resource and Get Request")
        self._create = next(item for item in operations if item["operation"] == "Create Resource")
        self._get = next(item for item in operations if item["operation"] == "Get Request")
        escaped = re.escape(self._get["path"])
        escaped = escaped.replace(re.escape("{requestId}"), r"(?P<request_id>[^/]+)")
        self._get_path = re.compile("^" + escaped + "$")

        owner = self

        class Handler(BaseHTTPRequestHandler):
            def _record(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length) if length else b""
                split = urlsplit(self.path)
                record = {
                    "method": self.command,
                    "target": self.path,
                    "path": split.path,
                    "query": split.query,
                    "headers": {key.lower(): value for key, value in self.headers.items()},
                    "body": body,
                }
                owner.requests.append(record)
                return record

            def _json(self, status, payload):
                owner.responses.append(payload)
                body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authorized(self, record):
                value = record["headers"].get("authorization", "")
                return value.startswith("Bearer ") and len(value) > len("Bearer ")

            def do_POST(self):
                record = self._record()
                if record["path"] != owner._create["path"] or record["query"]:
                    self._json(404, {"error": "operation not in contract"})
                    return
                if not self._authorized(record):
                    self._json(401, {"error": "unauthorized"})
                    return
                if record["headers"].get("content-type") != "application/json":
                    self._json(400, {"error": "content type"})
                    return
                try:
                    payload = json.loads(record["body"].decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._json(400, {"error": "json"})
                    return
                request_contract = owner._create["request"]
                required = set(request_contract["required_fields"])
                optional = set(request_contract["optional_fields"])
                if not isinstance(payload, dict) or not required <= set(payload) or set(payload) - required - optional:
                    self._json(400, {"error": "resource specification"})
                    return
                self._json(
                    200,
                    {
                        "deploymentId": "dep-fixture",
                        "projectId": "project-fixture",
                        "requestId": owner.request_id,
                        "resourceId": "resource-271",
                    },
                )

            def do_GET(self):
                record = self._record()
                match = owner._get_path.fullmatch(record["path"])
                if match is None or record["query"] or record["body"]:
                    self._json(404, {"error": "operation not in contract"})
                    return
                if not self._authorized(record):
                    self._json(401, {"error": "unauthorized"})
                    return
                index = min(owner._status_index, len(owner.statuses) - 1)
                status = owner.statuses[index]
                owner._status_index += 1
                terminal = status in owner.contract["asynchronous_scenario"]["terminal_statuses"]
                payload = {
                    "completedTasks": 1 if status == "SUCCESSFUL" else 0,
                    "createdAt": "2026-08-13T12:00:00.000Z",
                    "id": unquote(match.group("request_id")),
                    "name": "Create Resource",
                    "requestedBy": "fixture-user",
                    "status": status,
                    "totalTasks": 1,
                }
                if terminal and status != "SUCCESSFUL":
                    payload["details"] = "fixture terminal failure"
                self._json(200, payload)

            def log_message(self, _format, *_args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self):
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)
