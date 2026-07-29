#!/usr/bin/env python3
"""Contract-pinned loopback mock for the selected NSX Policy operations."""

from __future__ import annotations

import argparse
import base64
import json
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit


EXPECTED_OPERATIONS = {
    "OrgsOrgIdProjectsProjectIdInfraUpdateSecurityPolicyForDomain",
    "OrgsOrgIdProjectsProjectIdInfraReadIntentStatus",
    "OrgsOrgIdProjectsProjectIdInfraListSecurityPoliciesForDomain",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--ready", type=Path, required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    return parser.parse_args()


class MockState:
    def __init__(
        self,
        contract: dict[str, Any],
        log_path: Path,
        username: str,
        password: str,
    ) -> None:
        operations = contract.get("operations")
        if not isinstance(operations, list):
            raise ValueError("contract operations must be an array")
        operation_ids = {item.get("operationId") for item in operations}
        if operation_ids != EXPECTED_OPERATIONS:
            raise ValueError(f"unexpected contract operation set: {operation_ids!r}")

        self.base_path = contract["api"]["basePath"].rstrip("/")
        self.routes: list[tuple[str, str, str]] = []
        for operation in operations:
            self.routes.append(
                (
                    operation["method"],
                    operation["path"],
                    operation["operationId"],
                )
            )

        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        self.expected_authorization = f"Basic {token}"
        self.log_path = log_path
        self.log_path.write_text("", encoding="utf-8")
        self.lock = threading.Lock()
        self.sequence = 0
        self.status_polls: dict[tuple[str, str, str], int] = {}
        self.policies: dict[tuple[str, str, str, str], dict[str, str]] = {}
        self.realized_domains: set[tuple[str, str, str]] = set()
        self.list_count = 0

    def match(
        self, method: str, request_path: str
    ) -> tuple[str | None, dict[str, str]]:
        if not request_path.startswith(self.base_path + "/"):
            return None, {}
        relative = request_path[len(self.base_path) :]
        request_segments = relative.strip("/").split("/")

        for route_method, template, operation_id in self.routes:
            if route_method != method:
                continue
            template_segments = template.strip("/").split("/")
            if len(template_segments) != len(request_segments):
                continue
            values: dict[str, str] = {}
            matched = True
            for expected, actual in zip(template_segments, request_segments):
                if expected.startswith("{") and expected.endswith("}"):
                    values[expected[1:-1]] = unquote(actual)
                elif expected != actual:
                    matched = False
                    break
            if matched:
                return operation_id, values
        return None, {}

    def append_log(self, entry: dict[str, Any]) -> None:
        with self.lock:
            self.sequence += 1
            entry["sequence"] = self.sequence
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, sort_keys=True, separators=(",", ":")))
                stream.write("\n")


class Handler(BaseHTTPRequestHandler):
    server: "MockServer"

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        self._handle()

    def do_PUT(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        self._handle()

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        self._handle()

    def do_PATCH(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        self._handle()

    def do_DELETE(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        self._handle()

    def log_message(self, format: str, *args: object) -> None:
        return

    def _handle(self) -> None:
        parsed = urlsplit(self.path)
        operation_id, path_values = self.server.state.match(
            self.command, parsed.path
        )
        query = {
            key: values
            for key, values in parse_qs(parsed.query, keep_blank_values=True).items()
        }
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length) if content_length else b""
        entry: dict[str, Any] = {
            "method": self.command,
            "operationId": operation_id,
            "path": parsed.path,
            "query": query,
        }

        if operation_id is None:
            self._respond(404, {"error": "operation not in pinned contract"}, entry)
            return
        if self.headers.get("Authorization") != self.server.state.expected_authorization:
            self._respond(401, {"error": "missing or invalid Basic authentication"}, entry)
            return
        if self.headers.get("Accept") != "application/json":
            self._respond(406, {"error": "Accept must be application/json"}, entry)
            return

        if (
            operation_id
            == "OrgsOrgIdProjectsProjectIdInfraUpdateSecurityPolicyForDomain"
        ):
            self._put_policy(path_values, raw_body, entry)
        elif operation_id == "OrgsOrgIdProjectsProjectIdInfraReadIntentStatus":
            self._read_status(path_values, query, entry)
        elif (
            operation_id
            == "OrgsOrgIdProjectsProjectIdInfraListSecurityPoliciesForDomain"
        ):
            self._list_policies(path_values, entry)
        else:
            self._respond(500, {"error": "mock has no behavior for operation"}, entry)

    def _put_policy(
        self,
        path_values: dict[str, str],
        raw_body: bytes,
        entry: dict[str, Any],
    ) -> None:
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            self._respond(415, {"error": "Content-Type must be application/json"}, entry)
            return
        try:
            body = json.loads(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._respond(400, {"error": "invalid JSON"}, entry)
            return
        entry["request_json"] = body
        if (
            not isinstance(body, dict)
            or body.get("resource_type") != "SecurityPolicy"
            or body.get("category") != "Application"
            or not isinstance(body.get("display_name"), str)
        ):
            self._respond(400, {"error": "invalid SecurityPolicy body"}, entry)
            return

        org_id = path_values["org-id"]
        project_id = path_values["project-id"]
        domain_id = path_values["domain-id"]
        policy_id = path_values["security-policy-id"]
        policy = {
            "resource_type": "SecurityPolicy",
            "id": policy_id,
            "display_name": body["display_name"],
            "category": "Application",
            "path": f"/infra/domains/{domain_id}/security-policies/{policy_id}",
        }
        key = (org_id, project_id, domain_id, policy_id)
        intent_path = policy["path"]
        self.server.state.policies[key] = policy
        self.server.state.status_polls[(org_id, project_id, intent_path)] = 0
        self.server.state.realized_domains.discard((org_id, project_id, domain_id))
        self._respond(200, policy, entry)

    def _read_status(
        self,
        path_values: dict[str, str],
        query: dict[str, list[str]],
        entry: dict[str, Any],
    ) -> None:
        values = query.get("intent_path")
        if values is None or len(values) != 1:
            self._respond(400, {"error": "intent_path is required exactly once"}, entry)
            return
        intent_path = values[0]
        org_id = path_values["org-id"]
        project_id = path_values["project-id"]
        poll_key = (org_id, project_id, intent_path)
        if poll_key not in self.server.state.status_polls:
            self._respond(404, {"error": "unknown intent_path"}, entry)
            return

        poll = self.server.state.status_polls[poll_key] + 1
        self.server.state.status_polls[poll_key] = poll
        if poll < 3:
            consolidated = "IN_PROGRESS"
            publish = "UNREALIZED"
        else:
            consolidated = "SUCCESS"
            publish = "REALIZED"
            prefix = "/infra/domains/"
            suffix = "/security-policies/"
            if intent_path.startswith(prefix) and suffix in intent_path:
                domain_id = intent_path[len(prefix) : intent_path.index(suffix)]
                self.server.state.realized_domains.add((org_id, project_id, domain_id))

        entry["poll_number"] = poll
        entry["returned_consolidated_status"] = consolidated
        self._respond(
            200,
            {
                "consolidated_status": {
                    "consolidated_status": consolidated,
                },
                "publish_status": publish,
                "intent_path": intent_path,
            },
            entry,
        )

    def _list_policies(
        self,
        path_values: dict[str, str],
        entry: dict[str, Any],
    ) -> None:
        org_id = path_values["org-id"]
        project_id = path_values["project-id"]
        domain_id = path_values["domain-id"]
        domain_key = (org_id, project_id, domain_id)
        if domain_key not in self.server.state.realized_domains:
            self._respond(
                409,
                {"error": "policy realization has not reached a terminal success state"},
                entry,
            )
            return

        created = [
            value
            for key, value in self.server.state.policies.items()
            if key[:3] == domain_key
        ]
        if not created:
            self._respond(500, {"error": "collection unexpectedly empty"}, entry)
            return
        results = list(created)
        self.server.state.list_count += 1
        if self.server.state.list_count % 2 == 0:
            results.reverse()
        entry["response_order"] = [item["display_name"] for item in results]
        self._respond(
            200,
            {
                "result_count": len(results),
                "results": results,
            },
            entry,
        )

    def _respond(
        self,
        status: int,
        payload: dict[str, Any],
        entry: dict[str, Any],
    ) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        entry["response_status"] = status
        self.server.state.append_log(entry)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class MockServer(ThreadingHTTPServer):
    def __init__(self, state: MockState) -> None:
        super().__init__(("127.0.0.1", 0), Handler)
        self.state = state


def main() -> int:
    args = parse_args()
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    state = MockState(contract, args.log, args.username, args.password)
    server = MockServer(state)

    def stop(_signum: int, _frame: object) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    args.ready.write_text(base_url, encoding="utf-8")
    server.serve_forever(poll_interval=0.05)
    server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
