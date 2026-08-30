#!/usr/bin/env python3
"""Contract-pinned loopback service for the VCF Automation update operation."""

from __future__ import annotations

import argparse
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit


def load_operation(contract_path: Path) -> dict:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    operations = contract.get("operations")
    if not isinstance(operations, list) or len(operations) != 1:
        raise ValueError("the loopback fixture requires exactly one named operation")
    operation = operations[0]
    if operation.get("operationId") != "updateProject":
        raise ValueError("unexpected operationId")
    return operation


def route_pattern(path_template: str) -> re.Pattern[str]:
    marker = "{id}"
    if path_template.count(marker) != 1:
        raise ValueError("pathTemplate must contain one {id} segment")
    before, after = path_template.split(marker)
    return re.compile(f"^{re.escape(before)}(?P<id>[^/]+){re.escape(after)}$")


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], operation: dict, request_log: Path):
        super().__init__(address, ContractHandler)
        self.operation = operation
        self.route = route_pattern(operation["pathTemplate"])
        self.request_log = request_log
        self.log_lock = threading.Lock()
        self.project_states: dict[str, str] = {}
        self.effect_count = 0


class ContractHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: ContractServer

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send_json(self, status: int, payload: dict) -> None:
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded)

    def _dispatch(self) -> None:
        operation = self.server.operation
        split = urlsplit(self.path)
        route_match = self.server.route.fullmatch(split.path)
        if route_match is None:
            self._send_json(404, {"message": "operation not served"})
            return
        if self.command != operation["method"]:
            self._send_json(405, {"message": "method not served"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"message": "invalid content length"})
            return
        raw_body = self.rfile.read(content_length)
        try:
            body = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"message": "invalid JSON"})
            return

        media_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if media_type != operation["request"]["mediaType"]:
            self._send_json(400, {"message": "wrong media type"})
            return
        if not self.headers.get("Authorization", "").startswith("Bearer "):
            self._send_json(403, {"message": "missing bearer authorization"})
            return
        allowed_query = set(operation["queryParameters"])
        query = parse_qs(split.query, keep_blank_values=True)
        if not set(query).issubset(allowed_query):
            self._send_json(400, {"message": "unknown query parameter"})
            return
        schema = operation["request"]
        allowed_properties = set(schema["properties"])
        if not isinstance(body, dict) or not set(schema["requiredProperties"]).issubset(body):
            self._send_json(400, {"message": "missing required request property"})
            return
        if not set(body).issubset(allowed_properties):
            self._send_json(400, {"message": "unknown request property"})
            return

        project_id = unquote(route_match.group("id"))
        canonical_state = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        with self.server.log_lock:
            effect_applied = self.server.project_states.get(project_id) != canonical_state
            if effect_applied:
                self.server.project_states[project_id] = canonical_state
                self.server.effect_count += 1
            record = {
                "method": self.command,
                "rawPath": self.path,
                "path": split.path,
                "query": query,
                "headers": {key.lower(): value for key, value in self.headers.items()},
                "bodyText": raw_body.decode("utf-8"),
                "body": body,
                "projectId": project_id,
                "effectApplied": effect_applied,
                "effectCount": self.server.effect_count,
            }
            with self.server.request_log.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n")

        response = {"id": project_id, "_links": {"empty": True}}
        response.update(body)
        self._send_json(200, response)

    do_PATCH = _dispatch
    do_GET = _dispatch
    do_POST = _dispatch
    do_PUT = _dispatch
    do_DELETE = _dispatch


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--request-log", required=True, type=Path)
    parser.add_argument("--ready-file", required=True, type=Path)
    args = parser.parse_args()

    operation = load_operation(args.contract)
    args.request_log.write_text("", encoding="utf-8")
    server = ContractServer(("127.0.0.1", 0), operation, args.request_log)
    host, port = server.server_address
    args.ready_file.write_text(
        json.dumps({"baseUri": f"http://{host}:{port}"}, separators=(",", ":")),
        encoding="utf-8",
    )
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
