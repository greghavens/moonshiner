"""Contract-pinned loopback mock for the VCF 9.1 SDDC LCM service.

The callable SDDC LCM routes are derived at start-up from the operations named
in docs/contract.json. Any other target is rejected. Every request is appended
to a JSONL log that the verifier reads to assert the exact wire shape.

The mock binds 127.0.0.1 on an ephemeral port and prints that port on stdout.
No live VMware endpoint is involved.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

STATE_LOCK = threading.Lock()


def load_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def build_routes(contract):
    """Turn each contracted operation path into an anchored regex."""
    base = contract["service"]["basePath"]
    routes = []
    for operation in contract["operations"]:
        pattern = "".join(
            f"(?P<{part[1:-1]}>[^/]+)" if part.startswith("{") else re.escape(part)
            for part in re.split(r"(\{[^}]+\})", base + operation["path"])
        )
        routes.append(
            {
                "operationId": operation["operationId"],
                "method": operation["method"],
                "regex": re.compile("^" + pattern + "$"),
                "parameters": operation["parameters"],
                "requestBody": operation["requestBody"],
            }
        )
    return routes


def schema_index(contract):
    return {schema["name"]: schema for schema in contract["schemas"]}


class Recorder:
    def __init__(self, path):
        self._path = path
        self._handle = open(path, "a", encoding="utf-8")

    def write(self, entry):
        with STATE_LOCK:
            self._handle.write(json.dumps(entry, sort_keys=True) + "\n")
            self._handle.flush()
            os.fsync(self._handle.fileno())


class Mock:
    """Deterministic SDDC LCM state machine driven by a runtime scenario."""

    def __init__(self, contract, scenario, recorder):
        self.contract = contract
        self.scenario = scenario
        self.recorder = recorder
        self.routes = build_routes(contract)
        self.schemas = schema_index(contract)
        self.base = contract["service"]["basePath"]
        self.bootstrap = {
            (route["method"], route["path"])
            for route in contract["sessionBootstrap"]["routes"]
        }
        self.token = scenario["accessToken"]
        self.sequence = 0
        # taskKey -> number of getTask polls already served
        self.polls = {}
        # taskId -> taskKey
        self.task_ids = {}
        for key, task_id in scenario["tasks"].items():
            self.task_ids[task_id] = key

    # ---------- validation helpers ----------

    def required_of(self, schema_name):
        return list(self.schemas[schema_name].get("required", []))

    def reject_nulls(self, node, trail=""):
        """The spec declares no nullable member, so an explicit null is a defect."""
        if isinstance(node, dict):
            for key, value in node.items():
                where = f"{trail}.{key}" if trail else key
                if value is None:
                    return f"member '{where}' was sent as null"
                found = self.reject_nulls(value, where)
                if found:
                    return found
        elif isinstance(node, list):
            for index, value in enumerate(node):
                found = self.reject_nulls(value, f"{trail}[{index}]")
                if found:
                    return found
        return None

    def check_required(self, body, schema_name, trail=""):
        for name in self.required_of(schema_name):
            if name not in body:
                where = f"{trail}.{name}" if trail else name
                return f"required member '{where}' of {schema_name} is missing"
        return None

    # ---------- scenario data ----------

    def component_by_id(self, component_id):
        for component in self.scenario["components"]:
            if component["id"] == component_id:
                return component
        return None

    def task_payload(self, key, status, component=None):
        info = self.scenario["taskShapes"][key]
        # Preserve the scenario's raw status on the wire while using its
        # normalized form to build a semantically consistent payload. The
        # verifier deliberately supplies padded, lower-case task statuses so
        # implementations must perform the normalization required by the task.
        normalized_status = "_".join(str(status).split()).upper()
        task = {
            "id": self.scenario["tasks"][key],
            "name": info["name"],
            "type": info["type"],
            "status": status,
            "resourceType": "COMPONENT",
            "createdBy": self.scenario["user"],
            "createTime": "2026-05-13T09:00:00.000Z",
            "startTime": "2026-05-13T09:00:01.000Z",
            "updateTime": "2026-05-13T09:00:02.000Z",
            "retriable": normalized_status == "FAILED",
            "cancellable": normalized_status in ("PENDING", "RUNNING"),
        }
        if component is not None:
            task["resourceId"] = component["id"]
        if normalized_status in ("SUCCEEDED", "FAILED", "CANCELED"):
            task["endTime"] = "2026-05-13T09:05:00.000Z"

        stages = []
        for index, stage_name in enumerate(info["stages"]):
            if normalized_status == "RUNNING":
                stage_status = "SUCCEEDED" if index == 0 else "RUNNING"
            elif normalized_status == "SUCCEEDED":
                stage_status = "SUCCEEDED"
            else:
                last = index == len(info["stages"]) - 1
                stage_status = "FAILED" if last else "SUCCEEDED"
            stage = {
                "id": f"{stage_name}-stage",
                "name": stage_name,
                "status": stage_status,
            }
            if stage_status == "FAILED":
                stage["messages"] = [
                    {
                        "level": "ERROR",
                        "stageId": f"{stage_name}-stage",
                        "timestamp": "2026-05-13T09:04:59.000Z",
                        "message": {
                            "id": info["errorMessageId"],
                            "defaultMessage": info["errorMessage"],
                            "localizedMessage": info["errorMessage"],
                        },
                    }
                ]
            stages.append(stage)
        task["stages"] = stages
        task["taskSummary"] = {"totalSubTasks": 0, "totalSteps": len(stages)}
        if normalized_status == "FAILED":
            task["messages"] = [
                {
                    "level": "ERROR",
                    "timestamp": "2026-05-13T09:05:00.000Z",
                    "message": {
                        "id": info["errorMessageId"],
                        "defaultMessage": info["errorMessage"],
                        "localizedMessage": info["errorMessage"],
                    },
                }
            ]
        return task

    def error_body(self, code, message):
        return {
            "code": code,
            "message": {
                "id": f"com.broadcom.lcm.mock.{code.lower()}",
                "defaultMessage": message,
                "localizedMessage": message,
            },
            "resolution": {
                "id": "com.broadcom.lcm.mock.resolution",
                "defaultMessage": "Correct the request and retry.",
                "localizedMessage": "Correct the request and retry.",
            },
            "referenceId": f"{self.scenario['referencePrefix']}-{code.lower()}",
            "timestamp": "2026-05-13T09:00:00.000Z",
        }

    # ---------- dispatch ----------

    def handle(self, method, target, headers, body_bytes):
        split = urlsplit(target)
        path = split.path
        query = parse_qs(split.query, keep_blank_values=True)

        if (method, path) in self.bootstrap:
            return self.handle_bootstrap(method, path)

        matched = None
        for route in self.routes:
            found = route["regex"].match(path)
            if found:
                if route["method"] != method:
                    return 405, self.error_body(
                        "METHOD_NOT_ALLOWED",
                        f"{method} is not contracted for {path}.",
                    )
                matched = (route, found.groupdict())
                break
        if matched is None:
            return 404, self.error_body(
                "OPERATION_NOT_CONTRACTED",
                f"{method} {path} is not one of the contracted operations "
                f"{', '.join(self.contract['operationIds'])}.",
            )

        route, path_params = matched

        auth = headers.get("Authorization")
        if auth != f"Bearer {self.token}":
            return 401, self.error_body(
                "UNAUTHORIZED",
                "Authorization must carry the bearer token of the caller-owned "
                "PowerCLI session.",
            )

        body = None
        if route["requestBody"]:
            if not body_bytes:
                return 400, self.error_body(
                    "MISSING_BODY",
                    f"{route['operationId']} requires a JSON request body.",
                )
            try:
                body = json.loads(body_bytes.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                return 400, self.error_body("MALFORMED_BODY", f"Body is not JSON: {exc}")
            if not isinstance(body, dict):
                return 400, self.error_body(
                    "MALFORMED_BODY", "Body must be a JSON object."
                )
            defect = self.reject_nulls(body)
            if defect:
                return 400, self.error_body("NULL_MEMBER", defect)

        # Unknown query parameters are not contracted.
        contracted_query = {
            param["name"] for param in route["parameters"] if param["in"] == "query"
        }
        for name in query:
            if name not in contracted_query:
                return 400, self.error_body(
                    "QUERY_NOT_CONTRACTED",
                    f"Query parameter '{name}' is not contracted for "
                    f"{route['operationId']}.",
                )
        for param in route["parameters"]:
            if param["in"] == "query" and param["required"] and param["name"] not in query:
                return 400, self.error_body(
                    "QUERY_REQUIRED",
                    f"Query parameter '{param['name']}' is required for "
                    f"{route['operationId']}.",
                )

        handler = {
            "setDepot": self.op_set_depot,
            "resolveDepotComponents": self.op_resolve,
            "performComponentAction": self.op_component_action,
            "getTask": self.op_get_task,
        }[route["operationId"]]
        return handler(body, path_params, query, headers)

    def handle_bootstrap(self, method, path):
        if path == "/v1/tokens":
            return 200, {
                "accessToken": self.token,
                "refreshToken": {"id": self.scenario["refreshTokenId"]},
            }
        if path == "/v1/tokens/refresh-token":
            return 204, {}
        return 200, {
            "id": self.scenario["applianceId"],
            "version": self.contract["source"]["apiVersion"],
        }

    def op_set_depot(self, body, path_params, query, headers):
        defect = self.check_required(body, "FleetDepotSpec")
        if defect:
            return 400, self.error_body("SPEC_INCOMPLETE", defect)
        expected = self.scenario["depot"]
        if body.get("fqdn") != expected["fqdn"]:
            return 400, self.error_body(
                "DEPOT_UNKNOWN", f"Depot '{body.get('fqdn')}' is not registered."
            )
        return 202, self.task_payload("depot", "RUNNING")

    def op_resolve(self, body, path_params, query, headers):
        defect = self.check_required(body, "DepotComponentsSpec")
        if defect:
            return 400, self.error_body("SPEC_INCOMPLETE", defect)
        defect = self.check_required(
            body.get("fleetDepotSpec") or {}, "FleetDepotSpec", "fleetDepotSpec"
        )
        if defect:
            return 400, self.error_body("SPEC_INCOMPLETE", defect)
        requested = body.get("componentVersions")
        if not isinstance(requested, list) or not requested:
            return 400, self.error_body(
                "SPEC_INCOMPLETE", "componentVersions must be a non-empty array."
            )
        for index, entry in enumerate(requested):
            if not isinstance(entry, dict):
                return 400, self.error_body(
                    "SPEC_INCOMPLETE", f"componentVersions[{index}] must be an object."
                )
            defect = self.check_required(
                entry, "ComponentVersionSpec", f"componentVersions[{index}]"
            )
            if defect:
                return 400, self.error_body("SPEC_INCOMPLETE", defect)
        # Answer in the scenario's deliberately shuffled order, decoys included.
        return 200, {"componentVersions": list(self.scenario["resolved"])}

    def op_component_action(self, body, path_params, query, headers):
        component_id = path_params["componentId"]
        component = self.component_by_id(component_id)
        if component is None:
            return 404, self.error_body(
                "COMPONENT_NOT_FOUND", f"No component with id '{component_id}'."
            )
        action = query["action"][0]
        allowed = next(
            param["enum"]
            for route in self.routes
            if route["operationId"] == "performComponentAction"
            for param in route["parameters"]
            if param["name"] == "action"
        )
        if action not in allowed:
            return 400, self.error_body(
                "ACTION_UNKNOWN", f"Action '{action}' is not in the contracted enum."
            )
        if action not in ("precheck", "apply"):
            return 400, self.error_body(
                "ACTION_UNSUPPORTED",
                f"This SDDC LCM deployment only accepts 'precheck' and 'apply'; "
                f"got '{action}'.",
            )

        defect = self.check_required(body, "ComponentUpgradeSpec")
        if defect:
            return 400, self.error_body("SPEC_INCOMPLETE", defect)
        component_spec = body.get("componentSpec") or {}
        defect = self.check_required(component_spec, "ComponentDesiredSpec", "componentSpec")
        if defect:
            return 400, self.error_body("SPEC_INCOMPLETE", defect)
        defect = self.check_required(
            component_spec.get("software") or {}, "SoftwareSpec", "componentSpec.software"
        )
        if defect:
            return 400, self.error_body("SPEC_INCOMPLETE", defect)
        defect = self.check_required(
            component_spec.get("depot") or {}, "DepotSpec", "componentSpec.depot"
        )
        if defect:
            return 400, self.error_body("SPEC_INCOMPLETE", defect)
        if "lcmPlatformSpec" in body:
            defect = self.check_required(
                body["lcmPlatformSpec"], "LcmPlatformSpec", "lcmPlatformSpec"
            )
            if defect:
                return 400, self.error_body("SPEC_INCOMPLETE", defect)

        if component_spec["software"].get("version") != component["targetVersion"]:
            return 400, self.error_body(
                "VERSION_MISMATCH",
                f"Component '{component['name']}' expects target version "
                f"{component['targetVersion']}.",
            )
        if component_spec["depot"].get("url") != component["binaryUrl"]:
            return 400, self.error_body(
                "BINARY_URL_MISMATCH",
                f"componentSpec.depot.url must be the resolved binary url for "
                f"'{component['name']}'.",
            )

        key = f"{action}:{component['name']}"
        if key not in self.scenario["tasks"]:
            return 400, self.error_body(
                "ACTION_NOT_SCHEDULED",
                f"The scenario does not schedule '{action}' for "
                f"'{component['name']}'.",
            )
        return 202, self.task_payload(key, "RUNNING", component)

    def op_get_task(self, body, path_params, query, headers):
        task_id = path_params["taskId"]
        key = self.task_ids.get(task_id)
        if key is None:
            return 404, self.error_body(
                "TASK_NOT_FOUND", f"No task with id '{task_id}'."
            )
        with STATE_LOCK:
            served = self.polls.get(key, 0)
            self.polls[key] = served + 1
        script = self.scenario["taskShapes"][key]["poll"]
        status = script[served] if served < len(script) else script[-1]
        component = None
        if ":" in key:
            name = key.split(":", 1)[1]
            component = next(
                (c for c in self.scenario["components"] if c["name"] == name), None
            )
        return 200, self.task_payload(key, status, component)


def make_handler(mock, recorder):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "SddcLcmContractMock/1.0"

        def log_message(self, *args):  # silence stderr chatter
            pass

        def _record(self, method, body_bytes, status):
            with STATE_LOCK:
                mock.sequence += 1
                sequence = mock.sequence
            recorder.write(
                {
                    "sequence": sequence,
                    "method": method,
                    "target": self.path,
                    "headers": [[name, value] for name, value in self.headers.items()],
                    "bodyBase64": base64.b64encode(body_bytes).decode("ascii"),
                    "status": status,
                }
            )

        def _dispatch(self, method):
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            body_bytes = self.rfile.read(length) if length else b""
            try:
                status, payload = mock.handle(
                    method, self.path, self.headers, body_bytes
                )
            except Exception as exc:  # pragma: no cover - defensive
                status = 500
                payload = mock.error_body("MOCK_FAILURE", repr(exc))
            self._record(method, body_bytes, status)
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_PUT(self):
            self._dispatch("PUT")

        def do_PATCH(self):
            self._dispatch("PATCH")

        def do_DELETE(self):
            self._dispatch("DELETE")

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--log", required=True)
    args = parser.parse_args()

    contract = load_json(args.contract)
    scenario = load_json(args.scenario)
    recorder = Recorder(args.log)
    mock = Mock(contract, scenario, recorder)

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(mock, recorder))
    sys.stdout.write(f"{server.server_address[1]}\n")
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
