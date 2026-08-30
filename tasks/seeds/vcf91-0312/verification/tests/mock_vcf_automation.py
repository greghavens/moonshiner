#!/usr/bin/env python3
"""Loopback mock of the VCF Automation 9.1 deployment API.

The route table is built from docs/contract.json, so the mock can only serve the
three operations that the pinned contract names. Every request that reaches the
server -- including rejected ones -- is appended to the JSONL request log, and
every state change the server actually applies is appended to the mutation log.

The mock never contacts a live VMware endpoint and binds only to 127.0.0.1.
"""

import argparse
import copy
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

ACCESS_TOKEN = "loopback-automation-access-token"

DEPLOYMENT_ID = "7f1c2b40-6a1e-4c8d-9f22-1b0e8d3a5c47"

RESOURCES = [
    {
        "id": "res-web-01",
        "name": "web-01",
        "description": "Front-end application node",
        "type": "Cloud.vSphere.Machine",
        "state": "OK",
        "origin": "DEPLOYED",
        "createdAt": "2026-07-02T09:14:31.552Z",
        "syncStatus": "SUCCESS",
    },
    {
        "id": "res-db-01",
        "name": "db-01",
        "description": "Primary database node",
        "type": "Cloud.vSphere.Machine",
        "state": "OK",
        "origin": "DEPLOYED",
        "createdAt": "2026-07-02T09:14:33.104Z",
        "syncStatus": "SUCCESS",
    },
]

RESOURCE_ACTIONS = {
    "res-web-01": [
        {
            "actionType": "RESOURCE_ACTION",
            "dependents": [],
            "description": "Power off the virtual machine.",
            "displayName": "Power Off",
            "formDefinition": {
                "formURI": "form/resource-action/Cloud.vSphere.Machine.PowerOff",
                "trackProgressInModal": True,
            },
            "id": "Cloud.vSphere.Machine.PowerOff",
            "name": "PowerOff",
            "orgId": "org-1a2b3c4d",
            "projectId": "prj-9f8e7d6c",
            "schema": {"type": "object", "properties": {}},
            "valid": True,
        },
        {
            "actionType": "RESOURCE_ACTION",
            "dependents": [],
            "description": "Create a snapshot of the virtual machine.",
            "displayName": "Create Snapshot",
            "formDefinition": {
                "formURI": "form/resource-action/Cloud.vSphere.Machine.Snapshot.Create",
                "trackProgressInModal": True,
            },
            "id": "Cloud.vSphere.Machine.Snapshot.Create",
            "name": "Snapshot.Create",
            "orgId": "org-1a2b3c4d",
            "projectId": "prj-9f8e7d6c",
            "schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "memory": {"type": "boolean"},
                },
            },
            "valid": True,
        },
        {
            "actionType": "RESOURCE_ACTION",
            "dependents": [],
            "description": "Change the CPU and memory allocation.",
            "displayName": "Resize",
            "formDefinition": {
                "formURI": "form/resource-action/Cloud.vSphere.Machine.Resize",
                "trackProgressInModal": True,
            },
            "id": "Cloud.vSphere.Machine.Resize",
            "name": "Resize",
            "orgId": "org-1a2b3c4d",
            "projectId": "prj-9f8e7d6c",
            "schema": {
                "type": "object",
                "properties": {
                    "cpuCount": {"type": "integer"},
                    "totalMemoryMB": {"type": "integer"},
                },
            },
            "valid": False,
        },
        {
            "actionType": "RESOURCE_ACTION",
            "dependents": [],
            "description": "Case-variant distractor used to verify exact action matching.",
            "displayName": "Reboot",
            "formDefinition": {
                "formURI": "form/resource-action/Cloud.vSphere.Machine.reboot",
                "trackProgressInModal": True,
            },
            "id": "Cloud.vSphere.Machine.reboot",
            "name": "reboot",
            "orgId": "org-1a2b3c4d",
            "projectId": "prj-9f8e7d6c",
            "schema": {"type": "object", "properties": {}},
            "valid": True,
        },
        {
            "actionType": "RESOURCE_ACTION",
            "dependents": [],
            "description": "Action whose response omits the optional validity member.",
            "displayName": "Restart",
            "formDefinition": {
                "formURI": "form/resource-action/Cloud.vSphere.Machine.Restart",
                "trackProgressInModal": True,
            },
            "id": "Cloud.vSphere.Machine.Restart",
            "name": "Restart",
            "orgId": "org-1a2b3c4d",
            "projectId": "prj-9f8e7d6c",
            "schema": {"type": "object", "properties": {}},
        },
    ],
    "res-db-01": [
        {
            "actionType": "RESOURCE_ACTION",
            "dependents": [],
            "description": "Power off the virtual machine.",
            "displayName": "Power Off",
            "formDefinition": {
                "formURI": "form/resource-action/Cloud.vSphere.Machine.PowerOff",
                "trackProgressInModal": True,
            },
            "id": "Cloud.vSphere.Machine.PowerOff",
            "name": "PowerOff",
            "orgId": "org-1a2b3c4d",
            "projectId": "prj-9f8e7d6c",
            "schema": {"type": "object", "properties": {}},
            "valid": False,
        }
    ],
}

REQUEST_IDS = {
    "Cloud.vSphere.Machine.PowerOff": "9a3e0001-0f4c-4a1b-8c77-2d5e6f701001",
    "Cloud.vSphere.Machine.Snapshot.Create": "9a3e0002-0f4c-4a1b-8c77-2d5e6f701002",
    "Cloud.vSphere.Machine.Resize": "9a3e0003-0f4c-4a1b-8c77-2d5e6f701003",
    "Cloud.vSphere.Machine.reboot": "9a3e0004-0f4c-4a1b-8c77-2d5e6f701004",
}


def load_routes(contract_path):
    with open(contract_path, "r", encoding="utf-8") as handle:
        contract = json.load(handle)
    routes = []
    for operation in contract["operations"]:
        template = operation["path"]
        pattern = "^" + re.sub(
            r"\{([A-Za-z0-9_]+)\}", lambda m: "(?P<%s>[^/]+)" % m.group(1), template
        ) + "$"
        routes.append(
            {
                "operation_id": operation["contract_operation_id"],
                "method": operation["method"].upper(),
                "template": template,
                "regex": re.compile(pattern),
            }
        )
    return routes


class MockState:
    def __init__(self, request_log_path, mutation_log_path, routes):
        self.lock = threading.Lock()
        self.request_log_path = request_log_path
        self.mutation_log_path = mutation_log_path
        self.routes = routes
        self.sequence = 0
        self.resources = copy.deepcopy(RESOURCES)

    def append(self, path, record):
        with open(path, "a", encoding="utf-8") as handle:
            # Member order is preserved so the verifier can assert the exact
            # order of the members the client put on the wire.
            handle.write(json.dumps(record) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def log_request(self, record):
        self.sequence += 1
        record["sequence"] = self.sequence
        self.append(self.request_log_path, record)

    def log_mutation(self, record):
        self.append(self.mutation_log_path, record)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "VcfAutomationLoopbackMock/1.0"
    sys_version = ""

    def log_message(self, *args):
        return

    # -- helpers ---------------------------------------------------------

    def _send(self, status, payload):
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        if body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _read_body(self):
        length = self.headers.get("Content-Length")
        if length is None:
            return b""
        try:
            count = int(length)
        except ValueError:
            return b""
        if count <= 0:
            return b""
        return self.rfile.read(count)

    def _match(self, method, path):
        for route in self.server.state.routes:
            if route["method"] != method:
                continue
            match = route["regex"].match(path)
            if match:
                return route, match.groupdict()
        return None, {}

    def _handle(self, method):
        state = self.server.state
        split = urlsplit(self.path)
        raw_body = self._read_body()
        try:
            body_text = raw_body.decode("utf-8")
        except UnicodeDecodeError:
            body_text = None
        parsed_json = None
        if body_text:
            try:
                parsed_json = json.loads(body_text)
            except ValueError:
                parsed_json = None

        headers = {}
        header_values = {}
        for name in ("authorization", "accept", "content-type", "content-length"):
            values = self.headers.get_all(name)
            if values:
                headers[name] = values[0]
                header_values[name] = values

        with state.lock:
            route, path_parameters = self._match(method, split.path)
            record = {
                "method": method,
                "target": self.path,
                "path": split.path,
                "query": split.query,
                "queryParameters": parse_qs(split.query, keep_blank_values=True),
                "headers": headers,
                "headerValues": header_values,
                "headerNames": sorted({k.lower() for k in self.headers.keys()}),
                "bodyText": body_text if body_text is not None else "",
                "json": parsed_json,
                "operationId": route["operation_id"] if route else None,
                "pathParameters": path_parameters,
            }
            state.log_request(record)

            if route is None:
                self._send(
                    404,
                    {
                        "message": "%s %s is not one of the operations named by the pinned contract."
                        % (method, split.path)
                    },
                )
                return

            if headers.get("authorization") != "Bearer " + ACCESS_TOKEN:
                self._send(401, {"message": "Unauthorized."})
                return

            handler = {
                "getDeploymentResources": self._get_deployment_resources,
                "getResourceActions": self._get_resource_actions,
                "submitResourceActionRequest": self._submit_resource_action_request,
            }[route["operation_id"]]
            handler(state, path_parameters, split, parsed_json)

    # -- operations ------------------------------------------------------

    def _get_deployment_resources(self, state, path_parameters, split, _body):
        if path_parameters.get("deploymentId") != DEPLOYMENT_ID:
            self._send(404, {"message": "Deployment not found."})
            return
        query = parse_qs(split.query, keep_blank_values=True)
        selected = state.resources
        if "names" in query:
            wanted = set()
            for value in query["names"]:
                wanted.update(part for part in value.split(",") if part)
            selected = [r for r in state.resources if r["name"] in wanted]
        content = [copy.deepcopy(resource) for resource in selected]
        self._send(
            200,
            {
                "content": content,
                "empty": len(content) == 0,
                "first": True,
                "last": True,
                "number": 0,
                "numberOfElements": len(content),
                "size": 20,
                "totalElements": len(content),
                "totalPages": 1 if content else 0,
                "pageable": {"pageNumber": 0, "pageSize": 20},
                "sort": {"sorted": True, "unsorted": False, "empty": False},
            },
        )

    def _get_resource_actions(self, state, path_parameters, _split, _body):
        resource_id = path_parameters.get("resourceId")
        if resource_id not in RESOURCE_ACTIONS:
            self._send(404, {"message": "Resource not found."})
            return
        self._send(200, copy.deepcopy(RESOURCE_ACTIONS[resource_id]))

    def _submit_resource_action_request(self, state, path_parameters, _split, body):
        resource_id = path_parameters.get("resourceId")
        if resource_id not in RESOURCE_ACTIONS:
            self._send(404, {"message": "Resource not found."})
            return
        if not isinstance(body, dict):
            self._send(400, {"message": "A JSON object body is required."})
            return
        action_id = body.get("actionId")
        action = next(
            (a for a in RESOURCE_ACTIONS[resource_id] if a["id"] == action_id), None
        )
        if action is None:
            self._send(404, {"message": "Action not found for this resource."})
            return
        if action.get("valid") is not True:
            self._send(
                409,
                {"message": "The action is not currently valid for this resource."},
            )
            return

        for resource in state.resources:
            if resource["id"] == resource_id:
                resource["state"] = "IN_PROGRESS"
        request_id = REQUEST_IDS[action_id]
        state.log_mutation(
            {
                "operationId": "submitResourceActionRequest",
                "resourceId": resource_id,
                "actionId": action_id,
                "requestId": request_id,
            }
        )
        response = {
            "actionId": action_id,
            "cancelable": True,
            "completedTasks": 0,
            "createdAt": "2026-08-11T12:00:00.000Z",
            "deploymentId": DEPLOYMENT_ID,
            "dismissed": False,
            "id": request_id,
            "name": action["displayName"],
            "requestedBy": "operator@vcf.local",
            "resourceIds": [resource_id],
            "status": "CREATED",
            "totalTasks": 1,
            "updatedAt": "2026-08-11T12:00:00.000Z",
        }
        if isinstance(body.get("inputs"), dict):
            response["inputs"] = body["inputs"]
        self._send(200, response)

    # -- verbs -----------------------------------------------------------

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_PATCH(self):
        self._handle("PATCH")

    def do_DELETE(self):
        self._handle("DELETE")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--mutations", required=True)
    parser.add_argument("--ready", required=True)
    arguments = parser.parse_args()

    for path in (arguments.log, arguments.mutations):
        open(path, "w", encoding="utf-8").close()

    routes = load_routes(arguments.contract)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.state = MockState(arguments.log, arguments.mutations, routes)

    with open(arguments.ready, "w", encoding="utf-8") as handle:
        handle.write(str(server.server_address[1]))
        handle.flush()
        os.fsync(handle.fileno())

    try:
        server.serve_forever(poll_interval=0.05)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
