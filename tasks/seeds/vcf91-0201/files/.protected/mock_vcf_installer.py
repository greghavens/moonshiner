#!/usr/bin/env python3
"""Contract-pinned loopback VCF Installer API used only by the protected verifier."""

from __future__ import annotations

import argparse
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


TASK_ID = "task-proxy-001"
FAILED_TASK_ID = "task-proxy-failed"
SUCCESS_TASK_ID = "task-proxy-success"
TIMEOUT_TASK_ID = "task-proxy-timeout"
ACCESS_TOKEN = "loopback-access-token"
_log_lock = threading.Lock()
_sequence = 0


def load_routes(contract_path: Path) -> list[dict]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    routes: list[dict] = []
    for operation in contract["operations"]:
        template = operation["path"]
        segments = template.strip("/").split("/") if template != "/" else []
        pattern_parts = []
        parameter_names = []
        for segment in segments:
            match = re.fullmatch(r"\{([^{}]+)\}", segment)
            if match:
                parameter_names.append(match.group(1))
                pattern_parts.append(r"([^/]+)")
            else:
                pattern_parts.append(re.escape(segment))
        pattern = r"^/" + "/".join(pattern_parts) + r"$"
        routes.append(
            {
                "operationId": operation["operationId"],
                "method": operation["method"].upper(),
                "path": template,
                "pattern": re.compile(pattern),
                "parameterNames": parameter_names,
            }
        )
    return routes


class InstallerHandler(BaseHTTPRequestHandler):
    server_version = "VcfInstallerContractLoopback/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _send_json(self, status: int, payload: object | None) -> None:
        body = b"" if payload is None else json.dumps(
            payload, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        self.send_response(status)
        if payload is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _match_operation(self, method: str, path: str) -> tuple[dict | None, dict]:
        for route in self.server.routes:
            if route["method"] != method:
                continue
            match = route["pattern"].fullmatch(path)
            if not match:
                continue
            parameters = {
                name: unquote(value)
                for name, value in zip(route["parameterNames"], match.groups())
            }
            return route, parameters
        return None, {}

    def _record(
        self,
        route: dict | None,
        method: str,
        path: str,
        query: str,
        body_text: str,
        body_json: object | None,
        path_parameters: dict,
    ) -> None:
        global _sequence
        with _log_lock:
            _sequence += 1
            entry = {
                "sequence": _sequence,
                "operationId": None if route is None else route["operationId"],
                "method": method,
                "path": path,
                "query": query,
                "pathParameters": path_parameters,
                "headers": {
                    "authorization": self.headers.get("Authorization"),
                    "content-type": self.headers.get("Content-Type"),
                },
                "bodyText": body_text,
                "json": body_json,
            }
            with self.server.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, separators=(",", ":")) + "\n")
                stream.flush()

    def _dispatch(self) -> None:
        parsed = urlsplit(self.path)
        # VMware.Sdk.Vcf.Installer 13.5 performs this internal version probe
        # after createToken while Connect-VcfInstallerServer creates the real
        # SDK connection. It is SDK plumbing, not one of the focused OpenAPI
        # operations projected for the candidate workflow.
        if self.command == "GET" and parsed.path == "/v1/sddc-manager":
            route = {
                "operationId": "sdkConnectionProbe",
                "method": "GET",
                "path": "/v1/sddc-manager",
            }
            path_parameters = {}
        else:
            route, path_parameters = self._match_operation(self.command, parsed.path)
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length) if length else b""
        body_text = raw_body.decode("utf-8") if raw_body else ""
        try:
            body_json = json.loads(body_text) if body_text else None
        except json.JSONDecodeError:
            body_json = None

        self._record(
            route,
            self.command,
            parsed.path,
            parsed.query,
            body_text,
            body_json,
            path_parameters,
        )

        if route is None:
            self._send_json(
                404,
                {"errorCode": "UNKNOWN_OPERATION", "message": "Operation is not in the pinned contract."},
            )
            return

        operation_id = route["operationId"]
        if operation_id == "createToken":
            self._send_json(
                201,
                {
                    "accessToken": ACCESS_TOKEN,
                    "refreshToken": {"id": "loopback-refresh-token"},
                },
            )
            return

        if operation_id == "sdkConnectionProbe":
            self._send_json(200, {"version": "9.1.0.0.25380678"})
            return

        if operation_id == "updateSystemConfiguration":
            if isinstance(body_json, dict) and body_json.get("maxAllowedDomainsInSubscription") == 13:
                self._send_json(
                    400,
                    {
                        "errorCode": "SYSTEM_LIMIT_REJECTED",
                        "message": "The requested domain limit is not allowed.",
                    },
                )
                return
            self._send_json(200, None)
            return

        if operation_id == "updateProxyConfiguration":
            host = body_json.get("host") if isinstance(body_json, dict) else None
            if host == "proxy-api-fail.local":
                self._send_json(
                    400,
                    {
                        "errorCode": "PROXY_API_UNAVAILABLE",
                        "message": "The proxy service is temporarily unavailable.",
                    },
                )
                return
            task_id = {
                "proxy-task-fail.local": FAILED_TASK_ID,
                "proxy-success.local": SUCCESS_TASK_ID,
                "proxy-timeout.local": TIMEOUT_TASK_ID,
            }.get(host, TASK_ID)
            self._send_json(
                202,
                {
                    "id": task_id,
                    "name": "Update proxy configuration",
                    "status": "IN_PROGRESS",
                    "creationTimestamp": "2026-01-15T12:00:00.000Z",
                },
            )
            return

        if operation_id == "getTask":
            requested_id = path_parameters.get("id", "")
            if requested_id not in {
                TASK_ID,
                FAILED_TASK_ID,
                SUCCESS_TASK_ID,
                TIMEOUT_TASK_ID,
            }:
                self._send_json(
                    404,
                    {"errorCode": "TASK_NOT_FOUND", "message": "The requested task was not found."},
                )
                return
            if requested_id == TIMEOUT_TASK_ID:
                self._send_json(
                    200,
                    {
                        "id": requested_id,
                        "name": "Update proxy configuration",
                        "status": "IN_PROGRESS",
                        "creationTimestamp": "2026-01-15T12:00:00.000Z",
                    },
                )
                return
            if requested_id == FAILED_TASK_ID:
                self._send_json(
                    200,
                    {
                        "id": requested_id,
                        "name": "Update proxy configuration",
                        "status": "FAILED",
                        "creationTimestamp": "2026-01-15T12:00:00.000Z",
                        "completionTimestamp": "2026-01-15T12:00:01.000Z",
                        "errors": [
                            {
                                "errorCode": "PROXY_TASK_FAILED",
                                "message": "The proxy endpoint could not be reached.",
                            }
                        ],
                    },
                )
                return
            self._send_json(
                200,
                {
                    "id": requested_id,
                    "name": "Update proxy configuration",
                    "status": "SUCCESSFUL",
                    "creationTimestamp": "2026-01-15T12:00:00.000Z",
                    "completionTimestamp": "2026-01-15T12:00:01.000Z",
                },
            )
            return

        if operation_id == "setCeipStatus":
            if isinstance(body_json, dict) and body_json.get("status") == "ENABLE":
                self._send_json(
                    202,
                    {
                        "id": "task-ceip-success",
                        "name": "Update CEIP status",
                        "status": "IN_PROGRESS",
                        "creationTimestamp": "2026-01-15T12:00:02.000Z",
                    },
                )
                return
            self._send_json(
                409,
                {
                    "errorCode": "CEIP_CHANGE_CONFLICT",
                    "message": "CEIP is locked by the compliance policy.",
                },
            )
            return

        self._send_json(
            500,
            {"errorCode": "NO_FIXTURE", "message": "No loopback behavior exists for this contract operation."},
        )

    do_GET = _dispatch
    do_POST = _dispatch
    do_PATCH = _dispatch
    do_PUT = _dispatch
    do_DELETE = _dispatch


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler], routes: list[dict], log_path: Path):
        super().__init__(address, handler)
        self.routes = routes
        self.log_path = log_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--ready", required=True, type=Path)
    args = parser.parse_args()

    routes = load_routes(args.contract)
    args.log.parent.mkdir(parents=True, exist_ok=True)
    args.log.write_text("", encoding="utf-8")
    server = ContractServer(("127.0.0.1", 0), InstallerHandler, routes, args.log)
    args.ready.write_text(str(server.server_port), encoding="utf-8")
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
