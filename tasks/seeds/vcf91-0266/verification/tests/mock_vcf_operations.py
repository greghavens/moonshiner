"""Loopback-only VCF Operations mock, pinned to docs/contract.json.

The route table is derived from the contract at construction time, so the mock
can only ever serve the eight operations the contract names; anything else is
refused with 404 and recorded as a rejection. Every request is appended to a
JSONL log on disk that the acceptance suite reads back.
"""

import io
import json
import os
import re
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qsl, unquote, urlsplit

from . import fixtures

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"

EXPECTED_OPERATIONS = (
    ("POST", "/suite-api/api/auth/token/acquire", "acquireToken"),
    (
        "GET",
        "/suite-api/api/fleet-management/iam/identity-providers/{idpConfigId}"
        "/ldap-directories",
        "getLdapDirectories",
    ),
    (
        "GET",
        "/suite-api/api/fleet-management/iam/identity-providers/{idpConfigId}"
        "/ldap-directories/{ldapDirectoryId}/sync-logs",
        "getLdapSyncLogs",
    ),
    (
        "GET",
        "/suite-api/api/fleet-management/iam/identity-providers/{idpConfigId}"
        "/ldap-directories/{ldapDirectoryId}/sync-logs/{syncLogId}",
        "getLdapSyncLogById",
    ),
    ("POST", "/suite-api/api/alerts/query", "queryAlert"),
    ("GET", "/suite-api/api/alerts/contributingsymptoms", "getAlertContributingSymptoms"),
    ("GET", "/suite-api/api/symptoms", "getSymptoms"),
    ("POST", "/suite-api/api/auth/token/release", "releaseToken"),
)

TOKEN_PREFIX = "vRealizeOpsToken "


class ContractError(AssertionError):
    """The contract no longer describes the API this mock was built for."""


def _load_routes():
    """Derive the served route table from the pinned contract."""

    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    base_path = contract["x-wire-rules"]["server_base_path"]
    if base_path != contract["servers"][0]["url"]:
        raise ContractError("contract base path disagrees with servers[0].url")

    routes = []
    for path, path_item in contract["paths"].items():
        for method, operation in path_item.items():
            if not isinstance(operation, dict) or "operationId" not in operation:
                continue
            declared = [p["name"] for p in operation.get("parameters", [])]
            query_names = {
                p["name"] for p in operation.get("parameters", []) if p["in"] == "query"
            }
            required_query = {
                p["name"]
                for p in operation.get("parameters", [])
                if p["in"] == "query" and p.get("required")
            }
            template = base_path + path
            pattern = re.compile(
                re.sub(r"\\\{(\w+)\\\}", r"(?P<\1>[^/]+)", re.escape(template))
            )
            routes.append(
                {
                    "operationId": operation["operationId"],
                    "method": method.upper(),
                    "template": template,
                    "pattern": pattern,
                    "parameter_order": declared,
                    "query_names": query_names,
                    "required_query": required_query,
                    "has_body": "requestBody" in operation,
                    "authenticated": operation.get("security") != [],
                }
            )

    served = tuple((r["method"], r["template"], r["operationId"]) for r in routes)
    if served != EXPECTED_OPERATIONS:
        raise ContractError(
            "contract operation set changed: {!r}".format(served)
        )
    return routes


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _error(status, code, message):
    return status, _json_bytes({"errorCode": code, "message": message})


class _Appliance:
    """Simulated appliance state plus the on-disk request log."""

    def __init__(
        self,
        log_path,
        *,
        fail_operation=None,
        fail_status=500,
        invalid_json_operation=None,
        invalid_shape_operation=None,
    ):
        self.routes = _load_routes()
        self.log_path = Path(log_path)
        self.log_path.write_bytes(b"")
        self.fail_operation = fail_operation
        self.fail_status = fail_status
        self.invalid_json_operation = invalid_json_operation
        self.invalid_shape_operation = invalid_shape_operation
        self.lock = threading.Lock()
        self.issued_token = None
        self.token_released = False

    # -- request log --------------------------------------------------------

    def record(self, entry):
        line = json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
        with self.lock:
            with open(self.log_path, "a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())

    def read_log(self):
        text = self.log_path.read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    # -- routing ------------------------------------------------------------

    def match(self, method, path):
        for route in self.routes:
            if route["method"] != method:
                continue
            found = route["pattern"].fullmatch(path)
            if found:
                return route, {k: unquote(v) for k, v in found.groupdict().items()}
        return None, {}

    def handle(self, method, target, header_pairs, body_bytes):
        """Serve one request and append it to the log. Returns (status, body)."""

        split = urlsplit(target)
        route, path_params = self.match(method, split.path)
        headers = {}
        for name, value in header_pairs:
            headers.setdefault(name, []).append(value)

        body_text = None
        body = None
        if body_bytes:
            try:
                body_text = body_bytes.decode("utf-8")
                body = json.loads(body_text)
            except (UnicodeDecodeError, ValueError):
                body = None

        entry = {
            "operationId": route["operationId"] if route else None,
            "method": method,
            "target": target,
            "path": split.path,
            "query": split.query,
            "header_pairs": [list(pair) for pair in header_pairs],
            "body_text": body_text,
            "body": body,
        }

        status, payload = self._respond(route, path_params, split, headers, body)
        entry["status"] = status
        self.record(entry)
        return status, payload

    def _respond(self, route, path_params, split, headers, body):
        if route is None:
            return _error(404, "NOT_FOUND", "operation is not named by the contract")

        accept = headers.get("accept", [])
        if accept != ["application/json"]:
            return _error(406, "NOT_ACCEPTABLE", "exactly one JSON Accept header required")

        content_type = headers.get("content-type", [])
        if route["has_body"]:
            if content_type != ["application/json"]:
                return _error(415, "UNSUPPORTED_MEDIA_TYPE", "application/json required")
            if not isinstance(body, dict):
                return _error(400, "BAD_REQUEST", "JSON object body required")
        elif content_type:
            return _error(400, "BAD_REQUEST", "Content-Type sent on a request with no body")

        if route["authenticated"]:
            authorization = headers.get("authorization", [])
            if len(authorization) != 1:
                return _error(401, "UNAUTHORIZED", "exactly one Authorization header required")
            with self.lock:
                expected = self.issued_token
            if expected is None or authorization[0] != TOKEN_PREFIX + expected:
                return _error(401, "UNAUTHORIZED", "invalid session token")
        elif headers.get("authorization"):
            return _error(400, "BAD_REQUEST", "Authorization sent on an unsecured operation")

        query = parse_qsl(split.query, keep_blank_values=True)
        unknown = [name for name, _ in query if name not in route["query_names"]]
        if unknown:
            return _error(400, "BAD_REQUEST", "unknown query parameter(s): {}".format(unknown))
        blank = [name for name, value in query if value == ""]
        if blank:
            return _error(400, "BAD_REQUEST", "blank query parameter(s): {}".format(blank))
        missing = route["required_query"] - {name for name, _ in query}
        if missing:
            return _error(400, "BAD_REQUEST", "missing query parameter(s): {}".format(sorted(missing)))

        if route["operationId"] == self.fail_operation:
            return _error(self.fail_status, "SERVER_ERROR", "injected failure")
        if route["operationId"] == self.invalid_json_operation:
            return 200, b"{not-json"
        if route["operationId"] == self.invalid_shape_operation:
            return 200, _json_bytes(
                {
                    "pageInfo": {"page": 0, "pageSize": 5, "totalCount": 1},
                    "syncLogs": [None],
                }
            )

        handler = getattr(self, "_op_" + route["operationId"])
        return handler(path_params, dict_of(query), body)

    # -- operations ---------------------------------------------------------

    def _op_acquireToken(self, path_params, query, body):
        if set(body) - {"authSource", "password", "username"}:
            return _error(400, "BAD_REQUEST", "unknown property in username-password")
        if not body.get("username") or not body.get("password"):
            return 401, _json_bytes({"errorCode": "UNAUTHORIZED", "message": "bad credentials"})
        with self.lock:
            self.issued_token = fixtures.ISSUED_TOKEN
            self.token_released = False
            token = self.issued_token
        return 200, _json_bytes(
            {
                "expiresAt": "2026-03-20T06:00:00.000Z",
                "roles": ["ContentAdmin", "ReadOnly"],
                "token": token,
                "validity": 1774245600000,
            }
        )

    def _op_releaseToken(self, path_params, query, body):
        with self.lock:
            self.issued_token = None
            self.token_released = True
        return 200, b""

    def _op_getLdapDirectories(self, path_params, query, body):
        if path_params["idpConfigId"] != fixtures.IDP_CONFIG_ID:
            return _error(404, "NOT_FOUND", "no such identity provider configuration")
        return 200, _json_bytes({"ldapDirectories": fixtures.LDAP_DIRECTORIES})

    def _op_getLdapSyncLogs(self, path_params, query, body):
        if path_params["idpConfigId"] != fixtures.IDP_CONFIG_ID:
            return _error(404, "NOT_FOUND", "no such identity provider configuration")
        directory = path_params["ldapDirectoryId"]
        known = {d["ldapConfigurationId"] for d in fixtures.LDAP_DIRECTORIES}
        if directory not in known:
            return _error(404, "NOT_FOUND", "no such LDAP directory")

        logs = fixtures.SYNC_LOGS if directory == fixtures.FAILED_DIRECTORY_ID else []
        page = int(query.get("page", ["0"])[0])
        page_size = int(query.get("pageSize", ["50"])[0])
        if page < 0 or page_size < 1:
            return _error(400, "BAD_REQUEST", "invalid page or pageSize")
        if query.get("last", ["false"])[0] == "true":
            window = logs[:1]
        else:
            start = page * page_size
            window = logs[start:start + page_size]
        summaries = [
            {k: v for k, v in entry.items() if k not in fixtures.SYNC_LOG_DETAIL_ONLY_KEYS}
            for entry in window
        ]
        return 200, _json_bytes(
            {
                "pageInfo": {"page": page, "pageSize": page_size, "totalCount": len(logs)},
                "syncLogs": summaries,
            }
        )

    def _op_getLdapSyncLogById(self, path_params, query, body):
        if path_params["idpConfigId"] != fixtures.IDP_CONFIG_ID:
            return _error(404, "NOT_FOUND", "no such identity provider configuration")
        if path_params["ldapDirectoryId"] != fixtures.FAILED_DIRECTORY_ID:
            return _error(404, "NOT_FOUND", "no such LDAP directory")
        for entry in fixtures.SYNC_LOGS:
            if entry["id"] == path_params["syncLogId"]:
                return 200, _json_bytes(entry)
        return _error(404, "NOT_FOUND", "no such synchronization execution")

    def _op_queryAlert(self, path_params, query, body):
        if set(body) - {"activeOnly", "alertCriticality", "alertName", "alertStatus"}:
            return _error(400, "BAD_REQUEST", "unknown property in alert-query")
        selected = list(fixtures.ALERTS)
        if body.get("activeOnly") is True:
            selected = [a for a in selected if a["status"] in ("NEW", "ACTIVE", "UPDATED")]
        criticality = body.get("alertCriticality")
        if criticality is not None:
            if not isinstance(criticality, list):
                return _error(400, "BAD_REQUEST", "alertCriticality must be an array")
            selected = [a for a in selected if a["alertLevel"] in criticality]
        name = body.get("alertName")
        if name is not None:
            selected = [a for a in selected if name in a["alertDefinitionName"]]
        return 200, _json_bytes(
            {
                "alerts": selected,
                "pageInfo": {"page": 0, "pageSize": 1000, "totalCount": len(selected)},
            }
        )

    def _op_getAlertContributingSymptoms(self, path_params, query, body):
        requested = query.get("id", [])
        unknown = [i for i in requested if i not in fixtures.CONTRIBUTING_SYMPTOMS]
        if unknown:
            return _error(500, "SERVER_ERROR", "unknown alert identifier(s): {}".format(unknown))
        return 200, _json_bytes(
            {
                "contributingSymptoms": [
                    {
                        "alertId": alert_id,
                        "contributingSymptoms": {
                            "contributingSymptoms": fixtures.CONTRIBUTING_SYMPTOMS[alert_id]
                        },
                    }
                    for alert_id in requested
                ]
            }
        )

    def _op_getSymptoms(self, path_params, query, body):
        resource_ids = query.get("resourceId", [])
        active_only = query.get("activeOnly", ["true"])[0] == "true"
        include_alarm_info = query.get("includeAlarmInfo", ["false"])[0] == "true"

        selected = fixtures.SYMPTOMS
        if resource_ids:
            selected = [s for s in selected if s["resourceId"] in resource_ids]
        if active_only:
            selected = [s for s in selected if s["active"]]

        drop = set(fixtures.SYMPTOM_INTERNAL_KEYS)
        if not include_alarm_info:
            drop.add("alarmInfo")
        payload = [{k: v for k, v in s.items() if k not in drop} for s in selected]
        return 200, _json_bytes(
            {
                "pageInfo": {"page": 0, "pageSize": 1000, "totalCount": len(payload)},
                "symptom": payload,
            }
        )


def dict_of(pairs):
    """Group ``(name, value)`` query pairs into ``{name: [values]}``."""

    grouped = {}
    for name, value in pairs:
        grouped.setdefault(name, []).append(value)
    return grouped


class _Server(ThreadingHTTPServer):
    daemon_threads = True


class LoopbackOperationsMock:
    """Bind the contract-derived handler to an ephemeral 127.0.0.1 port."""

    def __init__(
        self,
        log_path,
        *,
        fail_operation=None,
        invalid_json_operation=None,
        invalid_shape_operation=None,
    ):
        self.appliance = _Appliance(
            log_path,
            fail_operation=fail_operation,
            invalid_json_operation=invalid_json_operation,
            invalid_shape_operation=invalid_shape_operation,
        )
        self._server = None
        self._thread = None

    @property
    def base_url(self):
        host, port = self._server.server_address
        return "http://{}:{}".format(host, port)

    def read_log(self):
        return self.appliance.read_log()

    @property
    def token_released(self):
        return self.appliance.token_released

    def probe(self, method, target, header_pairs, body=b""):
        """Send a request straight at the handler, bypassing the client."""

        return self.appliance.handle(method, target, header_pairs, body)

    def __enter__(self):
        appliance = self.appliance

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format_string, *args):
                return

            def _serve(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                pairs = [(name.lower(), value) for name, value in self.headers.items()]
                status, payload = appliance.handle(self.command, self.path, pairs, raw)
                self.send_response(status)
                if payload:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if payload:
                    self.wfile.write(payload)

            do_GET = _serve
            do_POST = _serve
            do_PUT = _serve
            do_DELETE = _serve
            do_PATCH = _serve

        self._server = _Server(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="vcf-operations-contract-mock",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


class RequestLevelOperationsMock:
    """Same handler, reached by patching the client's ``urlopen``.

    Used only where the sandbox forbids creating a loopback socket.
    """

    base_url = "http://127.0.0.1:8443"

    def __init__(
        self,
        log_path,
        *,
        fail_operation=None,
        invalid_json_operation=None,
        invalid_shape_operation=None,
    ):
        self.appliance = _Appliance(
            log_path,
            fail_operation=fail_operation,
            invalid_json_operation=invalid_json_operation,
            invalid_shape_operation=invalid_shape_operation,
        )

    def read_log(self):
        return self.appliance.read_log()

    @property
    def token_released(self):
        return self.appliance.token_released

    def probe(self, method, target, header_pairs, body=b""):
        return self.appliance.handle(method, target, header_pairs, body)

    def urlopen(self, request, timeout=None):
        del timeout
        split = urlsplit(request.full_url)
        target = split.path + ("?" + split.query if split.query else "")
        pairs = [(name.lower(), value) for name, value in request.header_items()]
        body = request.data or b""
        status, payload = self.appliance.handle(request.get_method(), target, pairs, body)
        if status >= 400:
            raise HTTPError(
                request.full_url,
                status,
                "error",
                {"Content-Type": "application/json"},
                io.BytesIO(payload),
            )

        class Response:
            def __init__(self):
                self.status = status
                self.code = status

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def read(self):
                return payload

            def getcode(self):
                return status

        return Response()


@contextmanager
def contract_mock(
    log_path,
    *,
    fail_operation=None,
    invalid_json_operation=None,
    invalid_shape_operation=None,
):
    """Prefer a real loopback listener; fall back to request-level dispatch."""

    loopback = LoopbackOperationsMock(
        log_path,
        fail_operation=fail_operation,
        invalid_json_operation=invalid_json_operation,
        invalid_shape_operation=invalid_shape_operation,
    )
    try:
        entered = loopback.__enter__()
    except (PermissionError, OSError):
        fallback = RequestLevelOperationsMock(
            log_path,
            fail_operation=fail_operation,
            invalid_json_operation=invalid_json_operation,
            invalid_shape_operation=invalid_shape_operation,
        )
        with patch("vcfops_triage.client.urlopen", fallback.urlopen, create=True):
            yield fallback
    else:
        try:
            yield entered
        finally:
            loopback.__exit__(None, None, None)
