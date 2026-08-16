#!/usr/bin/env python3
"""Loopback-only mock for the focused reference-derived VCF Automation contract."""

from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


EXPECTED_OPERATIONS = {
    (
        "GET",
        "/deployment/api/deployments/{deploymentId}/resources",
    ): "Get Deployment Resources",
    (
        "GET",
        "/deployment/api/deployments/{deploymentId}/resources/{resourceId}/actions",
    ): "Get Resource Actions 1",
    (
        "POST",
        "/deployment/api/deployments/{deploymentId}/resources/{resourceId}/requests",
    ): "Submit Resource Action Request 1",
}


def load_and_pin_contract(path: Path) -> None:
    contract = json.loads(path.read_text(encoding="utf-8"))
    actual = {
        (operation["method"], operation["path"]): operation["operation"]
        for operation in contract["operations"]
    }
    if actual != EXPECTED_OPERATIONS:
        raise SystemExit("contract operations do not match the loopback mock")
    if contract.get("contract_kind") != "reference-documentation-derived":
        raise SystemExit("contract must remain reference-documentation-derived")


class ContractServer(ThreadingHTTPServer):
    def __init__(self, mode: str, log_path: Path):
        super().__init__(("127.0.0.1", 0), Handler)
        self.mode = mode
        self.log_path = log_path
        self.collection_response_count = 0

    def log_request_record(self, record: dict[str, object]) -> None:
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
            stream.flush()

    def flip_collection(self, items: list[dict[str, object]]) -> list[dict[str, object]]:
        self.collection_response_count += 1
        if self.collection_response_count % 2:
            return list(reversed(items))
        return list(items)


class Handler(BaseHTTPRequestHandler):
    server: ContractServer

    RESOURCE_RE = re.compile(
        r"^/deployment/api/deployments/([^/]+)/resources$"
    )
    ACTIONS_RE = re.compile(
        r"^/deployment/api/deployments/([^/]+)/resources/([^/]+)/actions$"
    )
    REQUESTS_RE = re.compile(
        r"^/deployment/api/deployments/([^/]+)/resources/([^/]+)/requests$"
    )

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _record(self, body: str | None = None) -> tuple[str, str]:
        parsed = urlsplit(self.path)
        record: dict[str, object] = {
            "method": self.command,
            "path": parsed.path,
            "query": parsed.query,
            "authorization": self.headers.get("Authorization"),
        }
        if body is not None:
            record["body"] = body
        self.server.log_request_record(record)
        return parsed.path, parsed.query

    def _json(self, status: int, value: object) -> None:
        body = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        value = self.headers.get("Authorization", "")
        if value.startswith("Bearer ") and len(value) > len("Bearer "):
            return True
        self._json(401, {"message": "bearer token required"})
        return False

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path, _query = self._record()
        if not self._authorized():
            return

        resource_match = self.RESOURCE_RE.fullmatch(path)
        if resource_match:
            if unquote(resource_match.group(1)) != "dep/blue":
                self._json(404, {"message": "deployment not found"})
                return
            if self.server.mode == "list-fail":
                self._json(503, {"message": "deployment resources unavailable"})
                return
            items = [
                {"id": "resource-b", "name": "alpha node", "type": "VirtualMachine"},
                {"id": "resource-z", "name": "zeta node", "type": "VirtualMachine"},
                {"id": "resource-a", "name": "alpha node", "type": "Network"},
                {"id": "resource-upper", "name": "Alpha node", "type": "Disk"},
            ]
            content = self.server.flip_collection(items)
            self._json(
                200,
                {
                    "content": content,
                    "number": 0,
                    "numberOfElements": len(content),
                    "totalElements": len(content),
                    "totalPages": 1,
                },
            )
            return

        action_match = self.ACTIONS_RE.fullmatch(path)
        if action_match:
            if (
                unquote(action_match.group(1)) != "dep/blue"
                or unquote(action_match.group(2)) != "vm one/α?#%"
            ):
                self._json(404, {"message": "resource not found"})
                return
            if self.server.mode == "http-fail":
                self._json(404, {"message": "resource not found"})
                return
            if self.server.mode == "malformed":
                body = b"[{"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.server.mode == "absent-action":
                self._json(
                    200,
                    [{"id": "Snapshot", "name": "Create Snapshot", "valid": True}],
                )
                return
            items = [
                {"id": "Snapshot", "name": "Create Snapshot", "valid": True},
                {
                    "id": "PowerOff",
                    "name": "Power Off",
                    "valid": self.server.mode != "invalid-action",
                },
            ]
            self._json(200, self.server.flip_collection(items))
            return

        self._json(404, {"message": "operation is outside the focused contract"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length", "0"))
        body_bytes = self.rfile.read(length)
        body = body_bytes.decode("utf-8", errors="replace")
        path, _query = self._record(body)
        if not self._authorized():
            return

        request_match = self.REQUESTS_RE.fullmatch(path)
        if not request_match:
            self._json(404, {"message": "operation is outside the focused contract"})
            return
        if (
            unquote(request_match.group(1)) != "dep/blue"
            or unquote(request_match.group(2)) != "vm one/α?#%"
        ):
            self._json(404, {"message": "resource not found"})
            return
        if self.headers.get_content_type() != "application/json":
            self._json(415, {"message": "application/json required"})
            return
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self._json(400, {"message": "invalid JSON"})
            return
        expected_reason = (
            'operator said "pause"\nnow'
            if self.server.mode == "ok"
            else "maintenance"
        )
        expected_action = "Snapshot" if self.server.mode == "ok" else "PowerOff"
        if (
            payload.get("actionId") != expected_action
            or ("inputs" in payload and payload["inputs"] != {})
            or payload.get("reason") != expected_reason
        ):
            self._json(400, {"message": "invalid resource action request"})
            return
        if self.server.mode == "mutation-fail":
            self._json(409, {"message": "resource action conflicts"})
            return
        self._json(
            200,
            {"id": "request-001", "actionId": expected_action, "status": "CREATED"},
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument(
        "--mode",
        choices=(
            "ok",
            "http-fail",
            "invalid-action",
            "absent-action",
            "malformed",
            "list-fail",
            "mutation-fail",
        ),
        required=True,
    )
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()

    load_and_pin_contract(args.contract)
    args.log.parent.mkdir(parents=True, exist_ok=True)
    args.log.write_text("", encoding="utf-8")
    server = ContractServer(args.mode, args.log)
    print(server.server_port, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
