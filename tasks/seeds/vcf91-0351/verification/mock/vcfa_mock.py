#!/usr/bin/env python3
import argparse
import hashlib
import json
import re
import secrets
import signal
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit


EXPECTED_OPERATIONS = [
    ("getDeployments", "GET", "/deployment/api/deployments"),
    ("patchDeployment", "PATCH", "/deployment/api/deployments/{deploymentId}"),
    ("getDeploymentResources", "GET", "/deployment/api/deployments/{deploymentId}/resources"),
    ("getResourceActions", "GET", "/deployment/api/deployments/{deploymentId}/resources/{resourceId}/actions"),
    ("submitResourceActionRequest", "POST", "/deployment/api/deployments/{deploymentId}/resources/{resourceId}/requests"),
]


def load_and_pin_contract(path):
    contract = json.loads(Path(path).read_text(encoding="utf-8"))
    actual = [
        (item["operationId"], item["method"], item["path"])
        for item in contract["operations"]
    ]
    if contract.get("productVersion") != "9.1" or actual != EXPECTED_OPERATIONS:
        raise SystemExit("contract operation set does not match the VCF Automation 9.1 mock")
    canonical = "\n".join("|".join(item) for item in actual)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if digest != contract["contractPin"]["operationSetSha256"]:
        raise SystemExit("contract operation pin mismatch")
    if contract.get("provenance", {}).get("sourceType") != "reference-documentation":
        raise SystemExit("contract provenance must identify reference documentation")
    return contract


class State:
    def __init__(self, log_path, action_outcome):
        suffix = secrets.token_hex(6)
        self.token = "token-" + secrets.token_urlsafe(18)
        self.deployment_name = "deployment " + suffix + "+blue"
        self.resource_name = "machine " + suffix + "&one"
        self.action_name = "Power Off " + suffix + "+safe"
        self.new_description = "updated by change " + suffix
        self.deployment_id = "dep/" + suffix + " +?%"
        self.resource_id = "resource/" + suffix + " &+#%"
        self.action_id = "action/" + suffix + " +safe"
        self.decoy_deployment_id = str(uuid.uuid4())
        self.decoy_resource_id = str(uuid.uuid4())
        self.decoy_action_id = str(uuid.uuid4())
        self.description = "original description " + suffix
        self.request_id = str(uuid.uuid4())
        self.action_outcome = action_outcome
        self.log_path = Path(log_path)
        self.lock = threading.Lock()
        self.sequence = 0

    def log(self, entry):
        with self.lock:
            self.sequence += 1
            entry["sequence"] = self.sequence
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, separators=(",", ":")) + "\n")


class Handler(BaseHTTPRequestHandler):
    server_version = "VcfaContractMock/9.1"

    def do_GET(self):
        self._dispatch()

    def do_PATCH(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()

    def log_message(self, _format, *_args):
        return

    def _read_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"_malformed": raw.decode("utf-8", errors="replace")}

    def _route(self, method, path):
        if method == "GET" and path == "/deployment/api/deployments":
            return "getDeployments", {}
        match = re.fullmatch(r"/deployment/api/deployments/([^/]+)", path)
        if method == "PATCH" and match:
            return "patchDeployment", {"deploymentId": unquote(match.group(1))}
        match = re.fullmatch(r"/deployment/api/deployments/([^/]+)/resources", path)
        if method == "GET" and match:
            return "getDeploymentResources", {"deploymentId": unquote(match.group(1))}
        match = re.fullmatch(r"/deployment/api/deployments/([^/]+)/resources/([^/]+)/actions", path)
        if method == "GET" and match:
            return "getResourceActions", {
                "deploymentId": unquote(match.group(1)),
                "resourceId": unquote(match.group(2)),
            }
        match = re.fullmatch(r"/deployment/api/deployments/([^/]+)/resources/([^/]+)/requests", path)
        if method == "POST" and match:
            return "submitResourceActionRequest", {
                "deploymentId": unquote(match.group(1)),
                "resourceId": unquote(match.group(2)),
            }
        return None, {}

    def _dispatch(self):
        state = self.server.state
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        operation_id, path_parameters = self._route(self.command, parsed.path)
        request_body = self._read_body()

        if operation_id is None:
            self._respond(None, path_parameters, query, request_body, 404,
                          {"details": "Operation is not named by the pinned contract"})
            return
        if self.headers.get("Authorization") != "Bearer " + state.token:
            self._respond(operation_id, path_parameters, query, request_body, 401,
                          {"details": "Unauthorized"})
            return
        if operation_id in {"patchDeployment", "submitResourceActionRequest"}:
            media_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if media_type != "application/json":
                self._respond(operation_id, path_parameters, query, request_body, 415,
                              {"details": "JSON requests require application/json"})
                return

        if operation_id == "getDeployments":
            content = []
            if query.get("name") == [state.deployment_name]:
                content.extend([
                    {
                        "id": state.decoy_deployment_id,
                        "name": "Copy of " + state.deployment_name,
                        "description": "decoy deployment",
                        "status": "CREATE_SUCCESSFUL",
                    },
                    {
                        "id": state.deployment_id,
                        "name": state.deployment_name,
                        "description": state.description,
                        "status": "CREATE_SUCCESSFUL",
                    },
                ])
            self._respond(operation_id, path_parameters, query, request_body, 200,
                          self._page(content))
            return

        if path_parameters.get("deploymentId") != state.deployment_id:
            self._respond(operation_id, path_parameters, query, request_body, 404,
                          {"details": "Deployment not found"})
            return

        if operation_id == "patchDeployment":
            if not isinstance(request_body, dict) or not isinstance(request_body.get("description"), str):
                self._respond(operation_id, path_parameters, query, request_body, 400,
                              {"details": "A string description is required"})
                return
            state.description = request_body["description"]
            payload = {
                "id": state.deployment_id,
                "name": state.deployment_name,
                "description": state.description,
                "status": "UPDATE_SUCCESSFUL",
            }
            self._respond(operation_id, path_parameters, query, request_body, 200, payload)
            return

        if operation_id == "getDeploymentResources":
            content = []
            if query.get("names") == [state.resource_name]:
                content.extend([
                    {
                        "id": state.decoy_resource_id,
                        "name": state.resource_name + " backup",
                        "state": "ON",
                        "type": "Cloud.vSphere.Machine",
                    },
                    {
                        "id": state.resource_id,
                        "name": state.resource_name,
                        "state": "ON",
                        "type": "Cloud.vSphere.Machine",
                    },
                ])
            self._respond(operation_id, path_parameters, query, request_body, 200,
                          self._page(content))
            return

        if path_parameters.get("resourceId") != state.resource_id:
            self._respond(operation_id, path_parameters, query, request_body, 404,
                          {"details": "Resource not found"})
            return

        if operation_id == "getResourceActions":
            payload = [
                {"id": state.decoy_action_id, "name": "Restart-" + state.action_name, "valid": True},
                {"id": state.action_id, "name": state.action_name, "valid": True},
            ]
            self._respond(operation_id, path_parameters, query, request_body, 200, payload)
            return

        if not isinstance(request_body, dict) or request_body.get("actionId") != state.action_id:
            self._respond(operation_id, path_parameters, query, request_body, 400,
                          {"details": "Requested action was not selected by exact name"})
            return
        if request_body.get("inputs") != {}:
            self._respond(operation_id, path_parameters, query, request_body, 400,
                          {"details": "inputs must be an empty object"})
            return
        payload = {
            "actionId": state.action_id,
            "deploymentId": state.deployment_id,
            "id": state.request_id,
            "name": state.action_name,
            "resourceIds": [state.resource_id],
            "totalTasks": 0,
        }
        if state.action_outcome == "success":
            payload["status"] = "SUCCEEDED"
            self._respond(operation_id, path_parameters, query, request_body, 200, payload)
        else:
            payload["details"] = "Resource action rejected because the resource is busy"
            payload["status"] = "FAILED"
            self._respond(operation_id, path_parameters, query, request_body, 409, payload)

    @staticmethod
    def _page(content):
        return {
            "content": content,
            "empty": not content,
            "number": 0,
            "numberOfElements": len(content),
            "size": 20,
            "totalElements": len(content),
            "totalPages": 1 if content else 0,
        }

    def _respond(self, operation_id, path_parameters, query, request_body, status, payload):
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        entry = {
            "operationId": operation_id,
            "method": self.command,
            "rawTarget": self.path,
            "path": urlsplit(self.path).path,
            "pathParameters": path_parameters,
            "query": query,
            "requestHeaders": {
                "authorizationPresent": self.headers.get("Authorization") is not None,
                "contentType": self.headers.get("Content-Type"),
            },
            "requestBody": request_body,
            "responseStatus": status,
            "responseBody": payload,
        }
        self.server.state.log(entry)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True)
    parser.add_argument("--ready-file", required=True)
    parser.add_argument("--log-file", required=True)
    parser.add_argument("--action-outcome", choices=("success", "conflict"), required=True)
    args = parser.parse_args()
    load_and_pin_contract(args.contract)
    state = State(args.log_file, args.action_outcome)
    Path(args.log_file).write_text("", encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.state = state
    host, port = server.server_address
    ready = {
        "baseUrl": f"http://{host}:{port}",
        "token": state.token,
        "deploymentName": state.deployment_name,
        "resourceName": state.resource_name,
        "newDescription": state.new_description,
        "actionName": state.action_name,
    }
    ready_path = Path(args.ready_file)
    temporary = ready_path.with_suffix(".tmp")
    temporary.write_text("\n".join(f"{key}={value}" for key, value in ready.items()) + "\n",
                         encoding="utf-8")
    temporary.replace(ready_path)

    def stop(_signum, _frame):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    server.serve_forever()
    server.server_close()


if __name__ == "__main__":
    main()
