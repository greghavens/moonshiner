"""Loopback SDDC Manager mock pinned to docs/contract.json.

Every callable route is derived from the operations that the contract names;
anything else is refused.  The one request body of this contract is checked
against the contract schemas: an unknown member, a member this API version
does not define, a member sent as null / empty / false, and a member of the
wrong type are all rejected.  Every request is appended to a JSONL log which
is flushed and fsynced so the verifier can read it.
"""

import json
import os
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_JSON = "application/json"
_NON_TERMINAL = ("PENDING", "IN_PROGRESS")


class ContractError(Exception):
    def __init__(self, status, error_code, message):
        super().__init__(message)
        self.status = status
        self.error_code = error_code
        self.message = message


def load_contract(contract_path):
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
    """Contract schema checker for request bodies."""

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
                    "%s.%s is not a member of %s in this API version"
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
            self._check_value(properties[member], member_value,
                              "%s.%s" % (where, member))

    def _check_value(self, spec, value, where):
        kind = spec.get("type")
        if value is None:
            raise ContractError(
                400, "NULL_MEMBER",
                "%s was sent null; a member that is not set is omitted from "
                "the body" % where)
        if kind == "boolean":
            if not isinstance(value, bool):
                raise ContractError(400, "INVALID_MEMBER_TYPE",
                                    "%s is not a boolean" % where)
            if value is False:
                raise ContractError(
                    400, "FALSE_MEMBER",
                    "%s was sent false; a boolean that is not set is omitted "
                    "from the body" % where)
            return
        if kind == "string":
            if not isinstance(value, str):
                raise ContractError(400, "INVALID_MEMBER_TYPE",
                                    "%s is not a string" % where)
            if not value.strip():
                raise ContractError(
                    400, "EMPTY_MEMBER",
                    "%s was sent empty; a member that is not set is omitted "
                    "from the body" % where)
            return
        if kind == "array":
            if not isinstance(value, list):
                raise ContractError(400, "INVALID_MEMBER_TYPE",
                                    "%s is not an array" % where)
            if not value:
                raise ContractError(
                    400, "EMPTY_MEMBER",
                    "%s was sent as an empty array; a member that is not set "
                    "is omitted from the body" % where)
            items = spec.get("items", {})
            for index, item in enumerate(value):
                self._check_value(items, item, "%s[%d]" % (where, index))
            return
        if kind == "object":
            if not isinstance(value, dict):
                raise ContractError(400, "INVALID_MEMBER_TYPE",
                                    "%s is not an object" % where)
            if not value:
                raise ContractError(
                    400, "EMPTY_MEMBER",
                    "%s was sent as an empty object; a member that is not "
                    "set is omitted from the body" % where)
            self.check(spec["schema"], value, where)
            return
        raise ContractError(400, "INVALID_BODY",
                            "%s has no checkable type in this contract"
                            % where)


class CaseScript:
    """Deterministic server-side story for one triage run.

    ``task`` and ``notifications`` are the documents this instance serves,
    ``bundle_statuses`` is the status sequence that getSupportBundleStatus
    walks (the last entry repeats).
    """

    def __init__(self, token, task, notifications, bundle_id,
                 bundle_statuses, bundle_name=None,
                 bundle_description="Support bundle collection"):
        self.token = token
        self.task = task
        self.notifications = list(notifications)
        self.bundle_id = bundle_id
        self.bundle_statuses = list(bundle_statuses)
        self.bundle_name = bundle_name
        self.bundle_description = bundle_description


class _State:
    def __init__(self):
        self.started_spec = None
        self.bundle_polls = 0
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
        if not isinstance(payload, dict) or not payload:
            raise ContractError(400, "INVALID_BODY",
                                "%s expects a non-empty %s object"
                                % (operation["operationId"],
                                   request_body["schema"]))
        self.server.schemas.check(request_body["schema"], payload, "body")
        return payload

    # -- operations -------------------------------------------------------
    def _op_getTask(self, _payload, params):  # noqa: N802
        script = self.server.script
        if params.get("id") != script.task["id"]:
            raise ContractError(404, "TASK_NOT_FOUND",
                                "task %s was not found" % params.get("id"))
        return 200, script.task

    def _op_getNotifications(self, _payload, _params):  # noqa: N802
        return 200, self.server.script.notifications

    def _op_startSupportBundle(self, payload, _params):  # noqa: N802
        state = self.server.state
        script = self.server.script
        with state.lock:
            if state.started_spec is not None:
                raise ContractError(
                    409, "SUPPORT_BUNDLE_IN_PROGRESS",
                    "a support bundle collection is already running")
            self._require_scope(payload)
            state.started_spec = payload
            return 202, self._bundle(script.bundle_statuses[0])

    def _op_getSupportBundleStatus(self, _payload, params):  # noqa: N802
        state = self.server.state
        script = self.server.script
        with state.lock:
            if state.started_spec is None:
                raise ContractError(
                    404, "SUPPORT_BUNDLE_NOT_FOUND",
                    "no support bundle collection has been started")
            if params.get("id") != script.bundle_id:
                raise ContractError(
                    404, "SUPPORT_BUNDLE_NOT_FOUND",
                    "support bundle %s was not found" % params.get("id"))
            index = min(state.bundle_polls, len(script.bundle_statuses) - 1)
            state.bundle_polls += 1
            return 200, self._bundle(script.bundle_statuses[index])

    def _require_scope(self, payload):
        scope = payload.get("scope")
        if not isinstance(scope, dict) or not scope.get("domains"):
            raise ContractError(
                400, "SUPPORT_BUNDLE_SCOPE_REQUIRED",
                "the collection must be scoped to the domain the failure "
                "happened in")
        if not payload.get("logs"):
            raise ContractError(
                400, "SUPPORT_BUNDLE_LOGS_REQUIRED",
                "the collection must name the logs it collects")

    def _bundle(self, status):
        script = self.server.script
        normalized_status = re.sub(r"\s+", "_", status.strip()).upper()
        bundle = {
            "id": script.bundle_id,
            "status": status,
            "description": script.bundle_description,
            "creationTimestamp": "2026-04-14T10:04:11.207Z",
        }
        if normalized_status not in _NON_TERMINAL:
            bundle["completionTimestamp"] = "2026-04-14T10:19:52.884Z"
            if normalized_status == "COMPLETED_WITH_SUCCESS":
                bundle["bundleAvailable"] = "true"
                if script.bundle_name:
                    bundle["bundleName"] = script.bundle_name
            else:
                bundle["bundleAvailable"] = "false"
        return bundle


class ContractMockServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, contract_path, script, log_path):
        super().__init__(("127.0.0.1", 0), _Handler)
        contract = load_contract(contract_path)
        self.schemas = _Schemas(contract["schemas"])
        self.routes = [(operation["method"], _route_pattern(operation["path"]),
                        operation) for operation in contract["operations"]]
        self.script = script
        self.state = _State()
        self.reference_token = uuid.uuid4().hex
        self._log_path = log_path
        self._log_lock = threading.Lock()
        self._seq = 0
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
