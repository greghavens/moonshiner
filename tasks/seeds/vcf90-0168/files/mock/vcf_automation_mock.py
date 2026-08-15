#!/usr/bin/env python3
"""Loopback-only mock whose route set is loaded from docs/contract.json."""

import argparse
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


REQUIRED_OPERATIONS = {
    "updateProject": ("PATCH", "/iaas/api/projects/{id}", {200, 403}),
    "updateProjectZoneAssignments": (
        "PUT",
        "/iaas/api/projects/{id}/zones",
        {202, 404},
    ),
    "updateProjectResourceMetadata": (
        "PATCH",
        "/iaas/api/projects/{id}/resource-metadata",
        {200, 400},
    ),
}

SCENARIOS = {
    "final-failure",
    "full-success",
    "project-failure",
    "zone-failure",
    "null-collections",
    "empty-collections",
}


def load_routes(contract_path):
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    source = contract.get("source", {})
    if source.get("kind") != "reference-documentation" or source.get(
        "publishedSpecification"
    ) is not False:
        raise ValueError("contract must identify reference documentation, not a specification")

    operations = {item["id"]: item for item in contract["operations"]}
    if set(operations) != set(REQUIRED_OPERATIONS):
        raise ValueError("mock serves exactly the operations named by this seed contract")

    routes = []
    for operation_id, (method, path_template, scenario_statuses) in REQUIRED_OPERATIONS.items():
        operation = operations[operation_id]
        if (operation["method"], operation["pathTemplate"]) != (method, path_template):
            raise ValueError(f"contract route mismatch for {operation_id}")
        documented_statuses = {response["status"] for response in operation["responses"]}
        if not scenario_statuses <= documented_statuses:
            raise ValueError(f"a scenario status is not documented for {operation_id}")
        pattern = "^" + re.escape(path_template).replace(re.escape("{id}"), "[^/]+") + "$"
        routes.append((method, re.compile(pattern), operation_id))
    return routes


class ContractMock(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, routes, log_path, scenario):
        super().__init__(address, Handler)
        self.routes = routes
        self.log_path = log_path
        self.scenario = scenario
        self.sequence = 0


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_PATCH(self):
        self._handle()

    def do_PUT(self):
        self._handle()

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def do_DELETE(self):
        self._handle()

    def log_message(self, _format, *_args):
        pass

    def _handle(self):
        target = urlsplit(self.path)
        route = next(
            (
                operation_id
                for method, pattern, operation_id in self.server.routes
                if self.command == method and pattern.fullmatch(target.path)
            ),
            None,
        )
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.server.sequence += 1
        fields = [
            str(self.server.sequence),
            self.command,
            target.path,
            target.query,
            self.headers.get("Authorization", ""),
            self.headers.get("Content-Type", ""),
            self.headers.get("Accept", ""),
            base64.b64encode(body).decode("ascii"),
        ]
        with self.server.log_path.open("a", encoding="utf-8") as log:
            log.write("\t".join(fields) + "\n")

        if route is None:
            self._reply(404, {"message": "operation is not in the pinned contract"})
        elif route == "updateProject":
            if self.server.scenario == "project-failure":
                self._reply(
                    403,
                    {
                        "message": 'denied "by policy"\nπ',
                        "messageId": "project.denied/π",
                    },
                )
            else:
                self._reply(200, {"id": "project-id", "name": "updated"})
        elif route == "updateProjectZoneAssignments":
            if self.server.scenario == "zone-failure":
                self._reply(
                    404,
                    {
                        "message": "no eligible zones",
                        "messageId": "zones.not.found",
                    },
                )
            else:
                request_id = (
                    'zone-"quoted\nid'
                    if self.server.scenario == "full-success"
                    else "zone-request-42"
                )
                status = (
                    "INPROGRESS/✓"
                    if self.server.scenario == "full-success"
                    else "INPROGRESS"
                )
                self._reply(
                    202,
                    {
                        "progress": 0,
                        "message": "In Progress",
                        "status": status,
                        "id": request_id,
                        "selfLink": "/iaas/api/request-tracker/zone-request",
                    },
                )
        else:
            if self.server.scenario == "final-failure":
                self._reply(
                    400,
                    {
                        "message": "resource metadata backend rejected the update",
                        "messageId": "metadata.update.failed",
                        "statusCode": 400,
                        "errorCode": 9007,
                        "details": ["simulated downstream validation failure"],
                    },
                )
            else:
                self._reply(200, {"id": "project-id", "tags": []})

    def _reply(self, status, payload):
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--port-file", type=Path, required=True)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), required=True)
    args = parser.parse_args()

    routes = load_routes(args.contract)
    server = ContractMock(("127.0.0.1", 0), routes, args.log, args.scenario)
    args.port_file.write_text(str(server.server_port), encoding="ascii")
    server.serve_forever()


if __name__ == "__main__":
    main()
