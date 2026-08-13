"""Loopback mock of the VCF 9.0 SDDC Manager credential-rotation API.

The mock is pinned to ``docs/contract.json``: its routing table is built from
that file at start-up, so it serves exactly the operations the contract names
and nothing else. Any other path or method is rejected.

Token expiry is deterministic and counted in requests, not wall-clock seconds:
each access token authorizes exactly ``ACCESS_TOKEN_USES`` authorized calls and
the next one gets HTTP 401. The lifetime is never advertised to the client, so
401 is the only expiry signal - exactly as the real appliance behaves.

Every request is appended to a JSON Lines request log that tests can read.

Nothing here reaches the network beyond the loopback interface it binds to.
"""

from __future__ import annotations

import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
CONTRACT_PATH = os.path.join(REPO_ROOT, "docs", "contract.json")
FIXTURES_PATH = os.path.join(HERE, "fixtures.json")

#: Authorized calls permitted per access token before it expires.
ACCESS_TOKEN_USES = 2
#: getCredentialsTask reports IN_PROGRESS for this many polls, then SUCCESSFUL.
TASK_POLLS_IN_PROGRESS = 1

ACCESS_TOKENS = [
    "eyJhbGciOiJSUzI1NiJ9.sddc-manager-access-token-generation-{n}",
]
REFRESH_TOKEN_ID = "0a9f3d51-6c72-4b18-93ae-5d2f81c0e647"


def _load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


class ContractRouter:
    """Routing table built from docs/contract.json - the contract is the pin."""

    def __init__(self, contract):
        self.contract = contract
        self.routes = []  # (compiled_regex, path_template, {METHOD: operation})
        by_path = {}
        for op in contract["operations"]:
            by_path.setdefault(op["path"], {})[op["method"]] = op
        for template, methods in by_path.items():
            pattern = "^" + re.sub(
                r"\{([^/}]+)\}", lambda m: "(?P<%s>[^/]+)" % m.group(1),
                re.escape(template).replace(r"\{", "{").replace(r"\}", "}"),
            ) + "$"
            self.routes.append((re.compile(pattern), template, methods))

    def match(self, path, method):
        """Return (operation, path_params) or (None, reason)."""
        for rx, _template, methods in self.routes:
            m = rx.match(path)
            if not m:
                continue
            op = methods.get(method)
            if op is None:
                return None, ("method_not_allowed", sorted(methods))
            return op, m.groupdict()
        return None, ("not_found", None)


class MockState:
    """All mutable appliance state for one mock instance."""

    def __init__(self, contract, fixtures):
        self.lock = threading.Lock()
        self.contract = contract
        self.router = ContractRouter(contract)
        self.operator = fixtures["operator"]
        self.credentials = fixtures["credentials"]
        self.by_credential_id = {c["id"]: c for c in self.credentials}

        self.token_generation = 0
        self.access_tokens = {}  # token -> remaining authorized calls
        self.refresh_token_id = REFRESH_TOKEN_ID
        self.refresh_token_issued = False

        self.tasks = {}  # task id -> task record
        self.rotation_claimed = {}  # credential id -> task id
        self.seq = 0
        self.log = []

    # -- tokens ---------------------------------------------------------
    def mint_access_token(self):
        self.token_generation += 1
        token = ACCESS_TOKENS[0].format(n=self.token_generation)
        self.access_tokens = {token: ACCESS_TOKEN_USES}
        return token

    def spend_access_token(self, token):
        """Return None if the token is good (and spend one use), else a reason."""
        if token not in self.access_tokens:
            return "invalid"
        if self.access_tokens[token] <= 0:
            return "expired"
        self.access_tokens[token] -= 1
        return None


def _error(status, error_code, message, remediation=None):
    body = {
        "errorCode": error_code,
        "errorType": "VALIDATION_FAILED" if status == 400 else "INTERNAL_ERROR",
        "message": message,
    }
    if status == 401:
        body["errorType"] = "UNAUTHORIZED"
    elif status in (404, 405):
        body["errorType"] = "NOT_FOUND"
    if remediation:
        body["remediationMessage"] = remediation
    return status, body


def _walk_empty(node, path=""):
    """Yield dotted paths of null or empty-string values anywhere in a body."""
    if node is None or node == "":
        yield path or "<root>"
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _walk_empty(v, "%s.%s" % (path, k) if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk_empty(v, "%s[%d]" % (path, i))


class Handler(BaseHTTPRequestHandler):
    server_version = "VMware-SDDC-Manager-Mock/9.0.0.0"

    # BaseHTTPRequestHandler writes to stderr by default; stay quiet.
    def log_message(self, fmt, *args):
        pass

    # -- plumbing -------------------------------------------------------
    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _read_body(self):
        length = self.headers.get("Content-Length")
        if not length:
            return ""
        try:
            n = int(length)
        except ValueError:
            return ""
        return self.rfile.read(n).decode("utf-8", errors="replace")

    def _dispatch(self, method):
        state = self.server.state
        split = urlsplit(self.path)
        path, raw_query = split.path, split.query
        query = parse_qs(raw_query, keep_blank_values=True)
        raw_body = self._read_body()
        try:
            body = json.loads(raw_body) if raw_body else None
            body_is_json = raw_body != ""
        except ValueError:
            body, body_is_json = None, False

        with state.lock:
            state.seq += 1
            entry = {
                "seq": state.seq,
                "operationId": None,
                "method": method,
                "path": path,
                "rawQuery": raw_query,
                "query": query,
                "headers": {
                    "authorization": self.headers.get("Authorization"),
                    "content-type": self.headers.get("Content-Type"),
                    "accept": self.headers.get("Accept"),
                },
                "bodyRaw": raw_body,
                "bodyJson": body,
                "bodyIsJson": body_is_json,
                "status": None,
            }
            try:
                status, payload = self._handle(
                    state, method, path, query, raw_body, body, body_is_json, entry
                )
            except Exception as exc:  # pragma: no cover - defensive
                status, payload = _error(
                    500, "INTERNAL_ERROR", "mock failure: %r" % (exc,)
                )
            entry["status"] = status
            state.log.append(entry)
            self._append_log(entry)

        self._respond(status, payload)

    def _append_log(self, entry):
        log_path = self.server.log_path
        if not log_path:
            return
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")

    def _respond(self, status, payload):
        if payload is None:
            data = b""
        else:
            data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        if data:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data:
            self.wfile.write(data)

    # -- contract enforcement -------------------------------------------
    def _handle(self, state, method, path, query, raw_body, body, body_is_json, entry):
        op, extra = state.router.match(path, method)
        if op is None:
            reason, allowed = extra
            if reason == "method_not_allowed":
                return _error(
                    405, "METHOD_NOT_ALLOWED",
                    "%s is not defined for %s in the pinned contract; allowed: %s"
                    % (method, path, ", ".join(allowed)),
                )
            return _error(
                404, "PATH_NOT_FOUND",
                "%s is not an operation named by the pinned contract" % path,
            )

        op_id = op["operationId"]
        entry["operationId"] = op_id
        path_params = extra

        # Authorization is settled before the request is looked at, so an
        # expired token never does partial work.
        if op["requiresAuthorization"]:
            failure = self._authorize(state)
            if failure:
                return failure

        rb = op.get("requestBody")
        if rb and rb.get("required") and raw_body == "":
            return _error(
                400, "REQUEST_BODY_REQUIRED",
                "%s requires a %s request body" % (op_id, rb["contentType"]),
            )
        if raw_body != "" and not body_is_json:
            return _error(
                400, "MALFORMED_JSON",
                "%s request body is not valid JSON" % op_id,
            )

        # Unset optional fields must be omitted, never sent as null or "".
        if body is not None:
            empties = sorted(_walk_empty(body))
            if empties:
                return _error(
                    400, "EMPTY_FIELD_SENT",
                    "%s: unset optional fields must be omitted from the request "
                    "body, not sent null or empty; offending: %s"
                    % (op_id, ", ".join(empties)),
                    "Build the body from only the fields you actually set.",
                )

        # Same rule for the query string.
        declared = {p["name"] for p in op["queryParameters"]}
        for name, values in query.items():
            if name not in declared:
                return _error(
                    400, "UNKNOWN_QUERY_PARAMETER",
                    "%s does not declare query parameter %r" % (op_id, name),
                )
            if any(v == "" for v in values):
                return _error(
                    400, "EMPTY_QUERY_PARAMETER",
                    "%s: unset optional query parameter %r must be omitted, not "
                    "sent with an empty value" % (op_id, name),
                )

        return getattr(self, "_op_" + op_id)(state, query, body, path_params)

    def _authorize(self, state):
        header = self.headers.get("Authorization") or ""
        if not header.startswith("Bearer "):
            return _error(
                401, "UNAUTHENTICATED",
                "Missing or malformed Authorization header; expected "
                "'Bearer <accessToken>'",
            )
        token = header[len("Bearer "):].strip()
        reason = state.spend_access_token(token)
        if reason == "expired":
            return _error(
                401, "TOKEN_EXPIRED", "The access token has expired.",
                "Obtain a new access token using the refresh token, then retry "
                "the request.",
            )
        if reason == "invalid":
            return _error(
                401, "TOKEN_INVALID", "The access token is not recognised.",
                "Obtain a new access token using the refresh token.",
            )
        return None

    # -- operations ------------------------------------------------------
    def _op_createToken(self, state, query, body, path_params):
        if not isinstance(body, dict):
            return _error(
                400, "INVALID_SPEC",
                "createToken expects a TokenCreationSpec object",
            )
        allowed = {"username", "password", "apiKey", "idToken"}
        unknown = sorted(set(body) - allowed)
        if unknown:
            return _error(
                400, "INVALID_SPEC",
                "TokenCreationSpec has no such properties: %s" % ", ".join(unknown),
            )
        if "username" not in body or "password" not in body:
            return _error(
                400, "INVALID_SPEC",
                "This appliance authenticates with username and password.",
            )
        if (body["username"] != state.operator["username"]
                or body["password"] != state.operator["password"]):
            return _error(400, "BAD_CREDENTIALS", "Invalid username or password.")

        token = state.mint_access_token()
        state.refresh_token_issued = True
        return 201, {
            "accessToken": token,
            "refreshToken": {"id": state.refresh_token_id},
        }

    def _op_refreshAccessToken(self, state, query, body, path_params):
        # The contract types this body as a bare JSON string, not an object.
        if not isinstance(body, str):
            return _error(
                400, "INVALID_REFRESH_TOKEN",
                "The request body must be the refresh token id as a bare JSON "
                "string; received a %s." % type(body).__name__,
                "Send the id itself, for example \"abc-123\", not an object "
                "wrapping it.",
            )
        if not state.refresh_token_issued or body != state.refresh_token_id:
            return _error(404, "REFRESH_TOKEN_NOT_FOUND", "Refresh token not found.")
        # The response is a bare JSON string too.
        return 200, state.mint_access_token()

    def _op_getCredentials(self, state, query, body, path_params):
        def one(name):
            values = query.get(name)
            return values[0] if values else None

        page_number, page_size = one("pageNumber"), one("pageSize")
        for label, value in (("pageNumber", page_number), ("pageSize", page_size)):
            if value is not None and not value.isdigit():
                return _error(
                    400, "INVALID_PAGINATION",
                    "%s must be a non-negative number, got %r" % (label, value),
                )
        page_number = int(page_number) if page_number is not None else 0
        page_size = int(page_size) if page_size is not None else 0

        rows = state.credentials
        filters = {
            "resourceType": lambda c, v: c["resource"]["resourceType"] == v,
            "resourceName": lambda c, v: c["resource"]["resourceName"] == v,
            "resourceIp": lambda c, v: c["resource"].get("resourceIp") == v,
            "domainName": lambda c, v: v in c["resource"]["domainNames"],
            "accountType": lambda c, v: c["accountType"] == v,
        }
        for name, predicate in filters.items():
            value = one(name)
            if value is not None:
                rows = [c for c in rows if predicate(c, value)]

        total = len(rows)
        if page_size == 0:
            page, total_pages = rows, 1
        else:
            total_pages = max(1, -(-total // page_size))
            start = page_number * page_size
            page = rows[start:start + page_size]

        return 200, {
            "elements": [self._render_credential(c) for c in page],
            "pageMetadata": {
                "pageNumber": page_number,
                "pageSize": len(page),
                "totalElements": total,
                "totalPages": total_pages,
            },
        }

    @staticmethod
    def _render_credential(c):
        rendered = {
            "id": c["id"],
            "credentialType": c["credentialType"],
            "accountType": c["accountType"],
            "username": c["username"],
            "creationTimestamp": c["creationTimestamp"],
            "modificationTimestamp": c["modificationTimestamp"],
            "expiry": dict(c["expiry"]),
            "resource": dict(c["resource"]),
        }
        return rendered

    def _op_updateOrRotatePasswords(self, state, query, body, path_params):
        if not isinstance(body, dict):
            return _error(
                400, "INVALID_SPEC",
                "updateOrRotatePasswords expects a CredentialsUpdateSpec object",
            )
        allowed = {"operationType", "elements", "autoRotatePolicy"}
        unknown = sorted(set(body) - allowed)
        if unknown:
            return _error(
                400, "INVALID_SPEC",
                "CredentialsUpdateSpec has no such properties: %s"
                % ", ".join(unknown),
            )
        for required in ("operationType", "elements"):
            if required not in body:
                return _error(
                    400, "INVALID_SPEC",
                    "CredentialsUpdateSpec.%s is required" % required,
                )
        if body["operationType"] != "ROTATE":
            return _error(
                400, "UNSUPPORTED_OPERATION",
                "This appliance only accepts operationType ROTATE, got %r"
                % (body["operationType"],),
            )
        if not isinstance(body["elements"], list) or not body["elements"]:
            return _error(
                400, "INVALID_SPEC",
                "CredentialsUpdateSpec.elements must be a non-empty array",
            )

        targets = []
        for index, element in enumerate(body["elements"]):
            if not isinstance(element, dict):
                return _error(
                    400, "INVALID_SPEC", "elements[%d] must be an object" % index
                )
            allowed_el = {"resourceName", "resourceId", "resourceType", "credentials"}
            unknown = sorted(set(element) - allowed_el)
            if unknown:
                return _error(
                    400, "INVALID_SPEC",
                    "elements[%d]: ResourceCredentials has no such properties: %s"
                    % (index, ", ".join(unknown)),
                )
            for required in ("resourceType", "credentials"):
                if required not in element:
                    return _error(
                        400, "INVALID_SPEC",
                        "elements[%d].%s is required" % (index, required),
                    )
            if not isinstance(element["credentials"], list) or not element["credentials"]:
                return _error(
                    400, "INVALID_SPEC",
                    "elements[%d].credentials must be a non-empty array" % index,
                )
            if "resourceName" not in element and "resourceId" not in element:
                return _error(
                    400, "INVALID_SPEC",
                    "elements[%d] must identify the resource by resourceName or "
                    "resourceId" % index,
                )

            for j, cred in enumerate(element["credentials"]):
                where = "elements[%d].credentials[%d]" % (index, j)
                if not isinstance(cred, dict):
                    return _error(400, "INVALID_SPEC", "%s must be an object" % where)
                allowed_cred = {"credentialType", "accountType", "username", "password"}
                unknown = sorted(set(cred) - allowed_cred)
                if unknown:
                    return _error(
                        400, "INVALID_SPEC",
                        "%s: BaseCredential has no such properties: %s"
                        % (where, ", ".join(unknown)),
                    )
                if "username" not in cred:
                    return _error(
                        400, "INVALID_SPEC", "%s.username is required" % where
                    )
                if "password" in cred:
                    return _error(
                        400, "PASSWORD_NOT_ALLOWED",
                        "%s: password must be omitted for operationType ROTATE; "
                        "SDDC Manager generates the new password." % where,
                    )
                match = self._resolve(state, element, cred)
                if match is None:
                    return _error(
                        400, "CREDENTIAL_NOT_FOUND",
                        "%s does not match any managed credential" % where,
                    )
                targets.append(match)

        conflicts = sorted(
            {t["id"] for t in targets if t["id"] in state.rotation_claimed}
        )
        if conflicts:
            return _error(
                400, "ROTATION_ALREADY_IN_FLIGHT",
                "A rotation task already exists for credential(s): %s"
                % ", ".join(conflicts),
                "Poll the existing credentials task instead of submitting the "
                "rotation again.",
            )

        task_id = "cred-task-%04d" % (len(state.tasks) + 1)
        for target in targets:
            state.rotation_claimed[target["id"]] = task_id
        state.tasks[task_id] = {
            "id": task_id,
            "name": "Rotate passwords",
            "type": "CREDENTIALS_ROTATE",
            "status": "IN_PROGRESS",
            "creationTimestamp": "2026-08-12T04:31:18.000Z",
            "polls": 0,
            "credentialIds": [t["id"] for t in targets],
            "targets": targets,
        }
        return 202, {
            "id": task_id,
            "name": "Rotate passwords",
            "type": "CREDENTIALS_ROTATE",
            "status": "IN_PROGRESS",
            "creationTimestamp": "2026-08-12T04:31:18.000Z",
            "isCancellable": True,
            "isRetryable": False,
            "resources": [
                {"resourceId": t["resource"]["resourceId"],
                 "type": t["resource"]["resourceType"],
                 "name": t["resource"]["resourceName"]}
                for t in targets
            ],
        }

    @staticmethod
    def _resolve(state, element, cred):
        for candidate in state.credentials:
            resource = candidate["resource"]
            if resource["resourceType"] != element["resourceType"]:
                continue
            if ("resourceId" in element
                    and element["resourceId"] != resource["resourceId"]):
                continue
            if ("resourceName" in element
                    and element["resourceName"] != resource["resourceName"]):
                continue
            if candidate["username"] != cred["username"]:
                continue
            if ("credentialType" in cred
                    and candidate["credentialType"] != cred["credentialType"]):
                continue
            if ("accountType" in cred
                    and candidate["accountType"] != cred["accountType"]):
                continue
            return candidate
        return None

    def _op_getCredentialsTask(self, state, query, body, path_params):
        task = state.tasks.get(path_params.get("id"))
        if task is None:
            # The contract declares 200/400/500 for this operation.
            return _error(
                400, "TASK_NOT_FOUND",
                "No credentials task with id %r" % (path_params.get("id"),),
            )
        task["polls"] += 1
        done = task["polls"] > TASK_POLLS_IN_PROGRESS
        task["status"] = "SUCCESSFUL" if done else "IN_PROGRESS"

        rendered = {
            "id": task["id"],
            "name": task["name"],
            "type": task["type"],
            "status": task["status"],
            "creationTimestamp": task["creationTimestamp"],
            "isAutoRotate": False,
            "subTasks": [
                {
                    "id": "%s-sub-%02d" % (task["id"], i + 1),
                    "name": "Rotate password",
                    "description": "Rotate %s password for %s on %s" % (
                        t["credentialType"], t["username"],
                        t["resource"]["resourceName"],
                    ),
                    "resourceName": t["resource"]["resourceName"],
                    "entityType": t["resource"]["resourceType"],
                    "username": t["username"],
                    "credentialType": t["credentialType"],
                    "creationTimestamp": task["creationTimestamp"],
                    "status": "SUCCESSFUL" if done else "IN_PROGRESS",
                }
                for i, t in enumerate(task["targets"])
            ],
        }
        if done:
            rendered["completionTimestamp"] = "2026-08-12T04:33:02.000Z"
            for sub in rendered["subTasks"]:
                sub["completionTimestamp"] = "2026-08-12T04:33:02.000Z"
        return 200, rendered


class MockServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start_mock(host="127.0.0.1", port=0, log_path=None,
               contract_path=CONTRACT_PATH, fixtures_path=FIXTURES_PATH):
    """Start the mock on the loopback interface and return (server, base_url).

    Call ``server.shutdown()`` then ``server.server_close()`` when finished.
    """
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise ValueError("the mock only binds loopback, got %r" % (host,))

    contract = _load(contract_path)
    fixtures = _load(fixtures_path)
    server = MockServer((host, port), Handler)
    server.state = MockState(contract, fixtures)
    server.log_path = log_path
    if log_path:
        open(log_path, "w", encoding="utf-8").close()

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server.thread = thread
    return server, "http://%s:%d" % (host, server.server_address[1])


def stop_mock(server):
    server.shutdown()
    server.server_close()
    server.thread.join(timeout=5)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8931)
    parser.add_argument("--log", default=None, help="path for the JSONL request log")
    args = parser.parse_args()

    srv, base = start_mock(port=args.port, log_path=args.log)
    print("SDDC Manager mock listening on %s" % base)
    print("operations: %s" % ", ".join(
        op["operationId"] for op in srv.state.contract["operations"]))
    try:
        srv.thread.join()
    except KeyboardInterrupt:
        stop_mock(srv)
