"""Loopback SDDC Manager mock pinned to docs/contract.json.

The mock derives every callable route from the operations named in the
contract and rejects anything else.  Request bodies are checked against the
contract schemas: an unknown member, a response-only member, a missing
required member and a member sent as null / empty are all refused.  Every
dispatched API request is appended to a JSONL log that the verifier reads.
"""

import json
import os
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_JSON = "application/json"


class ContractError(Exception):
    def __init__(self, status, error_code, message):
        super().__init__(message)
        self.status = status
        self.error_code = error_code
        self.message = message


def _load_contract(contract_path):
    with open(contract_path, encoding="utf-8") as handle:
        return json.load(handle)


def _route_pattern(path):
    pattern = ""
    for chunk in re.split(r"(\{[^{}]+\})", path):
        if chunk.startswith("{") and chunk.endswith("}"):
            pattern += "(?P<%s>[^/]+)" % chunk[1:-1]
        else:
            pattern += re.escape(chunk)
    return re.compile("^%s$" % pattern)


class _Schemas:
    def __init__(self, schemas):
        self._schemas = schemas

    def check(self, name, value, where):
        schema = self._schemas[name]
        if not isinstance(value, dict):
            raise ContractError(400, "INVALID_BODY",
                                "%s is not a %s object" % (where, name))
        properties = schema["properties"]
        for member in value:
            if member not in properties:
                raise ContractError(
                    400, "UNKNOWN_MEMBER",
                    "%s.%s is not a member of %s in this contract"
                    % (where, member, name))
            if properties[member].get("readOnly"):
                raise ContractError(
                    400, "READ_ONLY_MEMBER",
                    "%s.%s is response-only and cannot be sent"
                    % (where, member))
        for member in schema["required"]:
            if member not in value:
                raise ContractError(
                    400, "MISSING_REQUIRED_MEMBER",
                    "%s.%s is required by %s" % (where, member, name))
        for member, member_value in value.items():
            spec = properties[member]
            child = "%s.%s" % (where, member)
            if member_value is None or member_value == "" \
                    or member_value == [] or member_value == {}:
                raise ContractError(
                    400, "EMPTY_MEMBER",
                    "%s was sent empty; an unsupplied optional member is "
                    "omitted from the body" % child)
            kind = spec.get("type")
            if kind == "string" and not isinstance(member_value, str):
                raise ContractError(400, "INVALID_MEMBER_TYPE",
                                    "%s is not a string" % child)
            if kind == "integer" and (isinstance(member_value, bool)
                                      or not isinstance(member_value, int)):
                raise ContractError(400, "INVALID_MEMBER_TYPE",
                                    "%s is not an integer" % child)
            if kind == "array":
                if not isinstance(member_value, list):
                    raise ContractError(400, "INVALID_MEMBER_TYPE",
                                        "%s is not an array" % child)
                item_schema = spec.get("items", {}).get("schema")
                if item_schema:
                    for index, item in enumerate(member_value):
                        self.check(item_schema, item,
                                   "%s[%d]" % (child, index))
            if kind == "object" and spec.get("schema"):
                self.check(spec["schema"], member_value, child)
            pattern = spec.get("pattern")
            if pattern and isinstance(member_value, str) \
                    and not re.match(pattern, member_value):
                raise ContractError(400, "INVALID_MEMBER_VALUE",
                                    "%s does not match the contract pattern"
                                    % child)


class CaseScript:
    """Deterministic server-side story for one onboarding run."""

    def __init__(self, token, network_pool_id, validation_id, task_id,
                 task_statuses, host_outcomes, task_error, task_name,
                 network_pool_name=None,
                 validation_execution_status="COMPLETED",
                 validation_result_status="SUCCEEDED",
                 reverse_subtasks=False,
                 add_case_decoy_subtask=False):
        self.token = token
        self.network_pool_id = network_pool_id
        self.validation_id = validation_id
        self.task_id = task_id
        self.task_statuses = list(task_statuses)
        self.host_outcomes = list(host_outcomes)
        self.task_errors = list(task_error) if isinstance(task_error, list) \
            else ([task_error] if task_error else [])
        self.task_error = self.task_errors[0] if self.task_errors else None
        self.task_name = task_name
        self.network_pool_name = network_pool_name
        self.validation_execution_status = validation_execution_status
        self.validation_result_status = validation_result_status
        self.reverse_subtasks = reverse_subtasks
        self.add_case_decoy_subtask = add_case_decoy_subtask


class _State:
    def __init__(self, script):
        self.script = script
        self.created_pool = None
        self.commissioned = None
        self.task_polls = 0
        self.lock = threading.Lock()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "SddcManagerContractMock/1.0"

    def log_message(self, *_args):
        return

    # -- plumbing ---------------------------------------------------------
    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return ""
        return self.rfile.read(length).decode("utf-8")

    def _record(self, method, target, body):
        entry = {
            "seq": self.server.next_seq(),
            "method": method,
            "target": target,
            "path": target.split("?", 1)[0],
            "query": target.split("?", 1)[1] if "?" in target else "",
            "authorization": self.headers.get_all("Authorization") or [],
            "contentType": self.headers.get_all("Content-Type") or [],
            "accept": self.headers.get_all("Accept") or [],
            "userAgent": self.headers.get_all("User-Agent") or [],
            "body": body,
        }
        self.server.append_log(entry)

    def _send(self, status, payload):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", _JSON)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _error(self, status, error_code, message):
        self._send(status, {"errorCode": error_code, "errorType": "CONTRACT",
                            "message": message,
                            "referenceToken": self.server.reference_token})

    def do_GET(self):  # noqa: N802
        self._dispatch("GET")

    def do_POST(self):  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self):  # noqa: N802
        self._dispatch("PUT")

    def do_PATCH(self):  # noqa: N802
        self._dispatch("PATCH")

    def do_DELETE(self):  # noqa: N802
        self._dispatch("DELETE")

    # -- contract enforcement ---------------------------------------------
    def _dispatch(self, method):
        body = self._read_body()
        self._record(method, self.path, body)
        path = self.path.split("?", 1)[0]
        try:
            operation, params = self.server.resolve(method, path)
            if operation is None:
                raise ContractError(
                    404, "OPERATION_NOT_IN_CONTRACT",
                    "%s %s is not an operation of this contract"
                    % (method, path))
            self._require_token()
            payload = self._decode(operation, body)
            handler = getattr(self, "_op_" + operation["operationId"])
            status, response = handler(payload, params)
            self._send(status, response)
        except ContractError as failure:
            self._error(failure.status, failure.error_code, failure.message)

    def _require_token(self):
        headers = self.headers.get_all("Authorization") or []
        if len(headers) != 1 or \
                headers[0] != "Bearer %s" % self.server.script.token:
            raise ContractError(401, "UNAUTHORIZED",
                                "a single bearer token is required")

    def _decode(self, operation, body):
        request_body = operation["requestBody"]
        if not request_body:
            if body:
                raise ContractError(400, "UNEXPECTED_BODY",
                                    "%s takes no request body"
                                    % operation["operationId"])
            return None
        content_type = (self.headers.get("Content-Type") or "").split(";")[0]
        if content_type.strip().lower() != _JSON:
            raise ContractError(400, "UNSUPPORTED_MEDIA_TYPE",
                                "%s expects %s" % (operation["operationId"],
                                                   _JSON))
        try:
            payload = json.loads(body)
        except ValueError:
            raise ContractError(400, "INVALID_BODY",
                                "the request body is not JSON")
        schemas = self.server.schemas
        if request_body["isArray"]:
            if not isinstance(payload, list) or not payload:
                raise ContractError(400, "INVALID_BODY",
                                    "%s expects a non-empty array of %s"
                                    % (operation["operationId"],
                                       request_body["schema"]))
            for index, item in enumerate(payload):
                schemas.check(request_body["schema"], item,
                              "body[%d]" % index)
        else:
            schemas.check(request_body["schema"], payload, "body")
        return payload

    # -- operations -------------------------------------------------------
    def _op_createNetworkPool(self, payload, _params):  # noqa: N802
        state = self.server.state
        script = self.server.script
        with state.lock:
            if state.created_pool is not None:
                raise ContractError(400, "NETWORK_POOL_EXISTS",
                                    "a network pool named %s already exists"
                                    % state.created_pool["name"])
            networks = []
            for index, network in enumerate(payload["networks"]):
                echoed = dict(network)
                echoed["id"] = "%s-net-%d" % (script.network_pool_id, index)
                networks.append(echoed)
            state.created_pool = {
                "id": script.network_pool_id,
                "name": script.network_pool_name or payload["name"],
                "networks": networks,
                "hostsCount": 0,
            }
            return 201, state.created_pool

    def _op_validateHostCommissionSpec(self, payload, _params):  # noqa: N802
        state = self.server.state
        script = self.server.script
        with state.lock:
            self._require_pool(payload, state)
            checks = [
                {"description": "Host %s is reachable" % spec["fqdn"],
                 "severity": "INFO", "resultStatus": "SUCCEEDED"}
                for spec in payload
            ]
            return 202, {
                "id": script.validation_id,
                "description": "Host commission specification validation",
                "executionStatus": script.validation_execution_status,
                "resultStatus": script.validation_result_status,
                "validationChecks": checks,
            }

    def _op_commissionHosts(self, payload, _params):  # noqa: N802
        state = self.server.state
        script = self.server.script
        with state.lock:
            self._require_pool(payload, state)
            state.commissioned = [spec["fqdn"] for spec in payload]
            return 202, self._task(state, "IN_PROGRESS")

    def _op_getTask(self, _payload, params):  # noqa: N802
        state = self.server.state
        script = self.server.script
        with state.lock:
            if params.get("id") != script.task_id:
                raise ContractError(404, "TASK_NOT_FOUND",
                                    "task %s was not found"
                                    % params.get("id"))
            if state.commissioned is None:
                raise ContractError(404, "TASK_NOT_FOUND",
                                    "no commission task has been submitted")
            index = min(state.task_polls, len(script.task_statuses) - 1)
            state.task_polls += 1
            return 200, self._task(state, script.task_statuses[index])

    def _require_pool(self, payload, state):
        script = self.server.script
        for index, spec in enumerate(payload):
            if state.created_pool is None \
                    or spec["networkPoolId"] != script.network_pool_id:
                raise ContractError(
                    400, "NETWORK_POOL_NOT_FOUND",
                    "body[%d].networkPoolId %s does not identify a network "
                    "pool of this instance" % (index, spec["networkPoolId"]))

    def _task(self, state, status):
        script = self.server.script
        fqdns = state.commissioned or []
        normalized_status = re.sub(r"\s+", "_", status.strip()).upper()
        terminal = normalized_status not in ("PENDING", "IN_PROGRESS")
        outcomes = dict(zip(fqdns, script.host_outcomes))
        sub_tasks = []
        subtask_fqdns = list(reversed(fqdns)) if script.reverse_subtasks \
            else fqdns
        for fqdn in subtask_fqdns:
            sub_status = outcomes.get(fqdn, "FAILED") if terminal \
                else "IN_PROGRESS"
            normalized_sub_status = re.sub(
                r"\s+", "_", sub_status.strip()).upper()
            sub_task = {
                "name": "Commission host %s" % fqdn,
                "type": "HOST_COMMISSION",
                "description": "Commission ESXi host %s" % fqdn,
                "status": sub_status,
                "creationTimestamp": "2025-06-17T08:14:22.418Z",
                "resources": [{
                    "resourceId": self.server.resource_id(fqdn),
                    "fqdn": fqdn,
                    "type": "ESXI",
                    "name": fqdn,
                }],
            }
            if terminal:
                sub_task["completionTimestamp"] = "2025-06-17T08:39:05.771Z"
            if normalized_sub_status == "FAILED" and script.task_errors:
                sub_task["errors"] = [dict(item)
                                      for item in script.task_errors]
            sub_tasks.append(sub_task)
        if terminal and script.add_case_decoy_subtask and fqdns:
            decoy_fqdn = fqdns[0].upper()
            sub_tasks.insert(0, {
                "name": "Unrelated case-sensitive decoy",
                "type": "HOST_COMMISSION",
                "description": "A differently cased resource is not the host",
                "status": "FAILED",
                "creationTimestamp": "2025-06-17T08:14:22.418Z",
                "completionTimestamp": "2025-06-17T08:39:05.771Z",
                "resources": [{
                    "resourceId": self.server.resource_id(decoy_fqdn),
                    "fqdn": decoy_fqdn,
                    "type": "ESXI",
                    "name": decoy_fqdn,
                }],
            })
        task = {
            "id": script.task_id,
            "name": script.task_name,
            "type": "HOST_COMMISSION",
            "status": status,
            "creationTimestamp": "2025-06-17T08:14:22.104Z",
            "subTasks": sub_tasks,
            # Every host of the request is associated with the task itself,
            # whatever each host's own sub-task did.
            "resources": [{
                "resourceId": self.server.resource_id(fqdn),
                "fqdn": fqdn,
                "type": "ESXI",
                "name": fqdn,
            } for fqdn in fqdns],
            "isCancellable": not terminal,
            "isRetryable": terminal and normalized_status == "FAILED",
        }
        if terminal:
            task["completionTimestamp"] = "2025-06-17T08:39:05.883Z"
            task["resolutionStatus"] = \
                "UNRESOLVED" if normalized_status == "FAILED" else "RESOLVED"
        if terminal and normalized_status == "FAILED" \
                and script.task_errors:
            task["errors"] = [dict(item) for item in script.task_errors]
        return task


class ContractMockServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, contract_path, script, log_path):
        super().__init__(("127.0.0.1", 0), _Handler)
        contract = _load_contract(contract_path)
        self.schemas = _Schemas(contract["schemas"])
        self.routes = [(operation["method"], _route_pattern(operation["path"]),
                        operation) for operation in contract["operations"]]
        self.script = script
        self.state = _State(script)
        self.reference_token = uuid.uuid4().hex
        self._log_path = log_path
        self._log_lock = threading.Lock()
        self._seq = 0
        self._resource_ids = {}
        with open(self._log_path, "w", encoding="utf-8"):
            pass

    @property
    def base_uri(self):
        return "http://127.0.0.1:%d/" % self.server_address[1]

    def resolve(self, method, path):
        for route_method, pattern, operation in self.routes:
            match = pattern.match(path)
            if match:
                if route_method != method:
                    continue
                return operation, match.groupdict()
        return None, {}

    def resource_id(self, fqdn):
        if fqdn not in self._resource_ids:
            self._resource_ids[fqdn] = str(uuid.uuid4())
        return self._resource_ids[fqdn]

    def next_seq(self):
        with self._log_lock:
            self._seq += 1
            return self._seq

    def append_log(self, entry):
        with self._log_lock:
            with open(self._log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")
                handle.flush()
                os.fsync(handle.fileno())

    def serve_in_background(self):
        thread = threading.Thread(target=self.serve_forever,
                                  kwargs={"poll_interval": 0.05})
        thread.daemon = True
        thread.start()
        return thread


def read_log(log_path):
    entries = []
    with open(log_path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    entries.sort(key=lambda entry: entry["seq"])
    return entries
