#!/usr/bin/env python3
"""
Loopback mock of the VMware Cloud Foundation 9.0 SDDC Manager REST API.

The mock is pinned to ../docs/contract.json: routes, path/query parameters and
request-body schemas are read from that file at start-up, so the mock can only
serve the operations the contract names. Anything else is a 404.

Every request is appended to runtime/requests.jsonl before a response is chosen,
so the log records rejected requests too.

Binds 127.0.0.1 on an ephemeral port and writes the port to runtime/port.
No external network access, no VMware endpoint is contacted.
"""
import json
import os
import re
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RUNTIME = os.path.join(HERE, "runtime")

CONTRACT = json.load(open(os.path.join(ROOT, "docs", "contract.json")))
FIXTURES = json.load(open(os.path.join(HERE, "fixtures.json")))

SCHEMAS = CONTRACT["requestSchemas"]
RULES = CONTRACT["wireRules"]

_log_lock = threading.Lock()
_seq = [0]

# ---------------------------------------------------------------- routing ---


def _compile(path_template):
    pattern = re.sub(r"\{([A-Za-z0-9_]+)\}", r"(?P<\1>[^/]+)", path_template)
    return re.compile("^" + pattern + "$")


ROUTES = []
for op in CONTRACT["operations"]:
    ROUTES.append({
        "operationId": op["operationId"],
        "method": op["method"],
        "path": op["path"],
        "regex": _compile(op["path"]),
        "query": {q["name"]: q for q in op["queryParameters"]},
        "requestBody": op["requestBody"],
        "successStatus": op["successStatus"],
        "requiresAuth": op["requiresAuth"],
    })


def match_route(method, path):
    """Return (route, path_params, path_matched_any_method)."""
    path_seen = False
    for r in ROUTES:
        m = r["regex"].match(path)
        if not m:
            continue
        path_seen = True
        if r["method"] == method:
            return r, m.groupdict(), True
    return None, {}, path_seen


# ------------------------------------------------------------- validation ---


def type_ok(value, spec):
    t = spec.get("type")
    if t == "object":
        return isinstance(value, dict)
    if t == "array":
        return isinstance(value, list)
    if t == "boolean":
        return isinstance(value, bool)
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "string":
        return isinstance(value, str)
    return True


def validate_body(value, schema_name, where, errs):
    schema = SCHEMAS.get(schema_name)
    if schema is None:
        return
    if not isinstance(value, dict):
        errs.append("%s: expected a JSON object for %s" % (where, schema_name))
        return

    props = schema["properties"]
    for key, val in value.items():
        loc = "%s.%s" % (where, key)
        if key not in props:
            errs.append(
                "%s: property '%s' does not exist on %s in VCF %s. Unknown properties are rejected."
                % (loc, key, schema_name, CONTRACT["derivedFrom"]["specInfoVersion"]))
            continue
        if val is None and RULES.get("rejectNullValues"):
            errs.append("%s: null is not a value. Omit the property instead." % loc)
            continue
        if isinstance(val, str) and val == "" and RULES.get("rejectEmptyStringProperties"):
            errs.append("%s: empty string is not a value. Omit the property instead." % loc)
            continue

        spec = props[key]
        if not type_ok(val, spec):
            errs.append("%s: expected type %s" % (loc, spec.get("type")))
            continue
        if spec.get("type") == "object" and spec.get("schema"):
            validate_body(val, spec["schema"], loc, errs)
        elif spec.get("type") == "array":
            item = spec.get("items", {})
            for i, entry in enumerate(val):
                iloc = "%s[%d]" % (loc, i)
                if entry is None and RULES.get("rejectNullValues"):
                    errs.append("%s: null is not a value." % iloc)
                    continue
                if item.get("schema"):
                    validate_body(entry, item["schema"], iloc, errs)
                elif not type_ok(entry, item):
                    errs.append("%s: expected type %s" % (iloc, item.get("type")))

    for req in schema.get("required", []):
        if req not in value:
            errs.append("%s: required property '%s' is missing" % (where, req))


def validate_query(route, raw_query, errs):
    parsed = parse_qs(raw_query, keep_blank_values=True)
    for name, values in parsed.items():
        if name not in route["query"]:
            errs.append(
                "query parameter '%s' is not declared for operation %s"
                % (name, route["operationId"]))
            continue
        if len(values) > 1 and RULES.get("rejectDuplicateQueryParameters"):
            errs.append("query parameter '%s' was supplied %d times" % (name, len(values)))
            continue
        value = values[0]
        if value == "" and RULES.get("rejectEmptyQueryParameterValues"):
            errs.append(
                "query parameter '%s' was sent with an empty value. Omit the parameter instead."
                % name)
            continue
        declared = route["query"][name]["type"]
        if declared == "integer":
            try:
                int(value)
            except ValueError:
                errs.append("query parameter '%s' must be an integer, got %r" % (name, value))
        elif declared == "boolean" and value not in ("true", "false"):
            errs.append("query parameter '%s' must be 'true' or 'false', got %r" % (name, value))
    return {k: v[0] for k, v in parsed.items() if len(v) == 1}


# ------------------------------------------------------------------ state ---


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.support_bundle_started = False
        self.support_bundle_polls = 0


STATE = State()


def error_body(code, message, remediation=None):
    body = {"errorCode": code, "errorType": "VALIDATION_ERROR", "message": message}
    if remediation:
        body["remediationMessage"] = remediation
    return body


# --------------------------------------------------------------- handlers ---


def op_create_token(body, _pp, _q):
    creds = FIXTURES["credentials"]
    if body.get("username") != creds["username"] or body.get("password") != creds["password"]:
        return 401, error_body("UNAUTHORIZED", "The supplied credentials are not valid.")
    return 201, {
        "accessToken": FIXTURES["accessToken"],
        "refreshToken": {"id": FIXTURES["refreshTokenId"]},
    }


def op_get_tasks(_body, _pp, query):
    tasks = list(FIXTURES["tasks"])
    if "taskStatus" in query:
        tasks = [t for t in tasks if t["status"] == query["taskStatus"]]
    if "taskType" in query:
        tasks = [t for t in tasks if t.get("type") == query["taskType"]]
    if "limit" in query:
        tasks = tasks[: int(query["limit"])]
    return 200, {
        "elements": tasks,
        "pageMetadata": {
            "pageNumber": 0,
            "pageSize": len(tasks),
            "totalElements": len(tasks),
            "totalPages": 1 if tasks else 0,
        },
    }


def _credentials_task_envelope(sub_tasks):
    ct = FIXTURES["credentialsTask"]
    return {
        "id": ct["id"],
        "name": ct["name"],
        "type": ct["type"],
        "status": ct["status"],
        "creationTimestamp": ct["creationTimestamp"],
        "completionTimestamp": ct["completionTimestamp"],
        "isAutoRotate": ct["isAutoRotate"],
        "errors": ct["errors"],
        "subTasks": sub_tasks,
    }


def op_get_credentials_task(_body, pp, _q):
    ct = FIXTURES["credentialsTask"]
    if pp["id"] != ct["id"]:
        return 404, error_body("NOT_FOUND", "No credentials task with id %s." % pp["id"])
    # Subtask errors are only exposed through getCredentialsSubTask.
    return 200, _credentials_task_envelope([dict(s) for s in ct["subTasks"]])


def op_get_credentials_sub_task(_body, pp, _q):
    ct = FIXTURES["credentialsTask"]
    if pp["id"] != ct["id"]:
        return 404, error_body("NOT_FOUND", "No credentials task with id %s." % pp["id"])
    for sub in ct["subTasks"]:
        if sub["id"] == pp["subtaskId"]:
            detail = dict(sub)
            errs = FIXTURES["_subTaskErrors"].get(sub["id"])
            if errs:
                detail["errors"] = errs
            return 200, _credentials_task_envelope([detail])
    return 404, error_body("NOT_FOUND", "No subtask with id %s." % pp["subtaskId"])


def op_get_notifications(_body, _pp, _q):
    return 200, FIXTURES["notifications"]


def op_start_support_bundle(_body, _pp, _q):
    sb = FIXTURES["supportBundle"]
    with STATE.lock:
        STATE.support_bundle_started = True
        STATE.support_bundle_polls = 0
    return 202, {
        "id": sb["id"],
        "status": "IN_PROGRESS",
        "description": sb["description"],
        "creationTimestamp": sb["creationTimestamp"],
    }


def op_get_support_bundle_status(_body, pp, _q):
    sb = FIXTURES["supportBundle"]
    with STATE.lock:
        if not STATE.support_bundle_started:
            return 404, error_body("NOT_FOUND", "No support bundle task has been started.")
        if pp["id"] != sb["id"]:
            return 404, error_body("NOT_FOUND", "No support bundle with id %s." % pp["id"])
        STATE.support_bundle_polls += 1
        polls = STATE.support_bundle_polls
    if polls <= sb["inProgressPolls"]:
        return 200, {
            "id": sb["id"],
            "status": "IN_PROGRESS",
            "description": sb["description"],
            "creationTimestamp": sb["creationTimestamp"],
        }
    return 200, {
        "id": sb["id"],
        "status": "SUCCESSFUL",
        "description": sb["description"],
        "creationTimestamp": sb["creationTimestamp"],
        "completionTimestamp": sb["completionTimestamp"],
        "bundleName": sb["bundleName"],
        "bundleAvailable": "true",
    }


HANDLERS = {
    "createToken": op_create_token,
    "getTasks": op_get_tasks,
    "getCredentialsTask": op_get_credentials_task,
    "getCredentialsSubTask": op_get_credentials_sub_task,
    "getNotifications": op_get_notifications,
    "startSupportBundle": op_start_support_bundle,
    "getSupportBundleStatus": op_get_support_bundle_status,
}


# ---------------------------------------------------------------- server ----


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "MockSddcManager/9.0.0.0"

    def log_message(self, *_args):
        pass

    def _respond(self, status, payload, record):
        raw = json.dumps(payload).encode("utf-8")
        record["responseStatus"] = status
        self._record(record)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _record(self, record):
        with _log_lock:
            _seq[0] += 1
            record["seq"] = _seq[0]
            ordered = {k: record[k] for k in (
                "seq", "operationId", "method", "path", "rawQuery", "query",
                "contentType", "authorization", "bodyRaw", "body",
                "violations", "responseStatus") if k in record}
            with open(os.path.join(RUNTIME, "requests.jsonl"), "a") as fh:
                fh.write(json.dumps(ordered) + "\n")

    def _handle(self, method):
        parsed = urlparse(self.path)
        path = parsed.path
        raw_query = parsed.query

        length = int(self.headers.get("Content-Length") or 0)
        raw_body = self.rfile.read(length) if length else b""
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        authorization = self.headers.get("Authorization")

        record = {
            "operationId": None,
            "method": method,
            "path": path,
            "rawQuery": raw_query,
            "query": None,
            "contentType": content_type or None,
            "authorization": authorization,
            "bodyRaw": raw_body.decode("utf-8", "replace") if raw_body else None,
            "body": None,
            "violations": [],
        }

        route, path_params, path_seen = match_route(method, path)
        if route is None:
            record["violations"].append("no operation in the contract matches %s %s" % (method, path))
            status = 405 if path_seen else 404
            return self._respond(status, error_body(
                "CONTRACT_NO_SUCH_OPERATION",
                "The mock serves only the operations named in docs/contract.json. "
                "%s %s is not one of them." % (method, path)), record)

        record["operationId"] = route["operationId"]
        errs = record["violations"]

        # 1. authentication
        if route["requiresAuth"]:
            expected = "Bearer " + FIXTURES["accessToken"]
            if not authorization:
                errs.append("missing Authorization header")
            elif authorization != expected:
                errs.append("Authorization header must be exactly 'Bearer <accessToken>' "
                            "using the accessToken returned by createToken")
            if errs:
                return self._respond(401, error_body(
                    "UNAUTHENTICATED", "; ".join(errs)), record)

        # 2. query parameters
        query = validate_query(route, raw_query, errs)
        record["query"] = query
        if errs:
            return self._respond(400, error_body(
                "CONTRACT_QUERY_VIOLATION", "; ".join(errs)), record)

        # 3. request body
        body = None
        rb = route["requestBody"]
        if rb:
            if content_type != rb["contentType"]:
                errs.append("Content-Type must be %s, got %r" % (rb["contentType"], content_type))
                return self._respond(400, error_body(
                    "CONTRACT_BODY_VIOLATION", "; ".join(errs)), record)
            try:
                body = json.loads(raw_body.decode("utf-8")) if raw_body else None
            except (ValueError, UnicodeDecodeError) as exc:
                errs.append("request body is not valid JSON: %s" % exc)
                return self._respond(400, error_body(
                    "CONTRACT_BODY_VIOLATION", "; ".join(errs)), record)
            record["body"] = body
            if body is None and rb["required"]:
                errs.append("a request body is required")
            else:
                validate_body(body, rb["schema"], rb["schema"], errs)
            if errs:
                return self._respond(400, error_body(
                    "CONTRACT_BODY_VIOLATION", "; ".join(errs)), record)
        elif raw_body:
            errs.append("operation %s does not take a request body" % route["operationId"])
            return self._respond(400, error_body(
                "CONTRACT_BODY_VIOLATION", "; ".join(errs)), record)

        status, payload = HANDLERS[route["operationId"]](body or {}, path_params, query)
        return self._respond(status, payload, record)

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
    os.makedirs(RUNTIME, exist_ok=True)
    open(os.path.join(RUNTIME, "requests.jsonl"), "w").close()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    with open(os.path.join(RUNTIME, "port"), "w") as fh:
        fh.write(str(port))
    sys.stderr.write("mock sddc-manager listening on http://127.0.0.1:%d\n" % port)
    sys.stderr.flush()

    def shutdown(*_a):
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
