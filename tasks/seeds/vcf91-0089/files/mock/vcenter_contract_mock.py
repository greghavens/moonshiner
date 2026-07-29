#!/usr/bin/env python3
"""Loopback-only mock constrained to the focused VCF 9.1 vCenter contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit


EXPECTED_OPERATION_IDS = {
    "Vcenter.VM_clone$Task",
    "Cis.Tasks_get",
}


def compile_path(path: str) -> tuple[re.Pattern[str], list[str]]:
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
    def __init__(self, contract_path: Path, request_log: Path) -> None:
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
            pattern, parameter_names = compile_path(operation["path"])
            fixed_query = {
                item["name"]: item["value"]
                for item in operation.get("fixedQuery", [])
            }
            allowed_query = {
                item["name"] for item in operation.get("queryParameters", [])
            }
            self.routes.append(
                {
                    "operation": operation,
                    "pattern": pattern,
                    "parameter_names": parameter_names,
                    "fixed_query": fixed_query,
                    "allowed_query": allowed_query,
                }
            )

        self.request_log = request_log
        self.lock = threading.Lock()
        self.sequence = 0
        self.tasks: dict[str, dict[str, Any]] = {}
        self.source_digest = hashlib.sha256(
            self.contract["source"]["commitSha"].encode("ascii")
        ).hexdigest()

    def match(
        self, method: str, path: str, query: dict[str, list[str]]
    ) -> tuple[dict[str, Any] | None, dict[str, str]]:
        for route in self.routes:
            operation = route["operation"]
            if operation["method"] != method:
                continue
            match = route["pattern"].match(path)
            if match is None:
                continue

            fixed_query = route["fixed_query"]
            expected_keys = set(fixed_query) | route["allowed_query"]
            if set(query) - expected_keys:
                continue
            if any(query.get(name) != [value] for name, value in fixed_query.items()):
                continue

            values = [unquote(value) for value in match.groups()]
            return operation, dict(zip(route["parameter_names"], values))
        return None, {}

    def append_log(self, entry: dict[str, Any]) -> None:
        with self.lock:
            self.sequence += 1
            entry["sequence"] = self.sequence
            with self.request_log.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, sort_keys=True, separators=(",", ":")))
                stream.write("\n")

    def create_task(self, body: dict[str, Any]) -> tuple[str, str]:
        material = json.dumps(body, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(
            (self.source_digest + material).encode("utf-8")
        ).hexdigest()
        task_id = f"task-{digest[:16]}"
        result = f"vm-{digest[16:28]}"
        with self.lock:
            self.tasks[task_id] = {"polls": 0, "result": result}
        return task_id, result

    def next_task_info(self, task_id: str) -> tuple[dict[str, Any] | None, int]:
        with self.lock:
            task = self.tasks.get(task_id)
            if task is None:
                return None, 0
            task["polls"] += 1
            poll_count = int(task["polls"])
            status = ("PENDING", "RUNNING", "SUCCEEDED")[
                min(poll_count - 1, 2)
            ]

        info: dict[str, Any] = {
            "description": {
                "id": "com.vmware.vcenter.vm.clone",
                "default_message": "Clone virtual machine",
                "args": [],
            },
            "service": "com.vmware.vcenter.vm",
            "operation": "clone",
            "status": status,
            "cancelable": status != "SUCCEEDED",
        }
        if status == "SUCCEEDED":
            info["result"] = task["result"]
        return info, poll_count


class ContractHandler(BaseHTTPRequestHandler):
    server_version = "VcfVCenterContractMock/1"
    protocol_version = "HTTP/1.1"

    @property
    def state(self) -> ContractState:
        return self.server.contract_state  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._reject_uncontracted("PUT")

    def do_PATCH(self) -> None:
        self._reject_uncontracted("PATCH")

    def do_DELETE(self) -> None:
        self._reject_uncontracted("DELETE")

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return b""
        return self.rfile.read(length) if length > 0 else b""

    def _dispatch(self, method: str) -> None:
        target = urlsplit(self.path)
        query = parse_qs(target.query, keep_blank_values=True)
        operation, path_parameters = self.state.match(method, target.path, query)
        if operation is None:
            self._reject_uncontracted(method)
            return

        session_token = self.headers.get("vmware-api-session-id")
        if session_token != "loopback-vcenter-session":
            self._write_json(
                HTTPStatus.UNAUTHORIZED,
                {"error_type": "UNAUTHENTICATED"},
            )
            return

        payload = self._read_body()
        raw_body: str | None
        try:
            raw_body = payload.decode("utf-8") if payload else None
        except UnicodeDecodeError:
            raw_body = None

        entry: dict[str, Any] = {
            "operationId": operation["operationId"],
            "method": method,
            "rawTarget": self.path,
            "path": target.path,
            "query": query,
            "accept": self.headers.get("Accept"),
            "contentType": self.headers.get("Content-Type"),
            "sessionToken": session_token,
            "contentLength": len(payload),
            "rawBody": raw_body,
        }

        if operation["operationId"] == "Vcenter.VM_clone$Task":
            media_type = (self.headers.get("Content-Type") or "").split(";", 1)[0]
            if media_type.lower() != "application/json":
                self._write_json(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    {"error_type": "INVALID_REQUEST"},
                )
                return
            try:
                body = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._write_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error_type": "INVALID_ARGUMENT"},
                )
                return
            if not isinstance(body, dict):
                self._write_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error_type": "INVALID_ARGUMENT"},
                )
                return
            if (
                not isinstance(body.get("source"), str)
                or not body["source"]
                or not isinstance(body.get("name"), str)
                or not body["name"]
            ):
                self._write_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error_type": "INVALID_ARGUMENT"},
                )
                return

            task_id, result = self.state.create_task(body)
            entry["body"] = body
            entry["issuedTaskId"] = task_id
            entry["eventualResult"] = result
            self.state.append_log(entry)
            self._write_json(HTTPStatus.ACCEPTED, task_id)
            return

        if operation["operationId"] == "Cis.Tasks_get":
            task_id = path_parameters["task"]
            info, poll_count = self.state.next_task_info(task_id)
            if info is None:
                self._write_json(
                    HTTPStatus.NOT_FOUND,
                    {"error_type": "NOT_FOUND"},
                )
                return
            entry["pollCount"] = poll_count
            entry["returnedStatus"] = info["status"]
            self.state.append_log(entry)
            self._write_json(HTTPStatus.OK, info)
            return

        self._reject_uncontracted(method)

    def _reject_uncontracted(self, method: str) -> None:
        target = urlsplit(self.path)
        payload = self._read_body()
        self.state.append_log(
            {
                "operationId": None,
                "method": method,
                "rawTarget": self.path,
                "path": target.path,
                "query": parse_qs(target.query, keep_blank_values=True),
                "accept": self.headers.get("Accept"),
                "contentType": self.headers.get("Content-Type"),
                "sessionToken": self.headers.get("vmware-api-session-id"),
                "contentLength": len(payload),
                "rawBody": payload.decode("utf-8", errors="replace") or None,
            }
        )
        self._write_json(
            HTTPStatus.NOT_FOUND,
            {"error_type": "OPERATION_OUTSIDE_CONTRACT"},
        )

    def _write_json(self, status: HTTPStatus, value: Any) -> None:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--request-log", type=Path, required=True)
    parser.add_argument("--port-file", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.request_log.parent.mkdir(parents=True, exist_ok=True)
    args.request_log.write_text("", encoding="utf-8")

    state = ContractState(args.contract, args.request_log)
    server = ThreadingHTTPServer(("127.0.0.1", 0), ContractHandler)
    server.contract_state = state  # type: ignore[attr-defined]
    args.port_file.write_text(str(server.server_port), encoding="ascii")
    try:
        server.serve_forever(poll_interval=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
