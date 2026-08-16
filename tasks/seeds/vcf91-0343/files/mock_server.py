#!/usr/bin/env python3
"""Contract-pinned loopback fixture for the protected Java harness."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit


EXPECTED_CONTRACT = {
    "getProjects": ("GET", "/iaas/api/projects", (200,)),
    "updateProject": ("PATCH", "/iaas/api/projects/{id}", (200,)),
    "updateProjectResourceMetadata": (
        "PATCH", "/iaas/api/projects/{id}/resource-metadata", (200,)
    ),
    "updateProjectZoneAssignments": (
        "PUT", "/iaas/api/projects/{id}/zones", (202,)
    ),
}


def load_contract(path: Path) -> dict:
    contract = json.loads(path.read_text(encoding="utf-8"))
    if contract.get("product_version") != "9.1":
        raise ValueError("mock requires the VCF Automation 9.1 contract")
    if contract.get("source_kind") != "reference-documentation":
        raise ValueError("contract must identify its reference-documentation source")
    operations = contract.get("operations", [])
    actual = {
        op["operation_id"]: (
            op["method"], op["path"], tuple(op["success_statuses"])
        )
        for op in operations
    }
    if actual != EXPECTED_CONTRACT:
        raise ValueError("mock operation set does not match docs/contract.json")
    return contract


def route_pattern(template: str) -> re.Pattern[str]:
    escaped = re.escape(template).replace(r"\{id\}", r"(?P<id>[^/]+)")
    return re.compile("^" + escaped + "$")


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, contract: dict, log_path: Path):
        super().__init__(address, handler)
        self.log_path = log_path
        self.collection_responses = 0
        self.routes = [
            (op["method"], route_pattern(op["path"]), op)
            for op in contract["operations"]
        ]


class Handler(BaseHTTPRequestHandler):
    server: ContractServer
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args) -> None:
        pass

    def do_GET(self) -> None:
        self._dispatch()

    def do_PATCH(self) -> None:
        self._dispatch()

    def do_PUT(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def _dispatch(self) -> None:
        path = urlsplit(self.path).path
        size = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(size) if size else b""
        body_text = raw_body.decode("utf-8")
        matched = None
        for method, pattern, operation in self.server.routes:
            match = pattern.fullmatch(path)
            if method == self.command and match:
                matched = (operation, match.groupdict())
                break

        self._log_request(path, body_text, matched[0]["operation_id"] if matched else None)
        if matched is None:
            self._json(404, {"message": "operation is not present in the pinned contract"})
            return
        authorization = self.headers.get("Authorization", "")
        if not authorization.startswith("Bearer ") or not authorization.removeprefix("Bearer "):
            self._json(403, {"message": "missing fixture bearer authorization"})
            return
        operation, params = matched
        operation_id = operation["operation_id"]
        if operation_id != "getProjects" and self.headers.get("Content-Type") != "application/json":
            self._json(400, {"message": "content type must be application/json"})
            return

        if operation_id == "getProjects":
            self._get_projects()
        elif operation_id == "updateProject":
            self._update_project(params["id"], body_text)
        elif operation_id == "updateProjectResourceMetadata":
            self._update_metadata(params["id"], body_text)
        elif operation_id == "updateProjectZoneAssignments":
            self._update_zones(body_text)
        else:
            raise AssertionError("contract loader admitted an unknown operation")

    def _log_request(self, path: str, body: str, operation_id: str | None) -> None:
        entry = {
            "method": self.command,
            "path": path,
            "operation_id": operation_id,
            "authorization": self.headers.get("Authorization"),
            "content_type": self.headers.get("Content-Type"),
            "body": body,
        }
        with self.server.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, separators=(",", ":")) + "\n")

    def _body_object(self, body: str) -> dict | None:
        try:
            value = json.loads(body)
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None

    def _get_projects(self) -> None:
        canonical = [
            {"id": "project-a", "name": "Alpha Project"},
            {"id": "project-b", "name": "Alpha Project"},
            {"id": "project-payments", "name": "Payments Platform"},
            {"id": "project-z", "name": "Zeta Project"},
        ]
        self.server.collection_responses += 1
        content = canonical if self.server.collection_responses % 2 == 0 else list(reversed(canonical))
        self._json(200, {
            "content": content,
            "totalElements": len(content),
            "numberOfElements": len(content),
        })

    def _update_project(self, encoded_id: str, body: str) -> None:
        value = self._body_object(body)
        if (
            not value
            or not isinstance(value.get("name"), str)
            or ("description" in value and value["description"] is not None
                and not isinstance(value["description"], str))
        ):
            self._json(400, {"message": "project name is required"})
            return
        if value["name"] == "rejected":
            self._json(400, {"message": "project update rejected"})
            return
        self._json(200, {
            "id": encoded_id,
            "name": value["name"],
            "description": value.get("description", ""),
            "_links": {"empty": False},
        })

    def _update_metadata(self, encoded_id: str, body: str) -> None:
        value = self._body_object(body)
        tags = value.get("tags") if value else None
        if (
            not isinstance(tags, list)
            or any(
                not isinstance(tag, dict)
                or not isinstance(tag.get("key"), str)
                or ("value" in tag and tag["value"] is not None
                    and not isinstance(tag["value"], str))
                for tag in tags
            )
        ):
            self._json(400, {"message": "tags must be an array"})
            return
        if any(tag["key"] == "restricted" for tag in tags):
            self._json(403, {"message": "metadata update forbidden"})
            return
        self._json(200, {
            "id": encoded_id,
            "name": "Payments Platform",
            "_links": {"empty": False},
        })

    def _update_zones(self, body: str) -> None:
        value = self._body_object(body)
        assignments = value.get("zoneAssignmentSpecifications") if value else None
        if (
            not isinstance(assignments, list)
            or any(
                not isinstance(assignment, dict)
                or not isinstance(assignment.get("zoneId"), str)
                or not isinstance(assignment.get("priority"), int)
                or not isinstance(assignment.get("maxNumberInstances"), int)
                for assignment in assignments
            )
        ):
            self._json(400, {"message": "zoneAssignmentSpecifications must be an array"})
            return
        if any(assignment["zoneId"] == "zone-retired" for assignment in assignments):
            self._json(400, {
                "message": "zone zone-retired is not available",
                "messageId": "fixture.zone.unavailable",
                "statusCode": 400,
            })
            return
        self._json(202, {
            "progress": 0,
            "status": "INPROGRESS",
            "id": "fixture-request",
            "selfLink": "/iaas/api/request-tracker/fixture-request",
        })

    def _json(self, status: int, value: dict) -> None:
        data = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    contract = load_contract(args.contract)
    args.log.unlink(missing_ok=True)
    server = ContractServer(("127.0.0.1", 0), Handler, contract, args.log)
    print(server.server_address[1], flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
