"""Loopback SDDC Manager stand-in, pinned to docs/contract.json.

The dispatch table is built from docs/contract.json at construction time. Only the
operations that contract names are served; every other path answers 404 and every
other method on a named path answers 405.

The server is deliberately permissive about the *shape* of a request body: it
records whatever bytes the client sent and applies only the validation the
contract says the real service applies. Asserting that unset optional properties
were omitted is the verifier's job, not this server's, so that the recorded body
stays available for an exact comparison.

Protected fixture. Do not edit.
"""

import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_DOCS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")
CONTRACT_PATH = os.path.join(_DOCS, "contract.json")

# --- Fixture identities -------------------------------------------------------

ADMIN_USERNAME = "administrator@vsphere.local"
ADMIN_PASSWORD = "Adm!n-Rig-4408"

CREDENTIAL_ID = "8b3f0a72-2f5e-4a4b-9d51-6c0a1f7e33d9"
SERVICE_USERNAME = "svc-vcf-automation@vsphere.local"
RESOURCE_ID = "d1c4b8e6-7a03-4f2c-8e19-55b7c9a2f640"
RESOURCE_NAME = "vc-mgmt-a.vrack.vsphere.local"
RESOURCE_IP = "10.0.0.43"
RESOURCE_TYPE = "VCENTER"
ACCOUNT_TYPE = "SERVICE"
CREDENTIAL_TYPE = "SSO"
DOMAIN_NAME = "sddc-mgmt-a"

INITIAL_PASSWORD = "V!nland-Prime-7712"
ROTATED_PASSWORD = "Sys-Gen-3Qv9!Kd2mNbX"

POLLS_UNTIL_TERMINAL = 3

_ALLOWED_OPERATION_TYPES = ("UPDATE", "ROTATE", "REMEDIATE")
_TS = "2025-06-18T08:48:39.000Z"


class RequestLog:
    """Ordered record of every request the server accepted.

    An entry is appended when the request *arrives*, so the ordering of the log
    is arrival ordering. The ``status`` field is filled in once the response has
    been decided.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._entries = []
        self._changed = threading.Condition(self._lock)

    def open_entry(self, **fields):
        with self._lock:
            entry = dict(fields)
            entry["seq"] = len(self._entries)
            entry["status"] = None
            self._entries.append(entry)
            self._changed.notify_all()
            return entry

    def close_entry(self, entry, status):
        with self._lock:
            entry["status"] = status
            self._changed.notify_all()

    def entries(self):
        with self._lock:
            return [dict(e) for e in self._entries]

    def by_operation(self, operation_id):
        return [e for e in self.entries() if e["operationId"] == operation_id]

    def index_of_first(self, operation_id):
        for e in self.entries():
            if e["operationId"] == operation_id:
                return e["seq"]
        return None

    def count(self, operation_id):
        return len(self.by_operation(operation_id))

    def wait_for(self, operation_id, timeout=10.0):
        """Block until at least one request for ``operation_id`` has arrived."""
        deadline = time.monotonic() + timeout
        with self._lock:
            while not any(e["operationId"] == operation_id for e in self._entries):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._changed.wait(remaining)
            return True


class _Route:
    def __init__(self, operation_id, method, path_template):
        self.operation_id = operation_id
        self.method = method
        self.path_template = path_template
        self.placeholders = re.findall(r"\{(\w+)\}", path_template)
        pattern = re.escape(path_template)
        for name in self.placeholders:
            pattern = pattern.replace(re.escape("{" + name + "}"), r"(?P<%s>[^/]+)" % name)
        self.regex = re.compile("^" + pattern + "$")


def _load_routes():
    with open(CONTRACT_PATH, "r", encoding="utf-8") as handle:
        contract = json.load(handle)
    routes = [
        _Route(op["operationId"], op["method"].upper(), op["path"])
        for op in contract["operations"]
    ]
    # Literal segments must win over placeholders: /v1/credentials/tasks/{id}
    # has to be tried before /v1/credentials/{id}.
    routes.sort(key=lambda r: (len(r.placeholders), -r.path_template.count("/")))
    return contract, routes


class MockSddcManager:
    """Contract-pinned SDDC Manager stand-in bound to 127.0.0.1."""

    def __init__(self, fail_rotation=False, polls_until_terminal=POLLS_UNTIL_TERMINAL):
        self.contract, self.routes = _load_routes()
        self.log = RequestLog()
        self.fail_rotation = fail_rotation
        self.polls_until_terminal = polls_until_terminal

        self._lock = threading.Lock()
        self._tokens = set()
        self._current_password = INITIAL_PASSWORD
        self._retired_passwords = set()
        self._modification_timestamp = _TS
        self._task = None
        self._task_seq = 0
        self._httpd = None
        self._thread = None
        self.base_url = None

    # -- lifecycle ------------------------------------------------------------

    def start(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(self))
        server.daemon_threads = True
        self._httpd = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()
        self.base_url = "http://127.0.0.1:%d" % server.server_address[1]
        return self.base_url

    def stop(self):
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
        return False

    # -- state inspection -----------------------------------------------------

    def current_password(self):
        with self._lock:
            return self._current_password

    def rotation_completed(self):
        with self._lock:
            return self._task is not None and self._task["status"] == "SUCCESSFUL"

    def classify_secret(self, username, password):
        with self._lock:
            if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
                return "admin"
            if username == SERVICE_USERNAME:
                if password == self._current_password:
                    return "service-current"
                if password in self._retired_passwords:
                    return "service-retired"
            return "unknown"

    # -- operation handlers ---------------------------------------------------

    def _credential(self, include_password):
        with self._lock:
            password = self._current_password
            modified = self._modification_timestamp
        credential = {
            "id": CREDENTIAL_ID,
            "credentialType": CREDENTIAL_TYPE,
            "accountType": ACCOUNT_TYPE,
            "username": SERVICE_USERNAME,
            "creationTimestamp": _TS,
            "modificationTimestamp": modified,
            "resource": {
                "resourceId": RESOURCE_ID,
                "resourceName": RESOURCE_NAME,
                "resourceIp": RESOURCE_IP,
                "resourceType": RESOURCE_TYPE,
                "domainNames": [DOMAIN_NAME],
                "domainName": DOMAIN_NAME,
            },
        }
        if include_password:
            credential["password"] = password
        return credential

    def op_createToken(self, entry, query, params, body):
        if not isinstance(body, dict):
            return 400, _error("BAD_REQUEST", "Request body is not a JSON object")
        username = body.get("username")
        password = body.get("password")
        kind = self.classify_secret(username, password)
        entry["presented_secret"] = kind
        if kind == "unknown":
            return 401, _error("UNAUTHORIZED", "Invalid username or password")
        token = "tok-%s-%d" % (kind, entry["seq"])
        with self._lock:
            self._tokens.add(token)
        return 201, {"accessToken": token, "refreshToken": {"id": "rt-%d" % entry["seq"]}}

    def op_getCredentials(self, entry, query, params, body):
        def mismatched(name, actual):
            supplied = query.get(name)
            return supplied is not None and supplied != actual

        if (
            mismatched("resourceName", RESOURCE_NAME)
            or mismatched("resourceType", RESOURCE_TYPE)
            or mismatched("resourceIp", RESOURCE_IP)
            or mismatched("accountType", ACCOUNT_TYPE)
            or mismatched("domainName", DOMAIN_NAME)
        ):
            elements = []
        else:
            # The list projection never carries the secret; it is readable only
            # through getCredential.
            elements = [self._credential(include_password=False)]
        return 200, {
            "elements": elements,
            "pageMetadata": {
                "pageNumber": 0,
                "pageSize": len(elements),
                "totalElements": len(elements),
                "totalPages": 1,
            },
        }

    def op_getCredential(self, entry, query, params, body):
        if params.get("id") != CREDENTIAL_ID:
            return 404, _error("NOT_FOUND", "Credential not found")
        return 200, self._credential(include_password=True)

    def op_updateOrRotatePasswords(self, entry, query, params, body):
        if not isinstance(body, dict):
            return 400, _error("BAD_REQUEST", "Request body is not a JSON object")
        operation_type = body.get("operationType")
        elements = body.get("elements")
        if operation_type is None or elements is None:
            return 400, _error("BAD_REQUEST", "operationType and elements are required")
        if operation_type not in _ALLOWED_OPERATION_TYPES:
            return 400, _error("BAD_REQUEST", "Unsupported operationType: %r" % (operation_type,))
        if not isinstance(elements, list) or not elements:
            return 400, _error("BAD_REQUEST", "elements must be a non-empty array")
        for element in elements:
            if not isinstance(element, dict):
                return 400, _error("BAD_REQUEST", "elements entry is not an object")
            if "resourceType" not in element or "credentials" not in element:
                return 400, _error("BAD_REQUEST", "resourceType and credentials are required")
            credentials = element["credentials"]
            if not isinstance(credentials, list) or not credentials:
                return 400, _error("BAD_REQUEST", "credentials must be a non-empty array")
            for credential in credentials:
                if not isinstance(credential, dict) or "username" not in credential:
                    return 400, _error("BAD_REQUEST", "credentials entry requires username")

        with self._lock:
            self._task_seq += 1
            task_id = "credtask-%d" % self._task_seq
            self._task = {
                "id": task_id,
                "name": "Rotate passwords",
                "type": "CREDENTIALS_ROTATE",
                "status": "IN_PROGRESS",
                "creationTimestamp": _TS,
                "polls": 0,
            }
        return 202, {
            "id": task_id,
            "name": "Rotate passwords",
            "type": "CREDENTIALS_ROTATE",
            "status": "IN_PROGRESS",
            "creationTimestamp": _TS,
            "isCancellable": True,
            "isRetryable": True,
        }

    def op_getCredentialsTask(self, entry, query, params, body):
        with self._lock:
            task = self._task
            if task is None or params.get("id") != task["id"]:
                return 400, _error("BAD_REQUEST", "Unknown credentials task")
            if task["status"] == "IN_PROGRESS":
                task["polls"] += 1
                if task["polls"] >= self.polls_until_terminal:
                    if self.fail_rotation:
                        task["status"] = "FAILED"
                    else:
                        task["status"] = "SUCCESSFUL"
                        # The password changes on the service side exactly here.
                        self._retired_passwords.add(self._current_password)
                        self._current_password = ROTATED_PASSWORD
                        self._modification_timestamp = "2025-06-18T09:14:02.000Z"
            payload = {
                "id": task["id"],
                "name": task["name"],
                "type": task["type"],
                "status": task["status"],
                "creationTimestamp": task["creationTimestamp"],
                "isAutoRotate": False,
            }
            if task["status"] != "IN_PROGRESS":
                payload["completionTimestamp"] = "2025-06-18T09:14:02.000Z"
            if task["status"] == "FAILED":
                payload["errors"] = [
                    _error("ROTATION_FAILED", "Password rotation could not be applied")
                ]
        return 200, payload


def _error(code, message):
    return {
        "errorCode": code,
        "errorType": "VALIDATION" if code == "BAD_REQUEST" else "SERVER",
        "message": message,
    }


def _make_handler(mock):
    contract_by_id = {op["operationId"]: op for op in mock.contract["operations"]}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "MockSddcManager/1.0"

        def log_message(self, *args):
            pass

        def _respond(self, status, payload, entry=None):
            body = json.dumps(payload).encode("utf-8")
            if entry is not None:
                # Publish the decided status before the client can consume the
                # body and race the verifier's inspection of the request log.
                mock.log.close_entry(entry, status)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _handle(self, method):
            raw_path = self.path
            if "?" in raw_path:
                path, _, raw_query = raw_path.partition("?")
            else:
                path, raw_query = raw_path, ""

            matched_route = None
            path_params = {}
            path_known = False
            for route in mock.routes:
                match = route.regex.match(path)
                if match:
                    path_known = True
                    if route.method == method:
                        matched_route = route
                        path_params = match.groupdict()
                        break
            if matched_route is None:
                status = 405 if path_known else 404
                entry = mock.log.open_entry(
                    operationId="<unmatched>",
                    method=method,
                    path=path,
                    raw_query=raw_query,
                    query={},
                    body=None,
                    raw_body="",
                    headers=self._captured_headers(),
                    presented_secret=None,
                )
                self._respond(status, _error("NOT_FOUND", "No such operation"), entry)
                return

            length = int(self.headers.get("Content-Length") or 0)
            raw_body = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                parsed_body = json.loads(raw_body) if raw_body else None
            except ValueError:
                parsed_body = "<malformed>"

            query = {}
            for pair in raw_query.split("&"):
                if not pair:
                    continue
                key, _, value = pair.partition("=")
                from urllib.parse import unquote_plus

                query[unquote_plus(key)] = unquote_plus(value)

            entry = mock.log.open_entry(
                operationId=matched_route.operation_id,
                method=method,
                path=path,
                raw_query=raw_query,
                query=query,
                body=parsed_body,
                raw_body=raw_body,
                headers=self._captured_headers(),
                path_params=path_params,
                presented_secret=None,
            )

            spec = contract_by_id[matched_route.operation_id]
            if spec.get("authenticated"):
                authorization = self.headers.get("Authorization") or ""
                token = authorization[7:] if authorization.startswith("Bearer ") else None
                with mock._lock:
                    valid = token in mock._tokens
                if not valid:
                    self._respond(401, _error("UNAUTHORIZED", "Missing or invalid token"), entry)
                    return

            handler = getattr(mock, "op_" + matched_route.operation_id)
            status, payload = handler(entry, query, path_params, parsed_body)
            self._respond(status, payload, entry)

        def _captured_headers(self):
            keep = ("content-type", "accept", "authorization")
            captured = {}
            for name, value in self.headers.items():
                lowered = name.lower()
                if lowered in keep:
                    if lowered == "authorization":
                        value = "Bearer <token>" if value.startswith("Bearer ") else "<other>"
                    captured[lowered] = value
            return captured

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def do_PATCH(self):
            self._handle("PATCH")

        def do_PUT(self):
            self._handle("PUT")

        def do_DELETE(self):
            self._handle("DELETE")

    return Handler
