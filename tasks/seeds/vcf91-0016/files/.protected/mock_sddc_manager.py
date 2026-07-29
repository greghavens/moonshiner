#!/usr/bin/env python3
"""Loopback-only SDDC Manager mock driven by docs/contract.json."""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


HTTP_METHODS = frozenset({"delete", "get", "head", "options", "patch", "post", "put"})


class ContractError(ValueError):
    """Raised when a request does not satisfy the selected OpenAPI contract."""


class Contract:
    def __init__(self, path: Path) -> None:
        self.document = json.loads(path.read_text(encoding="utf-8"))
        self.schemas = self.document.get("components", {}).get("schemas", {})
        self.routes: dict[tuple[str, str], dict[str, Any]] = {}
        for route, path_item in self.document.get("paths", {}).items():
            for method, operation in path_item.items():
                if method.lower() not in HTTP_METHODS or not isinstance(operation, dict):
                    continue
                operation_id = operation.get("operationId")
                if not isinstance(operation_id, str) or not operation_id:
                    raise ContractError(f"operation at {method.upper()} {route} has no operationId")
                self.routes[(method.upper(), route)] = operation
        if not self.routes:
            raise ContractError("contract names no operations")

    def operation(self, method: str, target: str) -> dict[str, Any] | None:
        parsed = urlsplit(target)
        if parsed.query or parsed.fragment:
            return None
        return self.routes.get((method, parsed.path))

    def request_schema(self, operation: dict[str, Any]) -> dict[str, Any]:
        try:
            return operation["requestBody"]["content"]["application/json"]["schema"]
        except (KeyError, TypeError) as exc:
            raise ContractError("named operation has no application/json request schema") from exc

    def response_status(self, operation: dict[str, Any], status_class: int) -> int:
        statuses = sorted(
            int(code)
            for code in operation.get("responses", {})
            if code.isdigit() and int(code) // 100 == status_class
        )
        if not statuses:
            raise ContractError(f"named operation has no {status_class}xx response")
        return statuses[0]

    def validate(self, value: Any, schema: dict[str, Any], *, request: bool = True) -> None:
        reference = schema.get("$ref")
        if reference is not None:
            prefix = "#/components/schemas/"
            if not isinstance(reference, str) or not reference.startswith(prefix):
                raise ContractError(f"unsupported schema reference: {reference!r}")
            name = reference[len(prefix) :]
            try:
                target = self.schemas[name]
            except KeyError as exc:
                raise ContractError(f"missing referenced schema: {name}") from exc
            self.validate(value, target, request=request)
            return

        expected_type = schema.get("type")
        if expected_type == "object":
            if not isinstance(value, dict):
                raise ContractError("expected a JSON object")
            properties = schema.get("properties", {})
            for name in schema.get("required", []):
                if name not in value:
                    raise ContractError(f"missing required property: {name}")
            for name, member in value.items():
                member_schema = properties.get(name)
                if member_schema is None:
                    continue
                if request and member_schema.get("readOnly") is True:
                    raise ContractError(f"read-only property in request: {name}")
                self.validate(member, member_schema, request=request)
            return
        if expected_type == "array":
            if not isinstance(value, list):
                raise ContractError("expected a JSON array")
            item_schema = schema.get("items", {})
            for item in value:
                self.validate(item, item_schema, request=request)
            return
        if expected_type == "string":
            if not isinstance(value, str):
                raise ContractError("expected a JSON string")
            if "minLength" in schema and len(value) < schema["minLength"]:
                raise ContractError("string is shorter than minLength")
            if "maxLength" in schema and len(value) > schema["maxLength"]:
                raise ContractError("string is longer than maxLength")
            return
        if expected_type == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                raise ContractError("expected a JSON integer")
            return
        if expected_type == "boolean" and not isinstance(value, bool):
            raise ContractError("expected a JSON boolean")


class MockState:
    def __init__(self, request_log: Path) -> None:
        self.request_log = request_log
        self.lock = threading.Lock()
        self.sequence = 0
        self.effect_count = 0
        self.current_payload: str | None = None

    def apply_and_record(
        self,
        *,
        operation_id: str,
        method: str,
        target: str,
        headers: list[tuple[str, str]],
        body: bytes,
        body_json: dict[str, Any],
        response_status: int,
    ) -> None:
        canonical_payload = json.dumps(
            body_json, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        with self.lock:
            self.sequence += 1
            effect_applied = canonical_payload != self.current_payload
            if effect_applied:
                self.current_payload = canonical_payload
                self.effect_count += 1
            entry = {
                "sequence": self.sequence,
                "operationId": operation_id,
                "method": method,
                "target": target,
                "headers": headers,
                "body_hex": body.hex(),
                "body_json": body_json,
                "effect_applied": effect_applied,
                "effect_count": self.effect_count,
                "response_status": response_status,
            }
            with self.request_log.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


class ContractHTTPServer(HTTPServer):
    contract: Contract
    state: MockState


class Handler(BaseHTTPRequestHandler):
    server: ContractHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *args: object) -> None:
        del args

    def _send_json(self, status: int, value: dict[str, Any]) -> None:
        data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def _dispatch(self) -> None:
        operation = self.server.contract.operation(self.command, self.path)
        if operation is None:
            self._send_json(404, {"message": "operation is not present in the contract"})
            return

        media_type = self.headers.get("Content-Type")
        if media_type != "application/json":
            self._send_json(400, {"message": "Content-Type must be application/json"})
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
            if length < 0:
                raise ValueError
        except ValueError:
            self._send_json(400, {"message": "invalid Content-Length"})
            return
        body = self.rfile.read(length)
        try:
            decoded = json.loads(body.decode("utf-8"))
            if not isinstance(decoded, dict):
                raise ContractError("request body must be an object")
            self.server.contract.validate(
                decoded, self.server.contract.request_schema(operation)
            )
            if not ({"vmwareAccount", "offlineAccount"} & decoded.keys()):
                raise ContractError("a depot account must be supplied")
        except (ContractError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send_json(400, {"message": str(exc)})
            return

        next_sequence = self.server.state.sequence + 1
        response_status = self.server.contract.response_status(
            operation, 5 if next_sequence == 1 else 2
        )
        self.server.state.apply_and_record(
            operation_id=operation["operationId"],
            method=self.command,
            target=self.path,
            headers=list(self.headers.raw_items()),
            body=body,
            body_json=decoded,
            response_status=response_status,
        )
        if response_status // 100 == 5:
            self._send_json(
                response_status,
                {
                    "errorCode": "TRANSIENT_AFTER_APPLY",
                    "message": "mutation applied before acknowledgement was lost",
                },
            )
            return
        self._send_json(response_status, decoded)

    do_DELETE = _dispatch
    do_GET = _dispatch
    do_HEAD = _dispatch
    do_OPTIONS = _dispatch
    do_PATCH = _dispatch
    do_POST = _dispatch
    do_PUT = _dispatch


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--request-log", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    contract = Contract(args.contract)
    args.request_log.parent.mkdir(parents=True, exist_ok=True)
    args.request_log.touch()
    server = ContractHTTPServer(("127.0.0.1", 0), Handler)
    server.contract = contract
    server.state = MockState(args.request_log)
    port = server.server_address[1]
    args.ready_file.write_text(
        json.dumps({"base_url": f"http://127.0.0.1:{port}"}),
        encoding="utf-8",
    )
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
