"""Loopback mock for the VCF 9.1 SDDC LCM task operations.

The server is *pinned to the contract the solution derived*: it reads
``docs/contract.json``, builds its route table, its query-parameter validation and
its authentication check from the operations named there, and serves nothing else.
A request that does not match a contract-declared route is a miss.

Every request is recorded verbatim -- raw query string included, before any
decoding -- so the test can assert the exact wire shape.

This file is protected. Read it, run it, but do not modify it.
"""

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "task_pages.json"

BEARER_TOKEN = "eyJhbGciOiJIUzI1NiJ9.sddc-lcm-test-token.signature"

# The two operations this mock knows how to answer. The contract must name
# exactly these; anything else it names has no handler and is a configuration
# error, and anything it fails to name is simply not routed.
SUPPORTED = ("getTasks", "getTask")


class ContractError(RuntimeError):
    """The derived contract cannot drive the mock."""


def _load_fixtures():
    with FIXTURES.open(encoding="utf-8") as fh:
        return json.load(fh)


def _template_to_regex(path_template):
    """Turn ``/v1/tasks/{taskId}`` into a compiled regex with named groups."""
    parts = []
    for chunk in re.split(r"(\{[A-Za-z_][A-Za-z0-9_]*\})", path_template):
        if chunk.startswith("{") and chunk.endswith("}"):
            parts.append("(?P<%s>[^/]+)" % chunk[1:-1])
        else:
            parts.append(re.escape(chunk))
    return re.compile("^" + "".join(parts) + "$")


class ContractRoutes:
    """Route table + validation rules built from ``docs/contract.json``."""

    def __init__(self, contract):
        if not isinstance(contract, dict):
            raise ContractError("contract.json must contain a JSON object")
        operations = contract.get("operations")
        if not isinstance(operations, dict) or not operations:
            raise ContractError("contract.json has no 'operations' object")

        named = sorted(operations)
        if named != sorted(SUPPORTED):
            raise ContractError(
                "contract names operations %s; this mock serves exactly %s"
                % (named, sorted(SUPPORTED))
            )

        self.routes = []
        self.by_id = {}
        for operation_id in SUPPORTED:
            op = operations[operation_id]
            if not isinstance(op, dict):
                raise ContractError("operation %r is not an object" % operation_id)

            method = op.get("method")
            path = op.get("path")
            if not isinstance(method, str) or not method:
                raise ContractError("operation %r has no 'method'" % operation_id)
            if not isinstance(path, str) or not path.startswith("/"):
                raise ContractError("operation %r has no usable 'path'" % operation_id)

            params = op.get("parameters")
            if not isinstance(params, list):
                raise ContractError("operation %r has no 'parameters' list" % operation_id)

            query_names, path_names = [], []
            for param in params:
                if not isinstance(param, dict):
                    raise ContractError("a parameter of %r is not an object" % operation_id)
                where, name = param.get("in"), param.get("name")
                if not isinstance(name, str) or not name:
                    raise ContractError("a parameter of %r has no 'name'" % operation_id)
                if where == "query":
                    query_names.append(name)
                elif where == "path":
                    path_names.append(name)

            record = {
                "operation_id": operation_id,
                "method": method.upper(),
                "path": path,
                "regex": _template_to_regex(path),
                "query_names": query_names,
                "path_names": path_names,
                "security": op.get("security") or [],
            }
            self.by_id[operation_id] = record
            self.routes.append(record)

        # Longer literal prefixes first so /v1/tasks/{taskId} is tried before
        # /v1/tasks cannot accidentally swallow it (they differ in segments, but
        # ordering keeps the match deterministic).
        self.routes.sort(key=lambda r: (-r["path"].count("/"), r["path"]))

    def match(self, method, path):
        for record in self.routes:
            if record["method"] != method.upper():
                continue
            found = record["regex"].match(path)
            if found:
                return record, found.groupdict()
        return None, None


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "SddcLcmMock/1.0"

    # -- plumbing ---------------------------------------------------------
    def log_message(self, *_args):
        pass

    def _send(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status, code, message):
        self._send(
            status,
            {
                "code": code,
                "message": {"id": code, "defaultMessage": message, "localizedMessage": message},
                "referenceId": "mock-%d" % status,
            },
        )

    # -- verbs ------------------------------------------------------------
    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")

    # -- dispatch ---------------------------------------------------------
    def _dispatch(self, method):
        split = urlsplit(self.path)
        raw_query = split.query
        length = int(self.headers.get("Content-Length") or 0)
        raw_body = self.rfile.read(length) if length else b""

        entry = {
            "method": method,
            "target": self.path,
            "path": split.path,
            "raw_query": raw_query,
            # keep_blank_values so a `status=` sent in error is *visible* rather
            # than silently dropped by the parser.
            "query_pairs": parse_qsl(raw_query, keep_blank_values=True),
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": raw_body.decode("utf-8", "replace"),
            "operation_id": None,
            "status": None,
        }

        try:
            self._route(method, split.path, entry)
        finally:
            self.server.request_log.append(entry)

    def _route(self, method, path, entry):
        record, path_values = self.server.routes.match(method, path)
        if record is None:
            entry["status"] = 404
            self._error(404, "NOT_FOUND", "no contract operation for %s %s" % (method, path))
            return

        entry["operation_id"] = record["operation_id"]

        if record["security"]:
            auth = self.headers.get("Authorization")
            if auth != "Bearer " + BEARER_TOKEN:
                entry["status"] = 401
                self._error(401, "UNAUTHORIZED", "missing or invalid bearer token")
                return

        pairs = entry["query_pairs"]
        declared = set(record["query_names"])
        unknown = [k for k, _ in pairs if k not in declared]
        if unknown:
            entry["status"] = 400
            self._error(400, "BAD_REQUEST", "undeclared query parameter(s): %s" % ",".join(unknown))
            return

        query = {k: v for k, v in pairs}
        handler = getattr(self, "_op_" + record["operation_id"])
        handler(query, path_values, entry)

    # -- operations -------------------------------------------------------
    def _op_getTasks(self, query, _path_values, entry):
        fixtures = self.server.fixtures
        elements = list(fixtures["wire_order"])

        # The mock honours only the paging parameters; filters are recorded for
        # wire assertions but do not reshape the fixture, so the expected result
        # set stays fixed and the test can reason about completeness.
        raw_size = query.get("pageSize")
        raw_number = query.get("pageNumber")

        try:
            page_size = int(raw_size) if raw_size is not None else 50
            page_number = int(raw_number) if raw_number is not None else 0
        except ValueError:
            entry["status"] = 400
            self._error(400, "BAD_REQUEST", "pageNumber and pageSize must be integers")
            return

        if page_size < 1 or page_size > 50:
            entry["status"] = 400
            self._error(400, "BAD_REQUEST", "pageSize must be between 1 and 50")
            return
        if page_number < 0:
            entry["status"] = 400
            self._error(400, "BAD_REQUEST", "pageNumber must not be negative")
            return

        total_elements = len(elements)
        total_pages = (total_elements + page_size - 1) // page_size
        start = page_number * page_size
        window = elements[start : start + page_size]

        entry["status"] = 200
        entry["page_number"] = page_number
        self._send(
            200,
            {
                "elements": window,
                "pageMetadata": {
                    "pageNumber": page_number,
                    "pageSize": page_size,
                    "totalElements": total_elements,
                    "totalPages": total_pages,
                },
            },
        )

    def _op_getTask(self, _query, path_values, entry):
        task_id = None
        for value in path_values.values():
            task_id = value
            break

        entry["task_id"] = task_id
        detail = self.server.fixtures["task_details"].get(task_id)
        if detail is None:
            entry["status"] = 404
            self._error(404, "NOT_FOUND", "no task %s" % task_id)
            return

        entry["status"] = 200
        self._send(200, detail)


class MockServer:
    """Context manager wrapping the pinned loopback server."""

    def __init__(self, contract_path=None):
        self.contract_path = Path(contract_path or (ROOT / "docs" / "contract.json"))
        self._httpd = None
        self._thread = None

    def __enter__(self):
        if not self.contract_path.is_file():
            raise ContractError("missing %s" % self.contract_path)
        try:
            with self.contract_path.open(encoding="utf-8") as fh:
                contract = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ContractError("%s is not valid JSON: %s" % (self.contract_path, exc)) from exc

        routes = ContractRoutes(contract)

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        httpd.daemon_threads = True
        httpd.routes = routes
        httpd.fixtures = _load_fixtures()
        httpd.request_log = []

        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05})
        thread.daemon = True
        thread.start()

        self._httpd = httpd
        self._thread = thread
        return self

    def __exit__(self, *_exc):
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        return False

    @property
    def base_url(self):
        host, port = self._httpd.server_address[:2]
        return "http://%s:%d" % (host, port)

    @property
    def log(self):
        return list(self._httpd.request_log)

    def reset_log(self):
        self._httpd.request_log.clear()
