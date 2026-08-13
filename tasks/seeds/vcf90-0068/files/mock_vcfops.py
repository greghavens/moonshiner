#!/usr/bin/env python3
"""Contract-pinned loopback VCF Operations appliance used by protected verification.

Routes are built from docs/contract.json. Nothing outside the two operations that
contract names is served, so a candidate cannot reach an operation the pinned
VMware Cloud Foundation 9.0 specification does not cover.

Every request is appended to a JSON Lines request log so verify.py can assert the
exact wire shape the candidate's client produced: target, query parameter set and
serialization, headers, JSON body member set, and the page sequence.

Run standalone for manual poking:

    python3 mock_vcfops.py 18443

"""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit


ROOT = Path(__file__).resolve().parent
CONTRACT_PATH = ROOT / "docs" / "contract.json"

PINNED_COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
PINNED_TAG = "9.0.0.0"
PINNED_SPEC_PATH = "specifications/vcf-operations/vcf-operations-openapi.json"
EXPECTED_OPERATION_IDS = {"acquireToken", "getAlerts"}


# --------------------------------------------------------------------------
# Contract loading. The mock refuses to start against anything but the pinned
# projection, so the served surface can never drift away from the 9.0.0.0 spec.
# --------------------------------------------------------------------------

def load_contract() -> dict[str, Any]:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    source = contract["source"]
    if source["commitSha"] != PINNED_COMMIT:
        raise SystemExit(f"contract.json commitSha drifted: {source['commitSha']!r}")
    if source["tag"] != PINNED_TAG:
        raise SystemExit(f"contract.json tag drifted: {source['tag']!r}")
    if source["specPath"] != PINNED_SPEC_PATH:
        raise SystemExit(f"contract.json specPath drifted: {source['specPath']!r}")
    ids = {op["operationId"] for op in contract["operations"]}
    if ids != EXPECTED_OPERATION_IDS:
        raise SystemExit(f"contract.json operation set drifted: {sorted(ids)}")
    return contract


CONTRACT = load_contract()
BASE_PATH = CONTRACT["serverBasePath"]
AUTH_HEADER_NAME = CONTRACT["securitySchemes"]["Token-based-authorization"]["name"].lower()

# (method, absolute target) -> operationId, straight from the contract.
ROUTES: dict[tuple[str, str], str] = {
    (op["method"], BASE_PATH + op["path"]): op["operationId"]
    for op in CONTRACT["operations"]
}

# Query parameter names each operation is allowed to receive, straight from the
# contract. Anything else is a 400 rather than a silently ignored extra.
ALLOWED_QUERY: dict[str, set[str]] = {
    op["operationId"]: {p["name"] for p in op.get("parameters", []) if p["in"] == "query"}
    for op in CONTRACT["operations"]
}

# Members of username-password, straight from the contract.
_UP = CONTRACT["schemas"]["username-password"]
ACQUIRE_BODY_MEMBERS: set[str] = set(_UP["properties"])
ACQUIRE_BODY_REQUIRED: set[str] = set(_UP["required"])

# Operations whose contract security array is empty are the unauthenticated ones.
UNAUTHENTICATED: set[str] = {
    op["operationId"] for op in CONTRACT["operations"] if not op.get("security")
}


# --------------------------------------------------------------------------
# Fixture estate. Dummy identifiers only; nothing here reaches a real appliance.
# --------------------------------------------------------------------------

USERNAME = "svc-vcfops-reader@local"
# A deterministic fixture identity that makes acquireToken return a non-2xx
# status other than the authentication-specific 401.
ERROR_USERNAME = "svc-vcfops-reader-error@local"
NON_JSON_USERNAME = "svc-vcfops-reader-non-json@local"
PASSWORD = "dummy-vcfops-pass-90"
AUTH_SOURCE = "Local Users"
TOKEN = "dummy-vcfops-auth-token-90"
TOKEN_VALIDITY = 1744495857000
TOKEN_EXPIRES_AT = "2025-04-12T21:30:57Z"

R1 = "11111111-1111-4111-8111-111111111111"
R2 = "22222222-2222-4222-8222-222222222222"
R3 = "33333333-3333-4333-8333-333333333333"
# A resource that owns no alerts, used to exercise the empty-collection path.
R_EMPTY = "88888888-8888-4888-8888-888888888888"
# A resource whose page-info deliberately misreports totalCount and whose pages
# never run out, used to exercise the client's runaway-pagination guard.
R_RUNAWAY = "99999999-9999-4999-8999-999999999999"
RUNAWAY_TOTAL_COUNT = 40
# Resources used to exercise response failures and optional alert sort members.
R_HTTP_ERROR = "77777777-7777-4777-8777-777777777771"
R_NON_JSON = "77777777-7777-4777-8777-777777777772"
R_BAD_PAGE_INFO = "77777777-7777-4777-8777-777777777773"
R_MISSING_SORT = "77777777-7777-4777-8777-777777777774"

A1 = "aaaaaaaa-0001-4001-8001-aaaaaaaaaaaa"
A2 = "aaaaaaaa-0002-4002-8002-aaaaaaaaaaaa"
A3 = "aaaaaaaa-0003-4003-8003-aaaaaaaaaaaa"
A4 = "aaaaaaaa-0004-4004-8004-aaaaaaaaaaaa"
A5 = "aaaaaaaa-0005-4005-8005-aaaaaaaaaaaa"
A6 = "aaaaaaaa-0006-4006-8006-aaaaaaaaaaaa"
A7 = "aaaaaaaa-0007-4007-8007-aaaaaaaaaaaa"


def _alert(
    alert_id: str,
    resource_id: str,
    level: str,
    start: int,
    status: str,
    definition: str,
) -> dict[str, Any]:
    return {
        "alertId": alert_id,
        "resourceId": resource_id,
        "alertLevel": level,
        "type": "Virtualization/Hypervisor",
        "subType": "Capacity",
        "status": status,
        "startTimeUTC": start,
        "cancelTimeUTC": 0,
        "updateTimeUTC": start + 600,
        "controlState": "OPEN",
        "alertDefinitionName": definition,
    }


# Storage order is deliberately unsorted and deliberately does not line up with
# any page boundary, so a per-page sort produces the wrong overall order.
ALERTS: list[dict[str, Any]] = [
    _alert(A5, R2, "WARNING", 1744473700, "ACTIVE", "Datastore is running out of disk space"),
    _alert(A2, R2, "CRITICAL", 1744473900, "NEW", "Host has memory contention"),
    _alert(A7, R3, "INFORMATION", 1744473500, "UPDATED", "Cluster has unbalanced workload"),
    _alert(A1, R1, "IMMEDIATE", 1744473857, "ACTIVE", "Virtual machine CPU demand is high"),
    _alert(A6, R1, "CRITICAL", 1744474100, "NEW", "Host is in an unknown state"),
    _alert(A3, R1, "WARNING", 1744473857, "ACTIVE", "Virtual machine has excess memory"),
    _alert(A4, R3, "IMMEDIATE", 1744474100, "UPDATED", "Datastore latency is elevated"),
]

# The contract makes both sort members optional. These objects also carry
# unique payload members so verification can prove that candidates return the
# raw alert objects rather than projecting or reconstructing them.
MISSING_SORT_ALERTS: list[dict[str, Any]] = [
    {
        "alertId": "aaaaaaaa-0008-4008-8008-aaaaaaaaaaaa",
        "resourceId": R_MISSING_SORT,
        "fixture": "no-start-b",
    },
    {"resourceId": R_MISSING_SORT, "startTimeUTC": 1744474200, "fixture": "no-id"},
    {
        "alertId": "bbbbbbbb-0007-4007-8007-bbbbbbbbbbbb",
        "resourceId": R_MISSING_SORT,
        "fixture": "no-start-a",
    },
    {
        "alertId": "aaaaaaaa-0010-4010-8010-aaaaaaaaaaaa",
        "resourceId": R_MISSING_SORT,
        "startTimeUTC": 1744474150,
        "fixture": "same-time-z",
    },
    {
        "alertId": "aaaaaaaa-0009-4009-8009-aaaaaaaaaaaa",
        "resourceId": R_MISSING_SORT,
        "startTimeUTC": 1744474150,
        "fixture": "same-time-a",
    },
]


class RawResponse:
    """A deliberately non-JSON appliance response for protocol verification."""

    def __init__(self, body: bytes, content_type: str = "text/plain") -> None:
        self.body = body
        self.content_type = content_type


def select_alerts(alert_ids: list[str], resource_ids: list[str]) -> list[dict[str, Any]]:
    """Union semantics, matching the operation summary in the pinned spec.

    "Look up Alerts by their identifiers or using the identifiers of the
    Resources they are associated with."
    """
    if not alert_ids and not resource_ids:
        return list(ALERTS)
    wanted_alerts = set(alert_ids)
    wanted_resources = set(resource_ids)
    return [
        a
        for a in ALERTS
        if a["alertId"] in wanted_alerts or a["resourceId"] in wanted_resources
    ]


# --------------------------------------------------------------------------
# Request log
# --------------------------------------------------------------------------

class RequestLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._seq = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")

    def append(self, entry: dict[str, Any]) -> None:
        with self._lock:
            self._seq += 1
            entry["seq"] = self._seq
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")

    def reset(self) -> None:
        with self._lock:
            self._seq = 0
            self.path.write_text("", encoding="utf-8")


# --------------------------------------------------------------------------
# Handler
# --------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "VcfOperationsMock/9.0"
    sys_version = ""

    # ---- plumbing ----

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003 - stdlib hook
        return

    def _read_body(self) -> bytes:
        length = self.headers.get("Content-Length")
        if not length:
            return b""
        try:
            count = int(length)
        except ValueError:
            return b""
        return self.rfile.read(count) if count > 0 else b""

    def _headers_dict(self) -> dict[str, str]:
        return {key.lower(): value for key, value in self.headers.items()}

    def _accepts_json(self) -> bool:
        accept = self.headers.get("Accept")
        if not accept:
            return False
        tokens = [part.split(";")[0].strip().lower() for part in accept.split(",")]
        return "application/json" in tokens

    @staticmethod
    def _send(status: int, payload: Any | None) -> tuple[int, Any]:
        """Decide the answer. Writing it is deferred until the log entry lands."""
        return status, payload

    def _write(self, status: int, payload: Any | None) -> None:
        if isinstance(payload, RawResponse):
            body = payload.body
            content_type = payload.content_type
        else:
            body = b"" if payload is None else json.dumps(payload).encode("utf-8")
            content_type = "application/json"
        self.send_response(status)
        if body:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    # ---- dispatch ----

    def do_GET(self) -> None:  # noqa: N802 - stdlib hook
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802 - stdlib hook
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802 - stdlib hook
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib hook
        self._dispatch("DELETE")

    def do_PATCH(self) -> None:  # noqa: N802 - stdlib hook
        self._dispatch("PATCH")

    def _dispatch(self, method: str) -> None:
        split = urlsplit(self.path)
        path = split.path
        raw_query = split.query
        query = parse_qs(raw_query, keep_blank_values=True)
        raw_body = self._read_body()
        headers = self._headers_dict()

        body_json: Any = None
        body_text: str | None = None
        if raw_body:
            body_text = raw_body.decode("utf-8", errors="replace")
            try:
                body_json = json.loads(body_text)
            except ValueError:
                body_json = None

        operation_id = ROUTES.get((method, path))
        if operation_id is None:
            status, payload = self._send(
                404, {"message": f"no contract operation for {method} {path}"}
            )
        elif operation_id == "acquireToken":
            status, payload = self._acquire_token(headers, query, body_json, body_text)
        else:
            status, payload = self._get_alerts(headers, query, raw_body)

        # The log entry is committed before the response is written, so a client
        # that has read its answer is guaranteed to be visible in the log.
        self.server.request_log.append(
            {
                "method": method,
                "path": path,
                "rawQuery": raw_query,
                "query": query,
                "headers": headers,
                "hasAuthorizationHeader": AUTH_HEADER_NAME in headers,
                "bodyRaw": body_text,
                "bodyJson": body_json,
                "operationId": operation_id,
                "status": status,
                "responseSummary": _summarize(payload),
            }
        )

        try:
            self._write(status, payload)
        except BrokenPipeError:
            return

    # ---- operations ----

    def _acquire_token(
        self,
        headers: dict[str, str],
        query: dict[str, list[str]],
        body_json: Any,
        body_text: str | None,
    ) -> tuple[int, Any]:
        if query:
            return self._send(400, {"message": "acquireToken takes no query parameters"})
        content_type = headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type != "application/json":
            return self._send(415, {"message": f"unsupported Content-Type {content_type!r}"})
        if not self._accepts_json():
            return self._send(406, {"message": "Accept must allow application/json"})
        if "authorization" in headers:
            # security: [] in the pinned spec - this operation is the one that
            # mints the token, so it must not be sent one.
            return self._send(400, {"message": "acquireToken must not carry Authorization"})
        if not isinstance(body_json, dict):
            return self._send(400, {"message": f"body is not a JSON object: {body_text!r}"})

        members = set(body_json)
        unknown = sorted(members - ACQUIRE_BODY_MEMBERS)
        if unknown:
            return self._send(400, {"message": f"username-password has no member(s) {unknown}"})
        missing = sorted(ACQUIRE_BODY_REQUIRED - members)
        if missing:
            return self._send(400, {"message": f"username-password is missing {missing}"})
        for name, value in body_json.items():
            if value is None or value == "":
                return self._send(
                    400,
                    {
                        "message": f"member {name!r} was sent as {value!r}; an unset "
                        "optional member must be omitted, not sent empty"
                    },
                )
            if not isinstance(value, str):
                return self._send(400, {"message": f"member {name!r} must be a string"})

        if body_json["username"] == ERROR_USERNAME and body_json["password"] == PASSWORD:
            return self._send(503, {"message": "deterministic acquireToken failure"})
        if body_json["username"] == NON_JSON_USERNAME and body_json["password"] == PASSWORD:
            return self._send(200, RawResponse(b"this token response is deliberately not JSON"))
        if body_json["username"] != USERNAME or body_json["password"] != PASSWORD:
            return self._send(401, None)
        if "authSource" in body_json and body_json["authSource"] != AUTH_SOURCE:
            return self._send(401, None)

        return self._send(
            200,
            {
                "token": TOKEN,
                "validity": TOKEN_VALIDITY,
                "expiresAt": TOKEN_EXPIRES_AT,
                "roles": ["ReadOnly"],
            },
        )

    def _get_alerts(
        self,
        headers: dict[str, str],
        query: dict[str, list[str]],
        raw_body: bytes,
    ) -> tuple[int, Any]:
        if raw_body:
            return self._send(400, {"message": "getAlerts must not carry a request body"})
        if not self._accepts_json():
            return self._send(406, {"message": "Accept must allow application/json"})
        token = headers.get(AUTH_HEADER_NAME)
        if token is None:
            return self._send(401, {"message": "missing Authorization header"})
        if token != TOKEN:
            return self._send(401, {"message": "Authorization header does not carry a live token"})

        allowed = ALLOWED_QUERY["getAlerts"]
        unknown = sorted(set(query) - allowed)
        if unknown:
            return self._send(400, {"message": f"getAlerts has no query parameter(s) {unknown}"})
        for name, values in query.items():
            for value in values:
                if value == "":
                    return self._send(
                        400,
                        {
                            "message": f"query parameter {name!r} was sent empty; an unset "
                            "optional parameter must be omitted entirely"
                        },
                    )
                if name in ("id", "resourceId") and "," in value:
                    return self._send(
                        400,
                        {
                            "message": f"query parameter {name!r} arrived comma-joined as "
                            f"{value!r}; the contract serializes it form/explode, one "
                            "repeated key per value"
                        },
                    )

        for name in ("page", "pageSize"):
            if name not in query:
                return self._send(400, {"message": f"query parameter {name!r} is required here"})
            if len(query[name]) != 1:
                return self._send(400, {"message": f"query parameter {name!r} was repeated"})

        try:
            page = int(query["page"][0])
            page_size = int(query["pageSize"][0])
        except ValueError:
            return self._send(400, {"message": "page and pageSize must be integers"})
        if page < 0:
            return self._send(400, {"message": "page is 0-based and cannot be negative"})
        if page_size < 1:
            return self._send(400, {"message": "pageSize must be at least 1"})

        alert_ids = query.get("id", [])
        resource_ids = query.get("resourceId", [])

        if R_HTTP_ERROR in resource_ids:
            return self._send(503, {"message": "deterministic getAlerts failure"})
        if R_NON_JSON in resource_ids:
            return self._send(200, RawResponse(b"this is deliberately not JSON"))
        if R_BAD_PAGE_INFO in resource_ids:
            return self._send(200, {"pageInfo": {}, "alerts": []})
        if R_MISSING_SORT in resource_ids:
            start = page * page_size
            window = MISSING_SORT_ALERTS[start : start + page_size]
            more = start + page_size < len(MISSING_SORT_ALERTS)
            return self._send(
                200,
                self._envelope(
                    len(MISSING_SORT_ALERTS), page, page_size, window, alert_ids,
                    resource_ids, more
                ),
            )

        if R_RUNAWAY in resource_ids:
            # Deliberately inconsistent: page-info claims a large collection and
            # every page keeps returning rows, so a client that trusts the server
            # to run out never terminates.
            window = [ALERTS[page % len(ALERTS)], ALERTS[(page + 1) % len(ALERTS)]]
            return self._send(
                200,
                self._envelope(RUNAWAY_TOTAL_COUNT, page, page_size, window, alert_ids,
                               resource_ids, more=True),
            )

        matched = select_alerts(alert_ids, resource_ids)
        start = page * page_size
        window = matched[start : start + page_size]
        more = start + page_size < len(matched)
        return self._send(
            200,
            self._envelope(len(matched), page, page_size, window, alert_ids, resource_ids, more),
        )

    def _envelope(
        self,
        total: int,
        page: int,
        page_size: int,
        window: list[dict[str, Any]],
        alert_ids: list[str],
        resource_ids: list[str],
        more: bool,
    ) -> dict[str, Any]:
        def href(target_page: int) -> str:
            pairs = [("id", v) for v in alert_ids]
            pairs += [("resourceId", v) for v in resource_ids]
            pairs += [("page", str(target_page)), ("pageSize", str(page_size))]
            return f"{BASE_PATH}/api/alerts?{urlencode(pairs)}"

        links = [{"href": href(page), "rel": "SELF", "name": "current"}]
        if page > 0:
            links.append({"href": href(page - 1), "rel": "PREVIOUS", "name": "previous"})
        if more:
            links.append({"href": href(page + 1), "rel": "NEXT", "name": "next"})
        return {
            "pageInfo": {"totalCount": total, "page": page, "pageSize": page_size},
            "links": links,
            "alerts": list(window),
        }


def _summarize(payload: Any) -> Any:
    if isinstance(payload, RawResponse):
        return {"rawBody": payload.body.decode("utf-8", errors="replace")}
    if isinstance(payload, dict) and "alerts" in payload:
        return {
            "pageInfo": payload.get("pageInfo"),
            "alertIds": [a.get("alertId") for a in payload["alerts"]],
        }
    if isinstance(payload, dict) and "token" in payload:
        return {"token": "<redacted>"}
    return payload


# --------------------------------------------------------------------------
# Server wrapper
# --------------------------------------------------------------------------

class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_log: RequestLog


class MockAppliance:
    """A loopback-only VCF Operations appliance pinned to docs/contract.json."""

    def __init__(self, log_path: Path, port: int = 0) -> None:
        self.request_log = RequestLog(log_path)
        self._server = _Server(("127.0.0.1", port), _Handler)
        self._server.request_log = self.request_log
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> str:
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.base_url

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def entries(self) -> list[dict[str, Any]]:
        text = self.request_log.path.read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def reset(self) -> None:
        self.request_log.reset()

    def __enter__(self) -> "MockAppliance":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()


def main(argv: list[str]) -> int:
    port = int(argv[1]) if len(argv) > 1 else 0
    log_path = Path(os.environ.get("VCFOPS_MOCK_LOG", ROOT / "_verification" / "requests.jsonl"))
    appliance = MockAppliance(log_path, port)
    url = appliance.start()
    print(f"mock VCF Operations on {url}{BASE_PATH} (log: {log_path})", flush=True)
    print(f"operations: {', '.join(sorted(EXPECTED_OPERATION_IDS))}", flush=True)
    try:
        while True:
            threading.Event().wait(3600)
    except KeyboardInterrupt:
        appliance.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
