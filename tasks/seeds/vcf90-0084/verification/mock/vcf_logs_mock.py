#!/usr/bin/env python3
"""Loopback-only mock for the operations selected in docs/contract.json."""

from __future__ import annotations

import argparse
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


EXPECTED_OPERATION_ID = "PUT_notification-webhook"


def load_operation(contract_path: Path) -> tuple[str, str, str, dict]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    operations = contract.get("operations", {})
    if set(operations) != {EXPECTED_OPERATION_ID}:
        raise ValueError("mock contract must name only PUT_notification-webhook")

    operation = operations[EXPECTED_OPERATION_ID]
    if operation.get("operationId") != EXPECTED_OPERATION_ID:
        raise ValueError("contract operationId does not match its key")
    if operation.get("method") != "PUT":
        raise ValueError("contract method must be PUT")

    security_name = next(iter(operation["security"][0]))
    authorization_scheme = contract["securitySchemes"][security_name]["scheme"]
    path = f"{contract['basePath']}{operation['path']}"
    return operation["method"], path, authorization_scheme, operation


def validate_known_properties(payload: object, operation: dict) -> str | None:
    if not isinstance(payload, dict):
        return "request body must be a JSON object"

    properties = operation["request"]["schema"]["properties"]
    for name, value in payload.items():
        schema = properties.get(name)
        # OpenAPI allows additional properties unless explicitly disabled.
        if schema is None:
            continue
        expected_type = schema.get("type")
        if expected_type == "string" and not isinstance(value, str):
            return f"{name} must be a string"
        if expected_type == "boolean" and not isinstance(value, bool):
            return f"{name} must be a boolean"
        if expected_type == "array":
            if not isinstance(value, list):
                return f"{name} must be an array"
            if schema.get("items", {}).get("type") == "string" and not all(
                isinstance(item, str) for item in value
            ):
                return f"{name} items must be strings"
    return None


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], handler, contract_path: Path, log_path: Path):
        method, operation_path, authorization_scheme, operation = load_operation(contract_path)
        super().__init__(address, handler)
        self.operation_method = method
        self.operation_path = operation_path
        self.authorization_scheme = authorization_scheme
        self.operation = operation
        self.log_path = log_path
        self.state: dict | None = None
        self.lock = threading.Lock()

    def append_log(self, record: dict) -> None:
        with self.lock:
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, separators=(",", ":"), sort_keys=True))
                stream.write("\n")


class Handler(BaseHTTPRequestHandler):
    server: ContractServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _record_rejection(self, status: int) -> None:
        self.server.append_log(
            {
                "operationId": None,
                "method": self.command,
                "rawTarget": self.path,
                "status": status,
                "rejected": True,
            }
        )

    def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        target = urlsplit(self.path)
        if target.path != self.server.operation_path or target.query:
            self._record_rejection(404)
            self._send_json(404, {"errorCode": "NOT_FOUND"})
            return

        content_length_value = self.headers.get("Content-Length")
        try:
            content_length = int(content_length_value or "")
        except ValueError:
            self._record_rejection(400)
            self._send_json(400, {"errorCode": "INVALID_CONTENT_LENGTH"})
            return

        raw_body = self.rfile.read(content_length)
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._record_rejection(400)
            self._send_json(400, {"errorCode": "JSON_FORMAT_ERROR"})
            return

        validation_error = validate_known_properties(payload, self.server.operation)
        authorization = self.headers.get("Authorization", "")
        content_type = self.headers.get("Content-Type", "")
        if not authorization.startswith(f"{self.server.authorization_scheme} "):
            status = 401
            error = "NOT_AUTHENTICATED"
        elif content_type != self.server.operation["request"]["contentType"]:
            status = 415
            error = "UNSUPPORTED_MEDIA_TYPE"
        elif validation_error:
            status = 400
            error = validation_error
        else:
            status = 200
            error = None

        canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        with self.server.lock:
            mutated = status == 200 and self.server.state != payload
            if status == 200:
                self.server.state = payload

        state_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        record = {
            "operationId": EXPECTED_OPERATION_ID,
            "method": self.command,
            "rawTarget": self.path,
            "headers": {key.lower(): value for key, value in self.headers.items()},
            "contentLength": content_length,
            "rawBody": raw_body.decode("utf-8", errors="replace"),
            "jsonBody": payload,
            "status": status,
            "mutated": mutated,
            "stateHash": state_hash,
        }
        self.server.append_log(record)

        if error is not None:
            self._send_json(status, {"errorCode": error})
            return

        response_properties = self.server.operation["responses"]["200"]["schema"]["properties"]
        response = {key: payload[key] for key in response_properties if key in payload}
        # Return the schema-valid URL list in canonical server order so the verifier can
        # distinguish the parsed response from a client merely returning its input.
        if "URLs" in response:
            response["URLs"] = list(reversed(response["URLs"]))
        self._send_json(200, response)

    def _reject_uncontracted_method(self) -> None:
        self._record_rejection(404)
        self._send_json(404, {"errorCode": "NOT_FOUND"})

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._reject_uncontracted_method()

    def do_POST(self) -> None:  # noqa: N802 - explicitly reject uncontracted methods
        self._reject_uncontracted_method()

    def do_PATCH(self) -> None:  # noqa: N802 - explicitly reject uncontracted methods
        self._reject_uncontracted_method()

    def do_DELETE(self) -> None:  # noqa: N802 - explicitly reject uncontracted methods
        self._reject_uncontracted_method()

    def do_HEAD(self) -> None:  # noqa: N802 - explicitly reject uncontracted methods
        self._reject_uncontracted_method()

    def do_OPTIONS(self) -> None:  # noqa: N802 - explicitly reject uncontracted methods
        self._reject_uncontracted_method()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--request-log", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.request_log.parent.mkdir(parents=True, exist_ok=True)
    args.request_log.write_text("", encoding="utf-8")
    server = ContractServer(
        ("127.0.0.1", args.port), Handler, args.contract.resolve(), args.request_log.resolve()
    )
    args.ready_file.write_text(str(server.server_address[1]), encoding="utf-8")
    server.serve_forever()


if __name__ == "__main__":
    main()
