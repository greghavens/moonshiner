"""Contract-pinned loopback VCF Installer service used only by verification."""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


EXPECTED_OPERATION_IDS = {
    "createToken",
    "validateSddcSpec",
    "deploySddc",
    "getTask",
    "refreshAccessToken",
}

SCENARIOS = {
    "refresh-success",
    "deploy-error",
    "terminal-failure",
    "poll-exhaustion",
}


def load_contract(path: Path) -> dict:
    contract = json.loads(path.read_text(encoding="utf-8"))
    operation_ids = {item["operationId"] for item in contract["operations"]}
    if operation_ids != EXPECTED_OPERATION_IDS:
        raise ValueError(f"unexpected operation set: {sorted(operation_ids)}")
    return contract


def _compile_route(path_template: str) -> re.Pattern[str]:
    pieces: list[str] = []
    cursor = 0
    for match in re.finditer(r"\{([A-Za-z][A-Za-z0-9_]*)\}", path_template):
        pieces.append(re.escape(path_template[cursor : match.start()]))
        pieces.append(f"(?P<{match.group(1)}>[^/]+)")
        cursor = match.end()
    pieces.append(re.escape(path_template[cursor:]))
    return re.compile("^" + "".join(pieces) + "$")


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, contract: dict, log_path: Path, scenario: str):
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown mock scenario: {scenario}")
        super().__init__(("127.0.0.1", 0), ContractHandler)
        self.log_path = log_path
        self.scenario = scenario
        self.routes = [
            {
                "operationId": operation["operationId"],
                "method": operation["method"].upper(),
                "pattern": _compile_route(operation["path"]),
            }
            for operation in contract["operations"]
        ]
        self.state_lock = threading.Lock()
        self.old_token_polls = 0

    @property
    def uri(self) -> str:
        host, port = self.server_address
        return f"http://{host}:{port}"

    def append_log(self, record: dict) -> None:
        with self.state_lock:
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")))
                stream.write("\n")


class ContractHandler(BaseHTTPRequestHandler):
    server: ContractServer
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def _dispatch(self) -> None:
        split = urlsplit(self.path)
        route = None
        path_values: dict[str, str] = {}
        for candidate in self.server.routes:
            match = candidate["pattern"].fullmatch(split.path)
            if candidate["method"] == self.command and match:
                route = candidate
                path_values = match.groupdict()
                break

        if route is None:
            self._respond(404, {"message": "operation is outside the pinned contract"})
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length) if length else b""
        record = {
            "operationId": route["operationId"],
            "method": self.command,
            "target": self.path,
            "path": split.path,
            "query": split.query,
            "pathValues": path_values,
            "authorization": self.headers.get("Authorization"),
            "contentType": self.headers.get("Content-Type"),
            "bodyText": raw_body.decode("utf-8"),
        }
        self.server.append_log(record)

        operation_id = route["operationId"]
        if operation_id == "createToken":
            self._respond(
                201,
                {
                    "accessToken": "access-before-expiry",
                    "refreshToken": {"id": "refresh-for-run"},
                },
            )
            return

        if operation_id == "validateSddcSpec":
            if record["authorization"] != "Bearer access-before-expiry":
                self._respond(401, {"message": "access token required"})
                return
            self._respond(
                202,
                {
                    "id": "validation-01",
                    "description": "contract validation",
                    "executionStatus": "COMPLETED",
                    "resultStatus": "SUCCEEDED",
                },
            )
            return

        if operation_id == "deploySddc":
            if record["authorization"] != "Bearer access-before-expiry":
                self._respond(401, {"message": "access token required"})
                return
            if self.server.scenario == "deploy-error":
                self._respond(503, {"message": "planned deployment rejection"})
                return
            self._respond(
                202,
                {
                    "id": "task-42",
                    "status": "IN_PROGRESS",
                    "creationTimestamp": "2026-08-02T12:00:00Z",
                },
            )
            return

        if operation_id == "refreshAccessToken":
            try:
                refresh_id = json.loads(record["bodyText"])
            except json.JSONDecodeError:
                self._respond(400, {"message": "refresh token must be JSON"})
                return
            if refresh_id != "refresh-for-run":
                self._respond(404, {"message": "refresh token not found"})
                return
            self._respond(200, "access-after-refresh")
            return

        if operation_id == "getTask":
            authorization = record["authorization"]
            if authorization not in {
                "Bearer access-before-expiry",
                "Bearer access-after-refresh",
            }:
                self._respond(401, {"message": "access token required"})
                return
            if self.server.scenario == "terminal-failure":
                self._respond(
                    200,
                    {
                        "id": "task-42",
                        "name": "VCF deployment",
                        "status": "FAILED",
                        "creationTimestamp": "2026-08-02T12:00:00Z",
                    },
                )
                return
            if self.server.scenario == "poll-exhaustion":
                self._respond(
                    200,
                    {
                        "id": "task-42",
                        "name": "VCF deployment",
                        "status": "IN_PROGRESS",
                        "creationTimestamp": "2026-08-02T12:00:00Z",
                    },
                )
                return
            if authorization == "Bearer access-before-expiry":
                with self.server.state_lock:
                    self.server.old_token_polls += 1
                    poll = self.server.old_token_polls
                if poll == 1:
                    self._respond(
                        200,
                        {
                            "id": "task-42",
                            "name": "VCF deployment",
                            "status": "IN_PROGRESS",
                            "creationTimestamp": "2026-08-02T12:00:00Z",
                        },
                    )
                else:
                    self._respond(401, {"message": "access token expired"})
                return
            if authorization == "Bearer access-after-refresh":
                self._respond(
                    200,
                    {
                        "id": "task-42",
                        "name": "VCF deployment",
                        "status": "SUCCESSFUL",
                        "creationTimestamp": "2026-08-02T12:00:00Z",
                        "completionTimestamp": "2026-08-02T12:02:00Z",
                    },
                )
                return
            self._respond(401, {"message": "access token required"})
            return

        self._respond(500, {"message": "unhandled contract operation"})

    def _respond(self, status: int, value: object) -> None:
        payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)


def start_contract_server(
    contract_path: Path,
    log_path: Path,
    scenario: str = "refresh-success",
) -> ContractServer:
    server = ContractServer(load_contract(contract_path), log_path, scenario)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
