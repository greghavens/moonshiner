"""Contract-pinned loopback service for the VCF Automation acceptance test."""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


EXPECTED_OPERATIONS = {
    "Patch Deployment",
    "Submit Deployment Action Request",
    "Get Request",
}
REQUEST_ID = "22222222-2222-4222-8222-222222222222"
SUCCESS_REQUEST_ID = "55555555-5555-4555-8555-555555555555"


def _route_pattern(path_template: str) -> re.Pattern[str]:
    pieces = []
    for segment in path_template.strip("/").split("/"):
        if segment.startswith("{") and segment.endswith("}"):
            pieces.append(f"(?P<{segment[1:-1]}>[^/]+)")
        else:
            pieces.append(re.escape(segment))
    return re.compile(r"^/" + "/".join(pieces) + r"$")


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, contract_path: Path, log_path: Path):
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        operation_names = {operation["name"] for operation in contract["operations"]}
        if operation_names != EXPECTED_OPERATIONS:
            raise ValueError(f"mock contract operations changed: {operation_names!r}")

        self.routes = [
            {
                "name": operation["name"],
                "method": operation["method"],
                "pattern": _route_pattern(operation["path_template"]),
            }
            for operation in contract["operations"]
        ]
        self.log_path = log_path
        self.log_lock = threading.Lock()
        self.deployment_id: str | None = None
        super().__init__(("127.0.0.1", 0), ContractHandler)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}"

    def match(self, method: str, path: str):
        for route in self.routes:
            match = route["pattern"].fullmatch(path)
            if route["method"] == method and match:
                return route["name"], {key: unquote(value) for key, value in match.groupdict().items()}
        return None, {}

    def log(self, record: dict) -> None:
        line = json.dumps(record, separators=(",", ":"), sort_keys=True)
        with self.log_lock:
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")


class ContractHandler(BaseHTTPRequestHandler):
    server: ContractServer
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle("GET")

    def do_PATCH(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle("PATCH")

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle("POST")

    def log_message(self, _format, *_args):
        return

    def _handle(self, method: str) -> None:
        target = urlsplit(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8") if length else ""
        operation, parameters = self.server.match(method, target.path)
        record = {
            "method": method,
            "path": target.path,
            "query": target.query,
            "headers": {key.lower(): value for key, value in self.headers.items()},
            "body": raw_body,
            "matched_operation": operation,
        }
        self.server.log(record)

        if operation is None or target.query:
            self._json_response(404, {"error": "operation is not in the pinned contract"})
            return

        if operation == "Patch Deployment":
            self.server.deployment_id = parameters["deploymentId"]
            request = json.loads(raw_body)
            self._json_response(
                200,
                {
                    "id": self.server.deployment_id,
                    "name": request.get("name"),
                    "status": "UPDATE_SUCCESSFUL",
                },
            )
            return

        if operation == "Submit Deployment Action Request":
            if parameters["deploymentId"] != self.server.deployment_id:
                self._json_response(404, {"error": "deployment not found"})
                return
            request = json.loads(raw_body)
            request_id = (
                REQUEST_ID
                if request.get("actionId") == "Deployment.ChangeLease"
                else SUCCESS_REQUEST_ID
            )
            self._json_response(
                200,
                {
                    "id": request_id,
                    "deploymentId": self.server.deployment_id,
                    "name": request.get("actionId"),
                    "requestedBy": "fixture-user",
                    "createdAt": "2026-08-13T12:00:00Z",
                    "completedTasks": 0,
                    "totalTasks": 1,
                    "status": "CREATED",
                },
            )
            return

        if parameters["requestId"] not in {REQUEST_ID, SUCCESS_REQUEST_ID}:
            self._json_response(404, {"error": "request not found"})
            return
        request_failed = parameters["requestId"] == REQUEST_ID
        self._json_response(
            200,
            {
                "id": parameters["requestId"],
                "deploymentId": self.server.deployment_id,
                "name": "Change lease" if request_failed else "Change owner",
                "requestedBy": "fixture-user",
                "createdAt": "2026-08-13T12:00:00Z",
                "completedTasks": 1,
                "totalTasks": 1,
                "status": "FAILED" if request_failed else "SUCCESSFUL",
                "details": "Cloud provider rejected lease change" if request_failed else None,
            },
        )

    def _json_response(self, status: int, body: dict) -> None:
        payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)


def start_server(contract_path: Path, log_path: Path):
    server = ContractServer(contract_path, log_path)
    thread = threading.Thread(target=server.serve_forever, name="mock-vcfa", daemon=True)
    thread.start()
    return server, thread
