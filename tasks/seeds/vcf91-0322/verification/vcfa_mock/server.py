"""A loopback mock of the VCF Automation (VCF 9.1) deployment APIs.

The mock is pinned to ``docs/contract.json``: it builds its routing table from the
operations the contract names and serves nothing else. A request that does not match
a contract operation gets 404, and a request that carries a query parameter or a JSON
body field the contract does not declare for that operation gets 400.

Every request is appended to a JSON Lines request log so a test can inspect the exact
wire shape that a client produced.

The server binds to 127.0.0.1 only. It never contacts a VMware endpoint.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

CONTRACT_PATH = os.path.join(_ROOT, "docs", "contract.json")
FIXTURES_PATH = os.path.join(_HERE, "fixtures.json")

_PLACEHOLDER = re.compile(r"^\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)\}$")


def load_contract(path: str = CONTRACT_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def load_fixtures(path: str = FIXTURES_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


class _Route:
    __slots__ = ("operation", "method", "segments", "wildcards")

    def __init__(self, operation: dict) -> None:
        self.operation = operation
        self.method = operation["method"].upper()
        self.segments = operation["path_template"].strip("/").split("/")
        self.wildcards = sum(1 for seg in self.segments if _PLACEHOLDER.match(seg))

    def match(self, path: str):
        parts = path.strip("/").split("/")
        if len(parts) != len(self.segments):
            return None
        captured = {}
        for actual, template in zip(parts, self.segments):
            placeholder = _PLACEHOLDER.match(template)
            if placeholder:
                if not actual:
                    return None
                captured[placeholder.group("name")] = actual
            elif actual != template:
                return None
        return captured


def _page(items, page: int, size: int, *, totals: bool = True) -> dict:
    start = page * size
    window = items[start : start + size]
    total_pages = (len(items) + size - 1) // size if size else 0
    body = {
        "content": window,
        "empty": not window,
        "first": page == 0,
        "last": start + size >= len(items),
        "number": page,
        "numberOfElements": len(window),
        "size": size,
    }
    if totals:
        body["totalElements"] = len(items)
        body["totalPages"] = total_pages
    return body


def _as_int(values, default):
    if not values:
        return default
    try:
        return int(values[0])
    except (TypeError, ValueError):
        return default


class _State:
    """Mutable service state, seeded from the fixtures on every server start."""

    def __init__(self, fixtures: dict) -> None:
        self.fixtures = fixtures
        self.deployments = [dict(d) for d in fixtures["deployments"]]
        self.requests = {k: dict(v) for k, v in fixtures["requests"].items()}
        self.requests_by_deployment = {
            k: [dict(r) for r in v] for k, v in fixtures["requests_by_deployment"].items()
        }
        self.events_by_request = fixtures["events_by_request"]
        self.logs_by_event = fixtures["logs_by_event"]
        self.resources_by_deployment = fixtures["resources_by_deployment"]
        self.access_token = fixtures["access_token"]
        self.lock = threading.Lock()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "vcfa-mock/1.0"

    # -- plumbing ---------------------------------------------------------

    def log_message(self, fmt, *args):  # silence stderr access logging
        return

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

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def _send(self, status: int, payload):
        if payload is None:
            raw = b""
        else:
            raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        if raw:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        if raw:
            self.wfile.write(raw)
        return status

    # -- dispatch ---------------------------------------------------------

    def _dispatch(self, method: str):
        mock = self.server.mock
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        body = self._read_body()
        headers = {k.lower(): v for k, v in self.headers.items()}

        route, captured = mock.resolve(method, parsed.path)
        operation_id = route.operation["id"] if route else None
        status = 500
        try:
            if route is None:
                status = self._send(
                    404,
                    {
                        "error": "operation_not_in_contract",
                        "message": (
                            "No operation in docs/contract.json matches "
                            f"{method} {parsed.path}."
                        ),
                    },
                )
            else:
                status = self._handle(route, captured, query, headers, body)
        finally:
            mock.record(
                {
                    "method": method,
                    "path": parsed.path,
                    "raw_query": parsed.query,
                    "query": query,
                    "headers": headers,
                    "body": body.decode("utf-8", errors="replace"),
                    "operation_id": operation_id,
                    "status": status,
                }
            )

    def _handle(self, route, captured, query, headers, body):
        operation = route.operation
        declared = {p["name"] for p in operation.get("query_parameters") or []}
        for name in query:
            if name not in declared:
                return self._send(
                    400,
                    {
                        "error": "undeclared_query_parameter",
                        "operationId": operation["id"],
                        "parameter": name,
                        "declared": sorted(declared),
                    },
                )
        for spec in operation.get("query_parameters") or []:
            if spec.get("required") and spec["name"] not in query:
                return self._send(
                    400,
                    {
                        "error": "missing_required_query_parameter",
                        "operationId": operation["id"],
                        "parameter": spec["name"],
                    },
                )
            allowed = spec.get("enum")
            if allowed and spec["name"] in query:
                for value in query[spec["name"]]:
                    if value not in allowed:
                        return self._send(
                            400,
                            {
                                "error": "invalid_query_parameter_value",
                                "operationId": operation["id"],
                                "parameter": spec["name"],
                                "allowed": allowed,
                            },
                        )

        if operation["auth"] == "bearer":
            expected = "Bearer " + self.server.mock.state.access_token
            if headers.get("authorization") != expected:
                return self._send(
                    401, {"error": "unauthorized", "message": "Missing or invalid bearer token."}
                )
        elif "Authorization" in (operation.get("request_headers") or {}).get("forbidden", []):
            if "authorization" in headers:
                return self._send(
                    400,
                    {
                        "error": "forbidden_request_header",
                        "operationId": operation["id"],
                        "header": "Authorization",
                    },
                )

        handler = getattr(self, "_op_" + operation["id"].replace(".", "_"))
        return handler(captured, query, headers, body)

    def _json_body(self, operation, body):
        """Parse and contract-check a JSON body. Returns (payload, error_status)."""
        spec = operation.get("request_body") or {}
        if spec.get("encoding") != "application/json":
            return None, None
        if not body:
            return {}, None
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None, self._send(400, {"error": "malformed_json_body"})
        if not isinstance(payload, dict):
            return None, self._send(400, {"error": "json_body_must_be_an_object"})
        declared = {f["name"] for f in spec.get("fields") or []}
        for key in payload:
            if key not in declared:
                return None, self._send(
                    400,
                    {
                        "error": "undeclared_body_field",
                        "operationId": operation["id"],
                        "field": key,
                        "declared": sorted(declared),
                    },
                )
        return payload, None

    # -- operations -------------------------------------------------------

    def _op_auth_token_exchange(self, captured, query, headers, body):
        state = self.server.mock.state
        fixtures = state.fixtures
        content_type = (headers.get("content-type") or "").split(";")[0].strip()
        if content_type != "application/x-www-form-urlencoded":
            return self._send(
                400,
                {
                    "error": "unsupported_content_type",
                    "expected": "application/x-www-form-urlencoded",
                    "received": content_type,
                },
            )
        form = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
        for key in form:
            if key not in ("grant_type", "refresh_token"):
                return self._send(400, {"error": "undeclared_body_field", "field": key})
        if form.get("grant_type", [None])[0] != "refresh_token":
            return self._send(400, {"error": "unsupported_grant_type"})
        if captured.get("tenant") != fixtures["tenant"]:
            return self._send(404, {"error": "unknown_tenant", "tenant": captured.get("tenant")})
        if form.get("refresh_token", [None])[0] != fixtures["api_token"]:
            return self._send(401, {"error": "invalid_grant", "message": "Unknown API token."})
        return self._send(
            200,
            {
                "access_token": state.access_token,
                "token_type": fixtures["token_type"],
                "expires_in": fixtures["expires_in"],
            },
        )

    def _op_deployments_list(self, captured, query, headers, body):
        state = self.server.mock.state
        items = list(state.deployments)

        if "name" in query:
            wanted = set(query["name"])
            items = [d for d in items if d.get("name") in wanted]
        if "ids" in query:
            wanted = {i for value in query["ids"] for i in value.split(",") if i}
            items = [d for d in items if d.get("id") in wanted]
        if "status" in query:
            wanted = {s for value in query["status"] for s in value.split(",") if s}
            items = [d for d in items if d.get("status") in wanted]
        if "projects" in query:
            wanted = {p for value in query["projects"] for p in value.split(",") if p}
            items = [d for d in items if d.get("projectId") in wanted]
        if "search" in query:
            needle = (query["search"][0] or "").lower()
            items = [
                d
                for d in items
                if needle in (d.get("name") or "").lower()
                or needle in (d.get("description") or "").lower()
            ]
        if "deleted" in query and query["deleted"][0].lower() == "true":
            items = [d for d in items if d.get("deleted")]

        return self._send(200, _page(items, _as_int(query.get("page"), 0), _as_int(query.get("size"), 20)))

    def _op_deployments_requests_list(self, captured, query, headers, body):
        state = self.server.mock.state
        deployment_id = captured["deploymentId"]
        if deployment_id not in state.requests_by_deployment:
            return self._send(404, {"error": "deployment_not_found", "id": deployment_id})
        items = list(state.requests_by_deployment[deployment_id])
        if "search" in query:
            needle = (query["search"][0] or "").lower()
            items = [r for r in items if needle in (r.get("name") or "").lower()]
        if "inprogressRequests" in query and query["inprogressRequests"][0].lower() == "true":
            items = [r for r in items if r.get("status") == "INPROGRESS"]
        return self._send(200, _page(items, _as_int(query.get("page"), 0), _as_int(query.get("size"), 20)))

    def _op_requests_get(self, captured, query, headers, body):
        state = self.server.mock.state
        record = state.requests.get(captured["requestId"])
        if record is None:
            return self._send(404, {"error": "request_not_found", "id": captured["requestId"]})
        return self._send(200, record)

    def _op_requests_events_list(self, captured, query, headers, body):
        state = self.server.mock.state
        request_id = captured["requestId"]
        if request_id not in state.requests:
            return self._send(404, {"error": "request_not_found", "id": request_id})
        events = state.events_by_request.get(request_id, [])
        return self._send(
            200, _page(events, _as_int(query.get("page"), 0), _as_int(query.get("size"), 20))
        )

    def _op_requests_events_logs_get(self, captured, query, headers, body):
        state = self.server.mock.state
        request_id = captured["requestId"]
        event_id = captured["eventId"]
        events = {e["id"]: e for e in state.events_by_request.get(request_id, [])}
        event = events.get(event_id)
        if event is None:
            return self._send(404, {"error": "event_not_found", "id": event_id})
        if not event.get("hasLogs"):
            return self._send(
                404,
                {
                    "error": "event_has_no_logs",
                    "id": event_id,
                    "message": "This event reports hasLogs false and carries no log.",
                },
            )
        entries = list(state.logs_by_event.get(event_id, []))
        if "sinceRow" in query:
            since = _as_int(query.get("sinceRow"), 0)
            entries = [e for e in entries if e["rownum"] >= since]
        slice_body = _page(entries, 0, max(len(entries), 1), totals=False)
        slice_body["pageable"] = {"offset": 0, "pageNumber": 0, "pageSize": slice_body["size"]}
        slice_body["sort"] = {"sorted": True, "unsorted": False, "empty": False}
        return self._send(200, slice_body)

    def _op_deployments_resources_list(self, captured, query, headers, body):
        state = self.server.mock.state
        deployment_id = captured["deploymentId"]
        if deployment_id not in state.resources_by_deployment:
            return self._send(404, {"error": "deployment_not_found", "id": deployment_id})
        items = list(state.resources_by_deployment[deployment_id])
        if "names" in query:
            wanted = {n for value in query["names"] for n in value.split(",") if n}
            items = [r for r in items if r.get("name") in wanted]
        if "resourceTypes" in query:
            wanted = {t for value in query["resourceTypes"] for t in value.split(",") if t}
            items = [r for r in items if r.get("type") in wanted]
        items.sort(key=lambda r: (r.get("type") or "", r.get("name") or ""))
        return self._send(
            200, _page(items, _as_int(query.get("page"), 0), _as_int(query.get("size"), 20))
        )

    def _op_requests_action(self, captured, query, headers, body):
        state = self.server.mock.state
        request_id = captured["requestId"]
        action = query["action"][0]
        with state.lock:
            record = state.requests.get(request_id)
            if record is None:
                return self._send(404, {"error": "request_not_found", "id": request_id})
            if action == "dismiss":
                if record.get("status") != "FAILED":
                    return self._send(
                        409,
                        {
                            "error": "request_not_dismissible",
                            "message": "dismiss applies only to failed requests.",
                            "status": record.get("status"),
                        },
                    )
                if record.get("dismissed"):
                    return self._send(409, {"error": "request_already_dismissed"})
                record["dismissed"] = True
                for listed in state.requests_by_deployment.get(record["deploymentId"], []):
                    if listed["id"] == request_id:
                        listed["dismissed"] = True
            else:  # cancel
                if record.get("status") != "INPROGRESS":
                    return self._send(
                        409,
                        {
                            "error": "request_not_cancelable",
                            "message": "cancel applies only to in-progress requests.",
                            "status": record.get("status"),
                        },
                    )
                record["status"] = "ABORTED"
        return self._send(200, None)

    def _op_deployments_requests_submitAction(self, captured, query, headers, body):
        state = self.server.mock.state
        operation = self.server.mock.operations["deployments.requests.submitAction"]
        payload, error = self._json_body(operation, body)
        if error is not None:
            return error
        content_type = (headers.get("content-type") or "").split(";")[0].strip()
        if content_type != "application/json":
            return self._send(
                400,
                {
                    "error": "unsupported_content_type",
                    "expected": "application/json",
                    "received": content_type,
                },
            )
        deployment_id = captured["deploymentId"]
        deployment = next((d for d in state.deployments if d["id"] == deployment_id), None)
        if deployment is None:
            return self._send(404, {"error": "deployment_not_found", "id": deployment_id})
        action_id = payload.get("actionId")
        if not action_id:
            return self._send(400, {"error": "action_id_required"})

        with state.lock:
            new_id = state.fixtures["new_request_id"]
            record = {
                "id": new_id,
                "name": action_id.split(".")[-1],
                "deploymentId": deployment_id,
                "actionId": action_id,
                "status": "INPROGRESS",
                "details": f"{action_id} submitted.",
                "requestedBy": "rmalik@corp.example.com",
                "createdAt": "2026-08-11T08:04:27.611Z",
                "updatedAt": "2026-08-11T08:04:27.611Z",
                "totalTasks": 5,
                "completedTasks": 0,
                "cancelable": True,
                "dismissed": False,
                "resourceIds": [],
            }
            if "inputs" in payload:
                record["inputs"] = payload["inputs"]
            if "reason" in payload:
                record["details"] = str(payload["reason"])
            state.requests[new_id] = record
            state.requests_by_deployment.setdefault(deployment_id, []).insert(0, dict(record))
        return self._send(200, record)


class _Server(HTTPServer):
    # The client workflow is deliberately sequential. Serving one request at a time
    # makes the JSONL sequence reflect arrival order even though a response is flushed
    # just before the completed request is appended to the log.
    allow_reuse_address = True


class MockVcfAutomation:
    """A contract-pinned loopback stand-in for a VCF Automation appliance."""

    def __init__(self, log_path: str, contract_path: str = CONTRACT_PATH,
                 fixtures_path: str = FIXTURES_PATH, host: str = "127.0.0.1", port: int = 0):
        self.contract = load_contract(contract_path)
        self.state = _State(load_fixtures(fixtures_path))
        self.operations = {op["id"]: op for op in self.contract["operations"]}
        self.routes = [_Route(op) for op in self.contract["operations"]]
        self.log_path = os.path.abspath(log_path)
        self._host = host
        self._port = port
        self._seq = 0
        self._log_lock = threading.Lock()
        self._httpd = None
        self._thread = None

    # -- lifecycle --------------------------------------------------------

    def start(self) -> str:
        directory = os.path.dirname(self.log_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.log_path, "w", encoding="utf-8"):
            pass
        self._httpd = _Server((self._host, self._port), _Handler)
        self._httpd.mock = self
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self.base_url

    def stop(self) -> None:
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

    @property
    def base_url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    # -- routing and logging ---------------------------------------------

    def resolve(self, method: str, path: str):
        best = None
        best_captured = None
        for route in self.routes:
            if route.method != method.upper():
                continue
            captured = route.match(path)
            if captured is None:
                continue
            if best is None or route.wildcards < best.wildcards:
                best, best_captured = route, captured
        return best, best_captured

    def record(self, entry: dict) -> None:
        with self._log_lock:
            self._seq += 1
            entry = dict(entry)
            entry["seq"] = self._seq
            with open(self.log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")

    def records(self):
        return read_request_log(self.log_path)


def read_request_log(path: str):
    entries = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    entries.sort(key=lambda e: e["seq"])
    return entries


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="0 picks a free port.")
    parser.add_argument("--log", default="mock-requests.jsonl", help="Request log path.")
    args = parser.parse_args(argv)

    mock = MockVcfAutomation(log_path=args.log, host=args.host, port=args.port)
    base_url = mock.start()
    print(
        json.dumps(
            {
                "base_url": base_url,
                "tenant": mock.state.fixtures["tenant"],
                "api_token": mock.state.fixtures["api_token"],
                "log": mock.log_path,
            }
        ),
        flush=True,
    )
    try:
        while True:
            mock._thread.join(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        mock.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
