"""Loopback mock of the VMware Cloud Foundation Operations suite-api.

The mock is pinned to ``docs/contract.json``: it builds its routing table from
the operations that contract names and serves nothing else.  Anything the
contract does not name is a 404, whatever the real appliance would do with it.

Every request is appended to a JSON-lines log so the verification suite can
assert the exact wire shape after the fact.

The mock listens on 127.0.0.1 only.  No VMware endpoint is contacted.

PROTECTED: do not modify.
"""

import hashlib
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

TOKEN_PREFIX = "OpsToken"
JSON_MEDIA_TYPE = "application/json"


# --------------------------------------------------------------------------
# Alert dataset
#
# 47 rows are served.  They are deliberately not in the order the client is
# required to emit, and one alert is served twice -- on page 0 and again on
# page 1 -- with a later ``status`` the second time, the way a real collection
# behaves when it shifts underneath a paged read.
# --------------------------------------------------------------------------

ALERT_LEVELS = ("CRITICAL", "IMMEDIATE", "WARNING", "INFORMATION", "NONE")
ALERT_STATUSES = ("NEW", "ACTIVE", "UPDATED")
CONTROL_STATES = ("OPEN", "ASSIGNED", "SUSPENDED")
BASE_START_TIME = 1753368185

DUPLICATE_SOURCE_INDEX = 9
DUPLICATE_REPEAT_INDEX = 17


def _stable_uuid(seed):
    """A deterministic, well-formed UUID string -- no randomness anywhere."""
    return str(uuid.UUID(hashlib.md5(seed.encode("utf-8")).hexdigest()))


RESOURCE_IDS = tuple(_stable_uuid("vcfops-resource-%d" % i) for i in range(4))


def _build_alert_rows():
    rows = []
    for index in range(47):
        alert_id = _stable_uuid("vcfops-alert-%d" % index)
        resource_id = RESOURCE_IDS[index % len(RESOURCE_IDS)]
        start_time = BASE_START_TIME - (index % 7) * 3600
        rows.append(
            {
                "alertId": alert_id,
                "resourceId": resource_id,
                "alertDefinitionId": "AlertDefinition-%d" % (index % 5),
                "alertDefinitionName": "Alert definition %d" % (index % 5),
                "alertImpact": "HEALTH" if index % 2 == 0 else "RISK",
                "alertLevel": ALERT_LEVELS[index % len(ALERT_LEVELS)],
                "status": ALERT_STATUSES[index % len(ALERT_STATUSES)],
                "controlState": CONTROL_STATES[index % len(CONTROL_STATES)],
                "startTimeUTC": start_time,
                "updateTimeUTC": start_time + 600,
                "cancelTimeUTC": 0,
                "suspendUntilTimeUTC": 0,
                "type": 16,
                "subType": 19,
                "links": [
                    {
                        "href": "/suite-api/api/alerts/%s" % alert_id,
                        "rel": "SELF",
                        "name": "linkToSelf",
                    }
                ],
            }
        )

    # The same alert served a second time, one page later, further along in its
    # life cycle.  A client that keeps the first row it saw reports "ACTIVE".
    repeat = json.loads(json.dumps(rows[DUPLICATE_SOURCE_INDEX]))
    rows[DUPLICATE_SOURCE_INDEX]["status"] = "ACTIVE"
    repeat["status"] = "UPDATED"
    repeat["updateTimeUTC"] = rows[DUPLICATE_SOURCE_INDEX]["updateTimeUTC"] + 1200
    rows[DUPLICATE_REPEAT_INDEX] = repeat
    return tuple(rows)


ALERT_ROWS = _build_alert_rows()
DUPLICATED_ALERT_ID = ALERT_ROWS[DUPLICATE_SOURCE_INDEX]["alertId"]
UNUSED_RESOURCE_ID = _stable_uuid("vcfops-resource-with-no-alerts")


# --------------------------------------------------------------------------
# Contract-driven routing
# --------------------------------------------------------------------------


class ContractError(Exception):
    """The contract under docs/ cannot be used to stand a service up."""


class MockService:
    """Request handling for the contracted operations, and nothing else."""

    def __init__(self, contract, alerts=ALERT_ROWS, log_path=None,
                 page_windows=None):
        self.contract = contract
        self.alerts = list(alerts)
        self.page_windows = (
            [list(window) for window in page_windows]
            if page_windows is not None else None
        )
        self.log_path = log_path
        self._lock = threading.Lock()
        self._seq = 0
        self._issued = 0
        self._active_tokens = set()
        self._routes = self._build_routes(contract)

    # -- routing ----------------------------------------------------------

    @staticmethod
    def _build_routes(contract):
        if not isinstance(contract, dict):
            raise ContractError("contract is not a JSON object")
        base_path = contract.get("basePath")
        if not isinstance(base_path, str) or not base_path.startswith("/"):
            raise ContractError("contract has no usable basePath")
        operations = contract.get("operations")
        if not isinstance(operations, dict) or not operations:
            raise ContractError("contract names no operations")

        routes = {}
        for operation_id, operation in operations.items():
            if not isinstance(operation, dict):
                raise ContractError("operation %r is not an object" % operation_id)
            method = operation.get("method")
            path = operation.get("path")
            if not isinstance(method, str) or not isinstance(path, str):
                raise ContractError("operation %r has no method/path" % operation_id)
            if "{" in path:
                raise ContractError(
                    "operation %r has a templated path this mock cannot serve"
                    % operation_id
                )
            routes[(method.upper(), base_path.rstrip("/") + path)] = operation_id
        return routes

    def operation(self, operation_id):
        return self.contract["operations"][operation_id]

    def declared_query_names(self, operation_id):
        names = []
        for parameter in self.operation(operation_id).get("queryParameters") or []:
            if isinstance(parameter, dict) and isinstance(parameter.get("name"), str):
                names.append(parameter["name"])
        return names

    def requires_authorization(self, operation_id):
        return bool(self.operation(operation_id).get("security"))

    def expects_body(self, operation_id):
        return bool(self.operation(operation_id).get("requestBody"))

    def body_schema(self, operation_id):
        body = self.operation(operation_id).get("requestBody") or {}
        name = body.get("schema")
        schemas = self.contract.get("schemas") or {}
        return schemas.get(name) or {}

    # -- request handling -------------------------------------------------

    def handle(self, method, raw_target, headers, body):
        """Return ``(status, payload)``; ``payload`` of ``None`` means no body."""
        split = urlsplit(raw_target)
        path = split.path
        query_pairs = parse_qsl(split.query, keep_blank_values=True)
        operation_id = self._routes.get((method.upper(), path))

        if operation_id is None:
            return operation_id, 404, {
                "message": "no contracted operation for %s %s" % (method, path)
            }

        accept = headers.get("accept")
        if accept != JSON_MEDIA_TYPE:
            return operation_id, 406, {
                "message": "Accept must be %s, got %r" % (JSON_MEDIA_TYPE, accept)
            }

        authorization = headers.get("authorization")
        if self.requires_authorization(operation_id):
            if not authorization:
                return operation_id, 401, {"message": "no authorization header"}
            expected_prefix = TOKEN_PREFIX + " "
            if not authorization.startswith(expected_prefix):
                return operation_id, 401, {
                    "message": "authorization must use the %s scheme" % TOKEN_PREFIX
                }
            token = authorization[len(expected_prefix):]
            if token not in self._active_tokens:
                return operation_id, 401, {"message": "token is not valid"}
        elif authorization is not None:
            return operation_id, 400, {
                "message": "%s is unauthenticated and must not carry an "
                "authorization header" % operation_id
            }

        declared = self.declared_query_names(operation_id)
        for name, value in query_pairs:
            if name not in declared:
                return operation_id, 400, {
                    "message": "%s does not declare query parameter %r"
                    % (operation_id, name)
                }
            if value == "":
                return operation_id, 400, {
                    "message": "query parameter %r was sent empty; unset "
                    "parameters must be omitted" % name
                }

        content_type = headers.get("content-type")
        if self.expects_body(operation_id):
            if content_type != JSON_MEDIA_TYPE:
                return operation_id, 415, {
                    "message": "Content-Type must be %s, got %r"
                    % (JSON_MEDIA_TYPE, content_type)
                }
            if not body:
                return operation_id, 400, {"message": "request body is required"}
        else:
            if body:
                return operation_id, 400, {
                    "message": "%s takes no request body" % operation_id
                }
            if content_type is not None:
                return operation_id, 400, {
                    "message": "%s sends no body and must not declare a "
                    "Content-Type" % operation_id
                }

        handler = getattr(self, "_op_" + operation_id, None)
        if handler is None:
            return operation_id, 501, {
                "message": "%s is contracted but not implemented by this mock"
                % operation_id
            }
        status, payload = handler(query_pairs, headers, body)
        return operation_id, status, payload

    # -- operations -------------------------------------------------------

    def _op_acquireToken(self, query_pairs, headers, body):
        try:
            document = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            return 400, {"message": "request body is not JSON: %s" % exc}
        if not isinstance(document, dict):
            return 400, {"message": "request body is not a JSON object"}

        schema = self.body_schema("acquireToken")
        properties = schema.get("properties") or {}
        required = schema.get("required") or []
        for name in document:
            if name not in properties:
                return 400, {"message": "username-password has no property %r" % name}
        for name in required:
            if name not in document:
                return 400, {"message": "%r is required" % name}
        for name, value in document.items():
            if value is None or value == "":
                return 400, {
                    "message": "%r was sent empty; unset properties must be "
                    "omitted from the body" % name
                }
            if not isinstance(value, str):
                return 400, {"message": "%r must be a string" % name}

        self._issued += 1
        token = "opstoken-%d" % self._issued
        self._active_tokens.add(token)
        return 200, {
            "token": token,
            "validity": 1786000000000,
            "expiresAt": "Wednesday, September 30, 2026 11:26:40 AM UTC",
            "roles": ["ADMIN"],
        }

    def _op_releaseToken(self, query_pairs, headers, body):
        token = headers["authorization"].split(" ", 1)[1]
        self._active_tokens.discard(token)
        return 200, None

    def _op_getAlerts(self, query_pairs, headers, body):
        page = 0
        page_size = 1000
        alert_ids = []
        resource_ids = []
        for name, value in query_pairs:
            if name == "page":
                page = value
            elif name == "pageSize":
                page_size = value
            elif name == "id":
                alert_ids.append(value)
            elif name == "resourceId":
                resource_ids.append(value)

        try:
            page = int(page)
            page_size = int(page_size)
        except (TypeError, ValueError):
            return 400, {"message": "page and pageSize must be int32 values"}
        if page < 0:
            return 400, {"message": "page must not be negative"}
        if page_size < 1:
            return 400, {"message": "pageSize must be at least 1"}

        rows = self.alerts
        if alert_ids:
            rows = [row for row in rows if row["alertId"] in set(alert_ids)]
        if resource_ids:
            rows = [row for row in rows if row["resourceId"] in set(resource_ids)]

        if self.page_windows is None:
            total = len(rows)
            start = page * page_size
            window = rows[start:start + page_size]
        else:
            # Some real paged collections return a short page before the last
            # page while rows move underneath the read.  The custom windows
            # let the verification exercise that case deterministically.
            total = sum(len(window) for window in self.page_windows)
            start = sum(
                len(window) for window in self.page_windows[:page]
            )
            window = (
                self.page_windows[page]
                if page < len(self.page_windows) else []
            )
        links = [
            {
                "href": "/suite-api/api/alerts?page=%d&pageSize=%d" % (page, page_size),
                "rel": "SELF",
                "name": "current",
            }
        ]
        if self.page_windows is None and start + page_size < total:
            links.append(
                {
                    "href": "/suite-api/api/alerts?page=%d&pageSize=%d"
                    % (page + 1, page_size),
                    "rel": "NEXT",
                    "name": "next",
                }
            )
        if page > 0:
            links.append(
                {
                    "href": "/suite-api/api/alerts?page=%d&pageSize=%d"
                    % (page - 1, page_size),
                    "rel": "PREVIOUS",
                    "name": "previous",
                }
            )
        return 200, {
            "alerts": window,
            "pageInfo": {"page": page, "pageSize": page_size, "totalCount": total},
            "links": links,
        }

    # -- logging ----------------------------------------------------------

    def record(self, entry):
        with self._lock:
            self._seq += 1
            entry["seq"] = self._seq
            if self.log_path:
                with open(self.log_path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry, sort_keys=True) + "\n")
                    handle.flush()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "vcfops-mock/1.0"

    def log_message(self, fmt, *args):  # keep the verification output clean
        pass

    def _dispatch(self, method):
        service = self.server.service
        length = self.headers.get("Content-Length")
        body = self.rfile.read(int(length)) if length else b""
        headers = {key.lower(): value for key, value in self.headers.items()}

        operation_id, status, payload = service.handle(
            method, self.path, headers, body
        )

        split = urlsplit(self.path)
        service.record(
            {
                "method": method,
                "target": self.path,
                "path": split.path,
                "raw_query": split.query,
                "query_pairs": [
                    list(pair)
                    for pair in parse_qsl(split.query, keep_blank_values=True)
                ],
                "headers": headers,
                "body": body.decode("utf-8", "replace") if body else None,
                "body_length": len(body),
                "operation_id": operation_id,
                "status": status,
            }
        )

        encoded = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        if encoded:
            self.send_header("Content-Type", JSON_MEDIA_TYPE)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        if encoded:
            self.wfile.write(encoded)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def do_PATCH(self):
        self._dispatch("PATCH")


class MockServer:
    """A contract-pinned suite-api on 127.0.0.1, with a readable request log."""

    def __init__(self, contract, log_path, alerts=ALERT_ROWS,
                 page_windows=None):
        self.service = MockService(
            contract,
            alerts=alerts,
            log_path=log_path,
            page_windows=page_windows,
        )
        self.log_path = log_path
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._httpd.service = self.service
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def port(self):
        return self._httpd.server_address[1]

    @property
    def base_url(self):
        """The appliance root -- the ``/suite-api`` prefix is the contract's."""
        return "http://127.0.0.1:%d" % self.port

    def requests(self):
        entries = []
        try:
            with open(self.log_path, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if line:
                        entries.append(json.loads(line))
        except FileNotFoundError:
            return []
        entries.sort(key=lambda entry: entry["seq"])
        return entries

    def stop(self):
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.stop()
        return False
