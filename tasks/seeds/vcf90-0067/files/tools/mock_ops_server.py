"""Loopback mock of the four VCF Operations endpoints named in docs/contract.json.

The mock is pinned to the contract: it loads docs/contract.json at startup and
serves *only* the operations listed there, enforcing the paths, methods, query
parameters, required and optional request fields and the enum vocabularies
recorded in it. Anything else answers 404.

Tokens go stale on a request count rather than on a clock, so a run that has to
refresh its token mid-flight behaves the same way every time. Pick where the
expiry lands with --scenario.

Every request is appended to a JSON Lines request log so a test can inspect the
exact wire shape that a client produced. The log carries a monotonic sequence
number rather than a timestamp so that runs are byte-comparable.

This talks to 127.0.0.1 only. It is not a VMware product and contacts nothing.

Run standalone:

    python tools/mock_ops_server.py --port 8443 --log /tmp/requests.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT_PATH = os.path.join(REPO_ROOT, "docs", "contract.json")

VALID_USERNAME = "admin"
VALID_PASSWORD = "VMware1!VMware1!"
VALID_AUTH_SOURCE = "Local Users"

# Tokens are handed out in this order, so a test can tell which token signed a
# given request and whether a retry used the replacement rather than the stale one.
MOCK_TOKENS = [
    "mock-ops-token-0001",
    "mock-ops-token-0002",
    "mock-ops-token-0003",
    "mock-ops-token-0004",
    "mock-ops-token-0005",
    "mock-ops-token-0006",
]
# Keep the stated lifetime safely in the future. Token retirement in the mock is
# driven only by the deterministic per-request budgets below, but a conforming
# client is still free to consult the validity returned by acquireToken.
MOCK_TOKEN_VALIDITY = 4102444800000
MOCK_TOKEN_EXPIRES_AT = "Friday, January 1, 2100 12:00:00 AM UTC"

# Fixed alert inventory. Five alerts match CRITICAL/IMMEDIATE and are active;
# the WARNING one proves alertCriticality is applied and the CANCELED one proves
# activeOnly is.
ALERTS = [
    {
        "alertId": "1a4f2e0c-4d1f-4a9e-9a0a-0f3f0a1b2c31",
        "resourceId": "8a19c5ba-0a99-4410-b171-f9eeb35050a2",
        "alertLevel": "CRITICAL",
        "status": "ACTIVE",
        "controlState": "OPEN",
        "type": "Virtualization/Hypervisor",
        "subType": "Capacity",
        "alertDefinitionId": "AlertDefinition-VMWARE-VirtualMachine-1",
        "alertDefinitionName": "Virtual machine has CPU contention",
        "alertImpact": "RISK",
        "startTimeUTC": 1744473855,
        "updateTimeUTC": 1744483855,
        "cancelTimeUTC": 0,
        "suspendUntilTimeUTC": 0,
    },
    {
        "alertId": "2b503f1d-5e20-4bb0-8b1b-1a4a1b2c3d42",
        "resourceId": "b8f8f811-1329-481c-aac7-33bf653c94b3",
        "alertLevel": "IMMEDIATE",
        "status": "ACTIVE",
        "controlState": "OPEN",
        "type": "Storage",
        "subType": "Capacity",
        "alertDefinitionId": "AlertDefinition-VMWARE-Datastore-1",
        "alertDefinitionName": "Datastore is running out of disk space",
        "alertImpact": "RISK",
        "startTimeUTC": 1744474100,
        "updateTimeUTC": 1744484100,
        "cancelTimeUTC": 0,
        "suspendUntilTimeUTC": 0,
    },
    {
        "alertId": "3c61402e-6f31-4cc1-9c2c-2b5b2c3d4e53",
        "resourceId": "c4b2f0d1-7a55-4c1e-9de1-2ab90c5e77a1",
        "alertLevel": "CRITICAL",
        "status": "NEW",
        "controlState": "OPEN",
        "type": "Virtualization/Hypervisor",
        "subType": "Performance",
        "alertDefinitionId": "AlertDefinition-VMWARE-HostSystem-1",
        "alertDefinitionName": "Host has memory contention",
        "alertImpact": "HEALTH",
        "startTimeUTC": 1744474400,
        "updateTimeUTC": 1744484400,
        "cancelTimeUTC": 0,
        "suspendUntilTimeUTC": 0,
    },
    {
        "alertId": "4d72513f-7042-4dd2-ad3d-3c6c3d4e5f64",
        "resourceId": "d1e3a2b4-8b66-4d2f-8ef2-3bc01d6f88b2",
        "alertLevel": "IMMEDIATE",
        "status": "UPDATED",
        "controlState": "ASSIGNED",
        "type": "Storage",
        "subType": "Performance",
        "alertDefinitionId": "AlertDefinition-VMWARE-Datastore-2",
        "alertDefinitionName": "Datastore has high write latency",
        "alertImpact": "HEALTH",
        "startTimeUTC": 1744474700,
        "updateTimeUTC": 1744484700,
        "cancelTimeUTC": 0,
        "suspendUntilTimeUTC": 0,
    },
    {
        "alertId": "5e836240-8153-4ee3-be4e-4d7d4e5f6075",
        "resourceId": "e2f4b3c5-9c77-4e30-9f03-4cd12e7f99c3",
        "alertLevel": "CRITICAL",
        "status": "ACTIVE",
        "controlState": "OPEN",
        "type": "Virtualization/Hypervisor",
        "subType": "Capacity",
        "alertDefinitionId": "AlertDefinition-VMWARE-ClusterComputeResource-1",
        "alertDefinitionName": "Cluster has CPU contention",
        "alertImpact": "RISK",
        "startTimeUTC": 1744475000,
        "updateTimeUTC": 1744485000,
        "cancelTimeUTC": 0,
        "suspendUntilTimeUTC": 0,
    },
    {
        "alertId": "6f947351-9264-4ff4-cf5f-5e8e5f607186",
        "resourceId": "f3a5c4d6-ad88-4f41-a014-5de23f800ad4",
        "alertLevel": "WARNING",
        "status": "ACTIVE",
        "controlState": "OPEN",
        "type": "Virtualization/Hypervisor",
        "subType": "Configuration",
        "alertDefinitionId": "AlertDefinition-VMWARE-VirtualMachine-2",
        "alertDefinitionName": "Virtual machine has an outdated VMware Tools version",
        "alertImpact": "HEALTH",
        "startTimeUTC": 1744475300,
        "updateTimeUTC": 1744485300,
        "cancelTimeUTC": 0,
        "suspendUntilTimeUTC": 0,
    },
    {
        "alertId": "70a58462-a375-4005-d060-6f9f60718297",
        "resourceId": "04b6d5e7-be99-4052-b125-6ef340911be5",
        "alertLevel": "CRITICAL",
        "status": "CANCELED",
        "controlState": "OPEN",
        "type": "Storage",
        "subType": "Availability",
        "alertDefinitionId": "AlertDefinition-VMWARE-Datastore-3",
        "alertDefinitionName": "Datastore is not accessible",
        "alertImpact": "HEALTH",
        "startTimeUTC": 1744470000,
        "updateTimeUTC": 1744471000,
        "cancelTimeUTC": 1744472000,
        "suspendUntilTimeUTC": 0,
    },
]

# alertId -> the note id the mock hands back, so responses are reproducible.
NOTE_IDS = {
    "1a4f2e0c-4d1f-4a9e-9a0a-0f3f0a1b2c31": "aa000001-0000-4000-8000-00000000note",
    "2b503f1d-5e20-4bb0-8b1b-1a4a1b2c3d42": "aa000002-0000-4000-8000-00000000note",
    "3c61402e-6f31-4cc1-9c2c-2b5b2c3d4e53": "aa000003-0000-4000-8000-00000000note",
    "4d72513f-7042-4dd2-ad3d-3c6c3d4e5f64": "aa000004-0000-4000-8000-00000000note",
    "5e836240-8153-4ee3-be4e-4d7d4e5f6075": "aa000005-0000-4000-8000-00000000note",
    "6f947351-9264-4ff4-cf5f-5e8e5f607186": "aa000006-0000-4000-8000-00000000note",
    "70a58462-a375-4005-d060-6f9f60718297": "aa000007-0000-4000-8000-00000000note",
}

MOCK_USER_ID = "2d8b511a-676a-4b9b-a032-aae9278c4f1f"

# How many authenticated requests each successive token survives. ``budgets[i]``
# applies to the i-th token issued; tokens past the end of the list get
# ``default``. ``None`` means the token never goes stale.
#
# With --page-size 2 over the five matching alerts the authenticated requests
# are, in order:
#   1 queryAlert page=0   2 addAlertNote #1   3 addAlertNote #2
#   4 queryAlert page=1   5 addAlertNote #3   6 addAlertNote #4
#   7 queryAlert page=2   8 addAlertNote #5   9 releaseToken
# so a first-token budget of 3 expires the token on the page=1 query and a
# budget of 2 expires it on the note for the second alert. A budget of 8 lets
# the complete triage batch finish and expires the token on releaseToken.
SCENARIOS = {
    "stable": {"budgets": [], "default": None},
    "expire_before_page": {"budgets": [3], "default": None},
    "expire_before_note": {"budgets": [2], "default": None},
    "expire_before_release": {"budgets": [8], "default": None},
    "always_expired": {"budgets": [], "default": 0},
}

# Headers that vary between HTTP client implementations and carry no contract
# meaning. They are dropped from the log so assertions stay stable.
BORING_HEADERS = frozenset(
    {"host", "user-agent", "accept-encoding", "connection", "content-length"}
)


def load_contract(path: str = CONTRACT_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


class ContractError(Exception):
    """A request that the pinned contract does not permit."""

    def __init__(self, status: int, message: str, detail: str = "", extra=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.detail = detail
        self.extra = extra or {}


def _path_matcher(template: str):
    """Turn ``/suite-api/api/alerts/{id}/notes`` into a compiled regex."""
    pattern = "".join(
        "(?P<%s>[^/]+)" % part[1:-1] if part.startswith("{") and part.endswith("}")
        else re.escape(part)
        for part in re.split(r"(\{[a-zA-Z_][a-zA-Z0-9_]*\})", template)
    )
    return re.compile("^" + pattern + "$")


class MockState:
    """Everything the handler mutates, shared across the server's threads."""

    def __init__(self, contract: dict, scenario: str, log_path: str | None):
        self.contract = contract
        self.lock = threading.Lock()
        self.scenario = scenario
        self.budgets = list(SCENARIOS[scenario]["budgets"])
        self.default_budget = SCENARIOS[scenario]["default"]
        self.log_path = log_path
        self.log: list[dict] = []
        self.seq = 0

        self.issued: list[str] = []
        self.live: dict[str, int | None] = {}
        self.notes: list[dict] = []

        ops = contract["operations"]
        # (method, compiled path) -> operationId, built from the contract rather
        # than hardcoded, so the mock cannot drift from it.
        self.routes = [
            (op["method"], _path_matcher(op["path"]), name) for name, op in ops.items()
        ]
        self.operation_ids = sorted(ops)

        token_body = ops["acquireToken"]["requestBody"]
        self.token_required = set(token_body["required"])
        self.token_allowed = set(token_body["required"]) | set(token_body["optional"])

        query_body = ops["queryAlert"]["requestBody"]
        self.query_required = set(query_body["required"])
        self.query_allowed = set(query_body["required"]) | set(query_body["optional"])
        self.criticality_enum = set(contract["enums"]["alert-query.alertCriticality[]"])
        self.query_params = {
            item["name"]: item for item in ops["queryAlert"]["queryParameters"]
        }

        note_body = ops["addAlertNote"]["requestBody"]
        self.note_required = set(note_body["required"])
        self.note_allowed = set(note_body["required"]) | set(note_body["optional"])

        self.auth_header = contract["security"]["headerName"]
        self.auth_template = contract["security"]["headerValueTemplate"]
        self.min_page_size = contract["paging"]["minimumPageSize"]

    # -- log --------------------------------------------------------------

    def record(self, entry: dict) -> None:
        with self.lock:
            entry["seq"] = self.seq
            self.seq += 1
            self.log.append(entry)
            if self.log_path:
                with open(self.log_path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry, sort_keys=True) + "\n")

    # -- tokens -----------------------------------------------------------

    def issue_token(self) -> str:
        with self.lock:
            index = len(self.issued)
            if index < len(MOCK_TOKENS):
                token = MOCK_TOKENS[index]
            else:
                token = "mock-ops-token-%04d" % (index + 1)
            if index < len(self.budgets):
                budget = self.budgets[index]
            else:
                budget = self.default_budget
            self.issued.append(token)
            self.live[token] = budget
            return token

    def spend(self, token: str) -> str:
        """Return 'ok', 'expired' or 'unknown' for one authenticated request."""
        with self.lock:
            if token not in self.live:
                return "unknown"
            remaining = self.live[token]
            if remaining is None:
                return "ok"
            if remaining <= 0:
                return "expired"
            self.live[token] = remaining - 1
            return "ok"

    def revoke(self, token: str) -> None:
        with self.lock:
            self.live.pop(token, None)

    # -- alerts -----------------------------------------------------------

    def matching_alerts(self, query: dict) -> list[dict]:
        selected = list(ALERTS)
        if query.get("activeOnly"):
            selected = [a for a in selected if a["status"] != "CANCELED"]
        if "alertCriticality" in query:
            wanted = set(query["alertCriticality"])
            selected = [a for a in selected if a["alertLevel"] in wanted]
        if "alertName" in query:
            needle = query["alertName"].lower()
            selected = [
                a for a in selected if needle in a["alertDefinitionName"].lower()
            ]
        return selected

    def add_note(self, alert_id: str, content: str) -> dict:
        with self.lock:
            note = {
                "id": NOTE_IDS[alert_id],
                "alertId": alert_id,
                "creationTimeUTC": 1744486000 + len(self.notes),
                "type": "USER",
                "userId": MOCK_USER_ID,
                "userName": VALID_USERNAME,
                "note": content,
            }
            self.notes.append(note)
            return note


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state: MockState  # injected by make_server

    # -- plumbing ---------------------------------------------------------

    def log_message(self, fmt, *args):  # noqa: A003 - silence stderr chatter
        pass

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _interesting_headers(self) -> dict:
        return {
            key.lower(): value
            for key, value in self.headers.items()
            if key.lower() not in BORING_HEADERS
        }

    def _send(self, status: int, payload=None, content_type="application/json"):
        if payload is None:
            body = b""
        elif isinstance(payload, bytes):
            body = payload
        else:
            body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        if body:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)
        return status

    def _resolve(self, method: str, path: str):
        for route_method, matcher, operation_id in self.state.routes:
            if route_method != method:
                continue
            match = matcher.match(path)
            if match:
                return operation_id, match.groupdict()
        return None, {}

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        raw_body = self._read_body()
        entry = {
            "method": method,
            "path": parsed.path,
            "query": dict(parse_qs(parsed.query, keep_blank_values=True)),
            "headers": self._interesting_headers(),
            "body_raw": raw_body.decode("utf-8", "replace") if raw_body else None,
        }
        try:
            entry["body"] = json.loads(raw_body) if raw_body else None
        except ValueError:
            entry["body"] = None
        entry["body_keys"] = (
            sorted(entry["body"]) if isinstance(entry["body"], dict) else None
        )

        operation_id, path_params = self._resolve(method, parsed.path)
        entry["operationId"] = operation_id
        entry["pathParams"] = path_params
        entry["token"] = self._presented_token()

        try:
            if operation_id is None:
                raise ContractError(
                    404,
                    "No such operation in the pinned contract.",
                    "%s %s is not one of: %s"
                    % (method, parsed.path, ", ".join(self.state.operation_ids)),
                )
            status = getattr(self, "_op_" + operation_id)(entry)
        except ContractError as exc:
            payload = {
                "message": exc.message,
                "detail": exc.detail,
                "contractViolation": True,
            }
            payload.update(exc.extra)
            status = self._send(exc.status, payload)

        entry["status"] = status
        self.state.record(entry)

    def do_GET(self):  # noqa: N802
        self._dispatch("GET")

    def do_POST(self):  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self):  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self):  # noqa: N802
        self._dispatch("DELETE")

    # -- shared checks ----------------------------------------------------

    def _presented_token(self):
        """The bare token in the Authorization header, or None."""
        supplied = self.headers.get(self.state.auth_header)
        if not supplied:
            return None
        prefix, _, _ = self.state.auth_template.partition("{token}")
        if prefix and supplied.startswith(prefix):
            return supplied[len(prefix):]
        return supplied

    def _require_auth(self) -> str:
        supplied = self.headers.get(self.state.auth_header)
        if not supplied:
            raise ContractError(
                401,
                "Missing %s header." % self.state.auth_header,
                "Every operation but acquireToken is guarded by "
                "Token-based-authorization.",
            )
        token = self._presented_token()
        if supplied != self.state.auth_template.format(token=token):
            raise ContractError(
                401,
                "Malformed %s header." % self.state.auth_header,
                "Expected %r." % self.state.auth_template,
            )
        outcome = self.state.spend(token)
        if outcome == "expired":
            raise ContractError(
                401,
                "The token has expired.",
                "Acquire a new token and replay this request; work already "
                "accepted under the previous token stands.",
                {"tokenExpired": True},
            )
        if outcome == "unknown":
            raise ContractError(
                401,
                "Unknown or released token.",
                "This token was never issued by this server, or was released.",
            )
        return token

    def _require_json_body(self, entry: dict) -> dict:
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if content_type != "application/json":
            raise ContractError(
                415,
                "Content-Type must be application/json.",
                "Got %r." % (content_type or "<absent>"),
            )
        if entry["body_raw"] is None:
            raise ContractError(400, "Request body is required.")
        if not isinstance(entry["body"], dict):
            raise ContractError(400, "Request body must be a JSON object.")
        return entry["body"]

    @staticmethod
    def _check_fields(body: dict, allowed: set, required: set, schema: str) -> None:
        unknown = sorted(set(body) - allowed)
        if unknown:
            raise ContractError(
                400,
                "Unknown field(s) for %s: %s." % (schema, ", ".join(unknown)),
                "The 9.0 contract allows only: %s." % ", ".join(sorted(allowed)),
            )
        empty = sorted(
            key
            for key, value in body.items()
            if value is None or (isinstance(value, (str, list, dict)) and len(value) == 0)
        )
        if empty:
            raise ContractError(
                400,
                "Field(s) sent empty: %s." % ", ".join(empty),
                "Optional fields must be omitted from the JSON object entirely, "
                'not sent as null, "" or [].',
            )
        missing = sorted(required - set(body))
        if missing:
            raise ContractError(
                400,
                "Missing required field(s) for %s: %s." % (schema, ", ".join(missing)),
            )

    def _int_query_param(self, entry: dict, name: str) -> int:
        spec = self.state.query_params[name]
        values = entry["query"].get(name)
        if values is None:
            return spec["default"]
        if len(values) != 1:
            raise ContractError(400, "Query parameter %r was repeated." % name)
        raw = values[0]
        if raw == "":
            raise ContractError(
                400,
                "Query parameter %r was sent empty." % name,
                "It is optional (default %s): omit it entirely rather than "
                "sending it with no value." % spec["default"],
            )
        try:
            return int(raw)
        except ValueError:
            raise ContractError(
                400, "Query parameter %r must be an integer, got %r." % (name, raw)
            ) from None

    # -- operations -------------------------------------------------------

    def _op_acquireToken(self, entry: dict) -> int:  # noqa: N802
        if self.headers.get(self.state.auth_header) is not None:
            raise ContractError(
                400,
                "acquireToken must not carry an %s header." % self.state.auth_header,
                'The specification sets "security": [] on this operation.',
            )
        body = self._require_json_body(entry)
        self._check_fields(
            body,
            self.state.token_allowed,
            self.state.token_required,
            "username-password",
        )
        for key, value in body.items():
            if not isinstance(value, str):
                raise ContractError(400, "Field %r must be a string." % key)
        if body["username"] != VALID_USERNAME or body["password"] != VALID_PASSWORD:
            raise ContractError(401, "Authentication failed.")
        if "authSource" in body and body["authSource"] != VALID_AUTH_SOURCE:
            raise ContractError(
                401,
                "Authentication failed.",
                "Unknown authSource %r." % body["authSource"],
            )
        token = self.state.issue_token()
        return self._send(
            200,
            {
                "token": token,
                "validity": MOCK_TOKEN_VALIDITY,
                "expiresAt": MOCK_TOKEN_EXPIRES_AT,
                "roles": ["AlertAdmin"],
            },
        )

    def _op_queryAlert(self, entry: dict) -> int:  # noqa: N802
        self._require_auth()
        page = self._int_query_param(entry, "page")
        page_size = self._int_query_param(entry, "pageSize")
        if page < 0:
            raise ContractError(400, "page must not be negative, got %d." % page)
        if page_size < self.state.min_page_size:
            raise ContractError(
                400,
                "pageSize must be at least %d, got %d."
                % (self.state.min_page_size, page_size),
                "page-info.pageSize declares minimum 1.",
            )
        unexpected = sorted(set(entry["query"]) - set(self.state.query_params))
        if unexpected:
            raise ContractError(
                400,
                "Unknown query parameter(s): %s." % ", ".join(unexpected),
                "queryAlert takes only page and pageSize.",
            )

        body = self._require_json_body(entry)
        self._check_fields(
            body, self.state.query_allowed, self.state.query_required, "alert-query"
        )
        if "activeOnly" in body and not isinstance(body["activeOnly"], bool):
            raise ContractError(400, "activeOnly must be a boolean.")
        if "alertName" in body and not isinstance(body["alertName"], str):
            raise ContractError(400, "alertName must be a string.")
        criticality = body.get("alertCriticality")
        if criticality is not None:
            if not isinstance(criticality, list) or not all(
                isinstance(item, str) for item in criticality
            ):
                raise ContractError(400, "alertCriticality must be an array of strings.")
            for item in criticality:
                if item not in self.state.criticality_enum:
                    raise ContractError(
                        400,
                        "Unknown alert criticality %r." % item,
                        "Allowed at 9.0.0.0: %s."
                        % ", ".join(sorted(self.state.criticality_enum)),
                    )

        selected = self.state.matching_alerts(body)
        window = selected[page * page_size : (page + 1) * page_size]
        return self._send(
            200,
            {
                "pageInfo": {
                    "totalCount": len(selected),
                    "page": page,
                    "pageSize": page_size,
                },
                "alerts": window,
                "links": [
                    {
                        "href": "/suite-api/api/alerts/query?page=%d&pageSize=%d"
                        % (page, page_size),
                        "rel": "SELF",
                        "name": "current",
                    }
                ],
            },
        )

    def _op_addAlertNote(self, entry: dict) -> int:  # noqa: N802
        self._require_auth()
        alert_id = entry["pathParams"]["id"]
        if alert_id not in NOTE_IDS:
            raise ContractError(
                404, "No Alert is found with the specified identifier: %s." % alert_id
            )
        if entry["query"]:
            raise ContractError(
                400,
                "addAlertNote takes no query parameters.",
                "Got: %s." % ", ".join(sorted(entry["query"])),
            )
        body = self._require_json_body(entry)
        self._check_fields(
            body, self.state.note_allowed, self.state.note_required, "alert-note-content"
        )
        if not isinstance(body["content"], str):
            raise ContractError(400, "content must be a string.")
        return self._send(201, self.state.add_note(alert_id, body["content"]))

    def _op_releaseToken(self, entry: dict) -> int:  # noqa: N802
        token = self._require_auth()
        self.state.revoke(token)
        return self._send(200, None)


def make_server(host="127.0.0.1", port=0, scenario="stable", log_path=None, contract=None):
    """Build (but do not start) a mock server bound to a loopback port."""
    if scenario not in SCENARIOS:
        raise ValueError(
            "unknown scenario %r; choose from %s" % (scenario, sorted(SCENARIOS))
        )
    state = MockState(contract or load_contract(), scenario, log_path)
    handler = type("BoundHandler", (Handler,), {"state": state})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    httpd.mock_state = state
    return httpd


class RunningMock:
    """Context manager that serves the mock on a background thread."""

    def __init__(self, scenario="stable", log_path=None, contract=None):
        self.httpd = make_server(scenario=scenario, log_path=log_path, contract=contract)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.port = self.httpd.server_address[1]
        self.base_url = "http://127.0.0.1:%d" % self.port
        self.state = self.httpd.mock_state

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        return False

    def requests(self, operation_id=None, status=None):
        entries = list(self.state.log)
        if operation_id is not None:
            entries = [e for e in entries if e.get("operationId") == operation_id]
        if status is not None:
            entries = [e for e in entries if e.get("status") == status]
        return entries


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--scenario", default="expire_before_page", choices=sorted(SCENARIOS))
    parser.add_argument("--log", dest="log_path", default=None, help="JSON Lines request log")
    args = parser.parse_args()

    httpd = make_server(args.host, args.port, args.scenario, args.log_path)
    host, port = httpd.server_address[:2]
    scenario = SCENARIOS[args.scenario]
    print("mock VCF Operations on http://%s:%d  scenario=%s" % (host, port, args.scenario))
    print(
        "token budgets: %s (then %s)"
        % (
            scenario["budgets"] or "-",
            "unlimited" if scenario["default"] is None else scenario["default"],
        )
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
