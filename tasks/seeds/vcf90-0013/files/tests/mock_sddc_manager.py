"""Loopback SDDC Manager mock pinned to docs/contract.json.

The mock derives every callable route from the operations named in the contract and
rejects anything else. Request bodies are validated against the contract schemas that
were derived from the VCF 9.0.0.0 specification, so a request that would be rejected by
the real appliance for shape reasons is rejected here too.

Every request is appended to a JSONL log that the verifier reads back.

This is a fixture, not a harness tool: it is an ordinary HTTP server bound to
127.0.0.1 on an ephemeral port. No live VMware endpoint is contacted.
"""

from __future__ import annotations

import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT_PATH = os.path.join(REPO_ROOT, "docs", "contract.json")

ACCESS_TOKEN = "mock-access-token-4d1f7b90c2a3"
REFRESH_TOKEN_ID = "0b4f2d0a-1d2e-4a3c-9f8d-5cbb61f0e001"
NETWORK_POOL_ID = "b1f3a5d2-4c7e-4a91-8b2d-6e0f1a2c3d40"
NETWORK_ID_PREFIX = "7a52c0e6-9b31-4f5d-88ac-1d0e4b6f00"
VALIDATION_ID = "5e2b9c33-71a4-4d0f-8c16-9ab3d7e2f051"
TASK_ID = "8d5a7c14-3b62-4f8e-a0d9-2e6b1f4c7a35"
RESOURCE_ID_PREFIX = "c47f2ab8-0e59-4d73-b6a1-3f8c92d5e1"
CREATED_AT = "2026-08-12T09:14:22.081Z"
FINISHED_AT = "2026-08-12T09:21:47.335Z"

# Number of getTask polls that report a non terminal status before the task settles.
IN_PROGRESS_POLLS = 1

TYPE_MAP = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


class ContractViolation(Exception):
    def __init__(self, status, error_code, message):
        super().__init__(message)
        self.status = status
        self.error_code = error_code
        self.message = message


def load_contract(path=CONTRACT_PATH):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _template_to_regex(path):
    pattern = ""
    for part in re.split(r"(\{[^}]+\})", path):
        if part.startswith("{") and part.endswith("}"):
            pattern += "(?P<%s>[^/]+)" % part[1:-1]
        else:
            pattern += re.escape(part)
    return re.compile("^" + pattern + "$")


class ContractValidator:
    """Validates request payloads against the contract's schema subset."""

    def __init__(self, contract):
        self.schemas = contract["schemas"]

    def validate(self, schema_name, value, where):
        schema = self.schemas[schema_name]
        if not isinstance(value, dict):
            raise ContractViolation(400, "BAD_REQUEST",
                                    f"{where} must be a JSON object for {schema_name}")
        props = schema["properties"]
        for key in value:
            if key not in props:
                raise ContractViolation(
                    400, "UNKNOWN_PROPERTY",
                    f"{where}.{key} is not a property of {schema_name} in the "
                    f"VCF 9.0.0.0 specification")
        for key in schema.get("serverAssigned", []):
            if key in value:
                raise ContractViolation(
                    400, "READ_ONLY_PROPERTY",
                    f"{where}.{key} is assigned by SDDC Manager and must not be sent")
        for key in schema["required"]:
            if key not in value:
                raise ContractViolation(
                    400, "MISSING_REQUIRED_PROPERTY",
                    f"{where}.{key} is required by {schema_name}")
        for key, item in value.items():
            self._validate_property(schema_name, key, props[key], item, f"{where}.{key}")

    def _validate_property(self, schema_name, key, decl, item, where):
        if item is None:
            raise ContractViolation(
                400, "NULL_PROPERTY",
                f"{where} is null; an unset optional property is omitted, not nulled")
        ref = decl.get("$ref")
        if ref:
            self.validate(ref, item, where)
            return
        declared = decl.get("type", "object")
        expected = TYPE_MAP[declared]
        if declared == "integer" and isinstance(item, bool):
            raise ContractViolation(400, "TYPE_MISMATCH", f"{where} must be an integer")
        if declared != "boolean" and isinstance(item, bool):
            raise ContractViolation(400, "TYPE_MISMATCH",
                                    f"{where} must be of type {declared}")
        if not isinstance(item, expected):
            raise ContractViolation(
                400, "TYPE_MISMATCH",
                f"{where} must be of type {declared}, got "
                f"{type(item).__name__}")
        if declared == "string" and not item.strip():
            raise ContractViolation(
                400, "BLANK_PROPERTY",
                f"{where} is blank; an unset optional property is omitted, not blanked")
        if declared == "array":
            if not item:
                raise ContractViolation(
                    400, "EMPTY_ARRAY",
                    f"{where} is empty; an unset optional array is omitted, not sent empty")
            item_ref = decl.get("items", {}).get("$ref")
            for index, element in enumerate(item):
                if item_ref:
                    self.validate(item_ref, element, f"{where}[{index}]")


class MockState:
    def __init__(self, reject_credentials=False):
        self.reject_credentials = reject_credentials
        self.issued_access_token = None
        self.network_pools = {}
        self.tasks = {}
        self.task_polls = {}
        self.seq = 0


def _error_body(error_code, message, remediation=None):
    body = {
        "errorCode": error_code,
        "errorType": "VALIDATION_FAILED" if error_code != "UNAUTHORIZED" else "UNAUTHORIZED",
        "message": message,
        "referenceToken": "MOCK-" + error_code,
    }
    if remediation:
        body["remediationMessage"] = remediation
    return body


def make_handler(contract, state, log_path):
    validator = ContractValidator(contract)
    routes = []
    for op_id, op in contract["operations"].items():
        routes.append((op["method"], _template_to_regex(op["path"]), op_id, op))
    log_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "SddcManagerMock/9.0.0.0"
        sys_version = ""

        def log_message(self, *args):  # silence stderr chatter
            pass

        # -- plumbing ---------------------------------------------------------
        def _record(self, status, operation_id, body_text, note=None):
            state.seq += 1
            header_pairs = [[k.lower(), v] for k, v in self.headers.items()]
            entry = {
                "seq": state.seq,
                "method": self.command,
                "target": self.path,
                "path": self.path.split("?", 1)[0],
                "query": self.path.split("?", 1)[1] if "?" in self.path else None,
                "operationId": operation_id,
                "status": status,
                "headers": {k: v for k, v in header_pairs},
                "headerPairs": header_pairs,
                "bodyText": body_text,
                "note": note,
            }
            try:
                entry["bodyJson"] = json.loads(body_text) if body_text else None
            except ValueError:
                entry["bodyJson"] = None
            with log_lock:
                with open(log_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry, sort_keys=True) + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())

        def _respond(self, status, payload):
            raw = json.dumps(payload).encode("utf-8") if payload is not None else b""
            self.send_response(status)
            if raw:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            if raw:
                self.wfile.write(raw)

        def _read_body(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return ""
            return self.rfile.read(length).decode("utf-8")

        def _handle(self):
            body_text = self._read_body()
            path = self.path.split("?", 1)[0]
            query = self.path.split("?", 1)[1] if "?" in self.path else None
            match = None
            for method, regex, op_id, op in routes:
                found = regex.match(path)
                if found and method == self.command:
                    match = (op_id, op, found.groupdict())
                    break
            if match is None:
                payload = _error_body(
                    "NOT_FOUND",
                    f"{self.command} {path} is not one of the contract operations: "
                    + ", ".join(sorted(contract["operations"])))
                self._record(404, None, body_text, note="off-contract request")
                self._respond(404, payload)
                return

            op_id, op, path_params = match
            try:
                payload, status = self._dispatch(op_id, op, path_params, query, body_text)
            except ContractViolation as exc:
                self._record(exc.status, op_id, body_text, note=exc.message)
                self._respond(exc.status, _error_body(exc.error_code, exc.message))
                return
            self._record(status, op_id, body_text)
            self._respond(status, payload)

        do_GET = _handle
        do_POST = _handle
        do_PUT = _handle
        do_PATCH = _handle
        do_DELETE = _handle

        # -- contract enforcement --------------------------------------------
        def _dispatch(self, op_id, op, path_params, query, body_text):
            if query is not None and not op["queryParameters"]:
                raise ContractViolation(
                    400, "UNEXPECTED_QUERY",
                    f"{op_id} declares no query parameters but the request sent '?{query}'")
            accept = self.headers.get("Accept") or ""
            if "application/json" not in accept and "*/*" not in accept:
                raise ContractViolation(
                    406, "NOT_ACCEPTABLE",
                    f"{op_id} responds with application/json; Accept was '{accept}'")
            if op_id not in contract["conventions"]["unauthenticatedOperations"]:
                auth = self.headers.get("Authorization") or ""
                if auth != "Bearer " + (state.issued_access_token or "\x00"):
                    raise ContractViolation(
                        401, "UNAUTHORIZED",
                        "Authorization must be 'Bearer <TokenPair.accessToken>' from "
                        "createToken")

            body_decl = op.get("requestBody")
            payload = None
            if body_decl is None:
                if body_text:
                    raise ContractViolation(400, "UNEXPECTED_BODY",
                                            f"{op_id} takes no request body")
            else:
                content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip()
                if content_type != "application/json":
                    raise ContractViolation(
                        415, "UNSUPPORTED_MEDIA_TYPE",
                        f"{op_id} requires Content-Type application/json, got "
                        f"'{content_type or 'none'}'")
                if not body_text:
                    raise ContractViolation(400, "MISSING_BODY",
                                            f"{op_id} requires a request body")
                try:
                    payload = json.loads(body_text)
                except ValueError as exc:
                    raise ContractViolation(400, "MALFORMED_JSON", str(exc))
                if body_decl["kind"] == "array":
                    if not isinstance(payload, list) or not payload:
                        raise ContractViolation(
                            400, "BAD_REQUEST",
                            f"{op_id} requires a non empty JSON array of "
                            f"{body_decl['itemSchema']}")
                    for index, element in enumerate(payload):
                        validator.validate(body_decl["itemSchema"], element, f"body[{index}]")
                else:
                    validator.validate(body_decl["schema"], payload, "body")

            handler = getattr(self, "_op_" + op_id)
            return handler(payload, path_params)

        # -- operations -------------------------------------------------------
        def _op_createToken(self, payload, _params):
            username = payload.get("username")
            password = payload.get("password")
            if not username or not password:
                raise ContractViolation(400, "BAD_REQUEST",
                                        "createToken requires username and password")
            if state.reject_credentials:
                raise ContractViolation(401, "UNAUTHORIZED",
                                        "The supplied credentials are not valid")
            state.issued_access_token = ACCESS_TOKEN
            return {"accessToken": ACCESS_TOKEN,
                    "refreshToken": {"id": REFRESH_TOKEN_ID}}, 201

        def _op_createNetworkPool(self, payload, _params):
            networks = []
            for index, network in enumerate(payload["networks"]):
                stored = dict(network)
                stored["id"] = NETWORK_ID_PREFIX + "%02d" % (index + 1)
                stored.setdefault("ipPools", [])
                networks.append(stored)
            pool = {"id": NETWORK_POOL_ID, "name": payload["name"],
                    "networks": networks, "hostsCount": 0}
            state.network_pools[pool["id"]] = pool
            return pool, 201

        def _op_addIpPoolToNetworkOfNetworkPool(self, payload, params):
            pool = state.network_pools.get(params["id"])
            if pool is None:
                raise ContractViolation(404, "NOT_FOUND",
                                        f"Network pool {params['id']} not found")
            network = next((n for n in pool["networks"] if n["id"] == params["networkId"]),
                           None)
            if network is None:
                raise ContractViolation(
                    404, "NOT_FOUND",
                    f"Network {params['networkId']} not found in pool {params['id']}")
            network["ipPools"].append({"start": payload["start"], "end": payload["end"]})
            return network, 200

        def _hosts_precheck(self, payload):
            for index, host in enumerate(payload):
                if host["networkPoolId"] not in state.network_pools:
                    raise ContractViolation(
                        400, "NETWORK_POOL_NOT_FOUND",
                        f"body[{index}].networkPoolId {host['networkPoolId']} does not "
                        "exist; create the network pool first")

        def _op_validateHostCommissionSpec(self, payload, _params):
            self._hosts_precheck(payload)
            checks = [{
                "description": "Host commission specification is well formed",
                "severity": "INFO",
                "resultStatus": "SUCCEEDED",
            }]
            for host in payload:
                if "sslThumbprint" not in host:
                    checks.append({
                        "description": ("SSL thumbprint was not supplied for "
                                        f"{host['fqdn']}; commissioning will verify the "
                                        "host certificate against SDDC Manager policy"),
                        "severity": "WARNING",
                        "resultStatus": "WARNING",
                        "acknowledge": True,
                    })
            return {
                "id": VALIDATION_ID,
                "description": "Validate host commission specifications",
                "executionStatus": "COMPLETED",
                "resultStatus": "SUCCEEDED",
                "validationChecks": checks,
            }, 202

        def _op_commissionHosts(self, payload, _params):
            self._hosts_precheck(payload)
            sub_tasks = []
            settled = False
            for index, host in enumerate(payload):
                base = {
                    "name": f"Commission host {host['fqdn']}",
                    "type": "HOST_COMMISSION",
                    "description": f"Commission ESXi host {host['fqdn']}",
                    "creationTimestamp": CREATED_AT,
                }
                # Every sub-task identifies its host through resources[0].fqdn. Only a
                # sub-task that succeeded carries an SDDC Manager assigned resourceId.
                base["resources"] = [{
                    "resourceId": host["fqdn"],
                    "fqdn": host["fqdn"],
                    "type": "ESXI",
                    "name": host["fqdn"],
                }]
                if settled:
                    base["status"] = "PENDING"
                elif "sslThumbprint" in host:
                    base["status"] = "SUCCESSFUL"
                    base["completionTimestamp"] = FINISHED_AT
                    base["resources"][0]["resourceId"] = (
                        RESOURCE_ID_PREFIX + "%02d" % (index + 1))
                else:
                    settled = True
                    base["status"] = "FAILED"
                    base["completionTimestamp"] = FINISHED_AT
                    base["errors"] = [_error_body(
                        "HOST_COMMISSION_SSL_THUMBPRINT_UNVERIFIED",
                        f"The SSL thumbprint of {host['fqdn']} could not be verified and "
                        "none was supplied in the commission specification",
                        "Supply sslThumbprint for the host and retry the task")]
                sub_tasks.append(base)
            failed = [s for s in sub_tasks if s["status"] == "FAILED"]
            task = {
                "id": TASK_ID,
                "name": "Commissioning Hosts",
                "type": "HOST_COMMISSION",
                "status": "IN_PROGRESS",
                "creationTimestamp": CREATED_AT,
                "subTasks": sub_tasks,
                "isCancellable": True,
                "isRetryable": False,
            }
            terminal = dict(task)
            terminal["status"] = "FAILED" if failed else "SUCCESSFUL"
            terminal["completionTimestamp"] = FINISHED_AT
            terminal["isCancellable"] = False
            terminal["isRetryable"] = True
            if failed:
                terminal["errors"] = [_error_body(
                    "HOST_COMMISSION_FAILED",
                    f"{len(failed)} of {len(sub_tasks)} hosts could not be commissioned",
                    "Inspect the sub-tasks, correct the hosts that failed and retry")]
            terminal["resources"] = [r for s in sub_tasks if s["status"] == "SUCCESSFUL"
                                     for r in s["resources"]]
            state.tasks[task["id"]] = {"pending": task, "terminal": terminal}
            state.task_polls[task["id"]] = 0
            return task, 202

        def _op_getTask(self, _payload, params):
            record = state.tasks.get(params["id"])
            if record is None:
                raise ContractViolation(404, "NOT_FOUND",
                                        f"Task {params['id']} not found")
            state.task_polls[params["id"]] += 1
            polls = state.task_polls[params["id"]]
            if polls <= IN_PROGRESS_POLLS:
                return record["pending"], 200
            return record["terminal"], 200

    return Handler


def start(log_path, reject_credentials=False, contract_path=CONTRACT_PATH):
    """Start the mock on an ephemeral loopback port.

    Returns (httpd, base_url, state). Call httpd.shutdown() when finished.
    """
    contract = load_contract(contract_path)
    state = MockState(reject_credentials=reject_credentials)
    handler = make_handler(contract, state, log_path)
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01},
                              daemon=True)
    thread.start()
    base_url = "http://127.0.0.1:%d" % httpd.server_address[1]
    return httpd, base_url, state


def read_log(log_path):
    if not os.path.exists(log_path):
        return []
    with open(log_path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


if __name__ == "__main__":  # manual smoke run: python3 tests/mock_sddc_manager.py
    import tempfile

    log = os.path.join(tempfile.mkdtemp(), "requests.jsonl")
    server, url, _ = start(log)
    print("mock listening on", url)
    print("request log:", log)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()
