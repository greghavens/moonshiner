#!/usr/bin/env python3
"""Loopback-only NSX Policy mock constrained by docs/contract.json."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit


EXPECTED_OPERATION_IDS = {
    "ListAllInfraSegments",
    "PatchInfraSegment",
    "ReadIntentStatus",
}


def compile_contract_path(path: str) -> tuple[re.Pattern[str], list[str]]:
    names: list[str] = []
    pieces: list[str] = []
    for piece in path.strip("/").split("/"):
        if piece.startswith("{") and piece.endswith("}"):
            names.append(piece[1:-1])
            pieces.append(r"([^/]+)")
        else:
            pieces.append(re.escape(piece))
    return re.compile(r"^/" + "/".join(pieces) + r"$"), names


class ContractState:
    def __init__(self, contract_path: Path, log_path: Path) -> None:
        self.contract = json.loads(contract_path.read_text(encoding="utf-8"))
        operations = self.contract.get("operations")
        if not isinstance(operations, list):
            raise ValueError("contract.operations must be an array")

        actual_ids = {item.get("operationId") for item in operations}
        if actual_ids != EXPECTED_OPERATION_IDS:
            raise ValueError(
                f"mock requires exactly {sorted(EXPECTED_OPERATION_IDS)}, "
                f"contract has {sorted(str(item) for item in actual_ids)}"
            )

        self.routes: list[dict[str, Any]] = []
        for operation in operations:
            pattern, parameter_names = compile_contract_path(operation["path"])
            self.routes.append(
                {
                    "operation": operation,
                    "pattern": pattern,
                    "parameter_names": parameter_names,
                }
            )

        self.log_path = log_path
        self.lock = threading.Lock()
        self.sequence = 0
        self.collection_responses = 0
        self.polls: dict[str, int] = {}
        self.segments: dict[str, dict[str, Any]] = {}

        source_sha = self.contract["source"]["commitSha"]
        digest = hashlib.sha256(source_sha.encode("ascii")).hexdigest()
        labels = ("Zulu", "Alpha", "Mike", "Bravo")
        self.inventory = [
            {
                "resource_type": "Segment",
                "id": f"segment-{digest[index * 4:(index + 1) * 4]}",
                "display_name": f"{label}-{digest[20 + index * 3:23 + index * 3]}",
                "path": f"/infra/segments/segment-{digest[index * 4:(index + 1) * 4]}",
            }
            for index, label in enumerate(labels)
        ]
        self.cursor_token = f"cursor-{digest[32:44]}"

    def match(
        self, method: str, path: str
    ) -> tuple[dict[str, Any] | None, dict[str, str]]:
        for route in self.routes:
            operation = route["operation"]
            if operation["method"] != method:
                continue
            match = route["pattern"].match(path)
            if match is None:
                continue
            values = [unquote(item) for item in match.groups()]
            return operation, dict(zip(route["parameter_names"], values))
        return None, {}

    def append_log(self, entry: dict[str, Any]) -> None:
        with self.lock:
            self.sequence += 1
            entry["sequence"] = self.sequence
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, sort_keys=True, separators=(",", ":")))
                stream.write("\n")

    def next_collection(
        self, page_items: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], str]:
        with self.lock:
            self.collection_responses += 1
            reverse = self.collection_responses % 2 == 1
            results = list(reversed(page_items)) if reverse else list(page_items)
            return results, "reversed" if reverse else "canonical"

    def record_patch(self, segment_id: str, body: dict[str, Any]) -> None:
        with self.lock:
            self.segments[segment_id] = dict(body)
            self.polls[segment_id] = 0

    def next_status(self, segment_id: str) -> tuple[str, int]:
        with self.lock:
            if segment_id not in self.polls:
                return "UNINITIALIZED", 0
            self.polls[segment_id] += 1
            count = self.polls[segment_id]
            return ("SUCCESS" if count >= 3 else "IN_PROGRESS"), count


class ContractHandler(BaseHTTPRequestHandler):
    server_version = "VcfNsxPolicyContractMock/1"
    protocol_version = "HTTP/1.1"

    @property
    def state(self) -> ContractState:
        return self.server.contract_state  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def do_POST(self) -> None:
        self._reject_uncontracted("POST")

    def do_PUT(self) -> None:
        self._reject_uncontracted("PUT")

    def do_DELETE(self) -> None:
        self._reject_uncontracted("DELETE")

    def _read_json_body(self) -> tuple[dict[str, Any] | None, str | None]:
        length_text = self.headers.get("Content-Length", "0")
        try:
            length = int(length_text)
        except ValueError:
            return None, "invalid Content-Length"
        payload = self.rfile.read(length) if length else b""
        if not payload:
            return None, None
        try:
            parsed = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, "request body must be UTF-8 JSON"
        if not isinstance(parsed, dict):
            return None, "request body must be a JSON object"
        return parsed, None

    def _dispatch(self, method: str) -> None:
        target = urlsplit(self.path)
        query = parse_qs(target.query, keep_blank_values=True)
        operation, path_parameters = self.state.match(method, target.path)
        if operation is None:
            self._reject_uncontracted(method)
            return

        allowed_query = {
            parameter["name"] for parameter in operation.get("queryParameters", [])
        }
        unexpected_query = sorted(set(query) - allowed_query)
        if unexpected_query:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"error_message": f"query parameters outside contract: {unexpected_query}"},
            )
            return

        authorization = self.headers.get("Authorization", "")
        if not authorization.startswith("Bearer ") or len(authorization) <= len("Bearer "):
            self._write_json(
                HTTPStatus.UNAUTHORIZED,
                {"error_message": "Bearer authorization is required"},
            )
            return

        body: dict[str, Any] | None = None
        body_error: str | None = None
        if method == "PATCH":
            body, body_error = self._read_json_body()
            if body_error:
                self._write_json(
                    HTTPStatus.BAD_REQUEST, {"error_message": body_error}
                )
                return

        operation_id = operation["operationId"]
        log_entry: dict[str, Any] = {
            "operationId": operation_id,
            "method": method,
            "path": target.path,
            "query": query,
            "authorizationScheme": authorization.split(" ", 1)[0],
            "contentType": self.headers.get("Content-Type"),
        }

        if operation_id == "ListAllInfraSegments":
            cursor_values = query.get("cursor", [])
            if len(cursor_values) > 1 or (
                cursor_values and cursor_values[0] != self.state.cursor_token
            ):
                self._write_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error_message": "cursor was not issued by this collection"},
                )
                return
            if cursor_values:
                page_items = self.state.inventory[2:]
                next_cursor = None
            else:
                page_items = self.state.inventory[:2]
                next_cursor = self.state.cursor_token

            results, response_order = self.state.next_collection(page_items)
            log_entry["responseOrder"] = response_order
            self.state.append_log(log_entry)
            response_body: dict[str, Any] = {
                "sort_by": "display_name",
                "sort_ascending": True,
                "results": results,
            }
            if next_cursor is not None:
                response_body["result_count"] = len(self.state.inventory)
                response_body["cursor"] = next_cursor
            self._write_json(
                HTTPStatus.OK,
                response_body,
            )
            return

        if operation_id == "PatchInfraSegment":
            segment_id = path_parameters["segment-id"]
            content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0]
            if content_type.lower() != "application/json":
                self._write_json(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    {"error_message": "application/json is required"},
                )
                return
            if body is None:
                self._write_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error_message": "Segment body is required"},
                )
                return
            if (
                body.get("resource_type") != "Segment"
                or body.get("id") != segment_id
                or not isinstance(body.get("display_name"), str)
                or not body["display_name"]
                or len(body["display_name"]) > 255
            ):
                self._write_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error_message": "body does not satisfy the focused Segment schema"},
                )
                return
            self.state.record_patch(segment_id, body)
            log_entry["body"] = body
            self.state.append_log(log_entry)
            self._write_empty(HTTPStatus.OK)
            return

        if operation_id == "ReadIntentStatus":
            values = query.get("intent_path", [])
            if len(values) != 1 or not values[0].startswith("/infra/segments/"):
                self._write_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error_message": "one segment intent_path is required"},
                )
                return
            segment_id = values[0].removeprefix("/infra/segments/")
            status, poll_count = self.state.next_status(segment_id)
            log_entry["pollCount"] = poll_count
            self.state.append_log(log_entry)
            self._write_json(
                HTTPStatus.OK,
                {
                    "intent_path": values[0],
                    "consolidated_status": {"consolidated_status": status},
                    "publish_status": "REALIZED" if status == "SUCCESS" else "UNREALIZED",
                },
            )
            return

        self._reject_uncontracted(method)

    def _reject_uncontracted(self, method: str) -> None:
        target = urlsplit(self.path)
        self.state.append_log(
            {
                "operationId": None,
                "method": method,
                "path": target.path,
                "query": parse_qs(target.query, keep_blank_values=True),
                "authorizationScheme": None,
                "contentType": self.headers.get("Content-Type"),
            }
        )
        self._write_json(
            HTTPStatus.NOT_FOUND,
            {"error_message": "operation is not present in the pinned contract"},
        )

    def _write_empty(self, status: HTTPStatus) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    def _write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--request-log", type=Path, required=True)
    parser.add_argument("--port-file", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.request_log.write_text("", encoding="utf-8")
    state = ContractState(args.contract, args.request_log)
    server = ThreadingHTTPServer(("127.0.0.1", 0), ContractHandler)
    server.contract_state = state  # type: ignore[attr-defined]

    temporary_port_file = args.port_file.with_suffix(args.port_file.suffix + ".tmp")
    temporary_port_file.write_text(str(server.server_port), encoding="ascii")
    os.replace(temporary_port_file, args.port_file)

    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
