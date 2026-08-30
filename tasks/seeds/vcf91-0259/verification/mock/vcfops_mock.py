#!/usr/bin/env python3
"""Loopback mock of the VCF Operations report API, pinned to docs/contract.json.

The route table is built from the ``operations`` block of the contract, so the
process serves exactly the operations the contract names and nothing else. Any
other path or method answers 404 and is recorded as an ``unknown_operation``
contract violation.

Every request -- accepted or rejected -- is appended to a JSON Lines request log
so a test can read back the exact wire shape that was sent.

Run it directly to develop against it::

    python3 mock/vcfops_mock.py --log /tmp/requests.jsonl

It binds 127.0.0.1 on an ephemeral port and prints a single readiness line::

    VCFOPS_MOCK_READY {"port": 41234, "baseUrl": "http://127.0.0.1:41234/suite-api"}

Standard library only. This file is part of the fixture and is not the subject
of the exercise; do not modify it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

CONTRACT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "contract.json"
)

# --------------------------------------------------------------------------
# Fixture data. Deterministic: no clock, no randomness feeds any response.
# --------------------------------------------------------------------------

VALID_USERNAME = "report-runner"
VALID_PASSWORD = "Fixture-Passw0rd!"
VALID_AUTH_SOURCE = "Local Users"

# Report definitions this fixture knows about, and how generation behaves for
# each. The poll index is 1-based and counts getReport calls for one report.
REPORT_DEFINITIONS = {
    "2f7a2f2a-0001-4a10-9f1a-9b0f0d5c1001": {
        "name": "VM Rightsizing - Cluster Summary",
        "outcome": "COMPLETED",
    },
    "2f7a2f2a-0002-4a10-9f1a-9b0f0d5c1002": {
        "name": "Capacity Reclamation - Broken Definition",
        "outcome": "FAILED",
    },
    "2f7a2f2a-0003-4a10-9f1a-9b0f0d5c1003": {
        "name": "Datastore Inventory - Never Finishes",
        "outcome": "STUCK",
    },
}

# Poll 1 -> QUEUED, poll 2 -> RUNNING, poll 3 and later -> the outcome.
# A STUCK definition stays RUNNING forever.
TERMINAL_AFTER_POLLS = 3

KNOWN_RESOURCES = {
    "8b1d4a76-2c33-4a5e-9f27-6a4f2c0b7e11": "cluster-prod-01",
    "3d9c7e21-5b48-4d19-8a63-1f7e5c9d0a22": "datastore-nfs-gold",
}


def report_payload(report_id: str, fmt: str) -> bytes:
    """The exact bytes downloadReport serves for a report, per format."""
    if fmt == "CSV":
        return (
            "Resource,Metric,Value\r\n"
            "vcf-operations-report,%s,1\r\n" % report_id
        ).encode("utf-8")
    return b"%PDF-1.4\n" + ("%% vcf-operations report %s\n" % report_id).encode("utf-8") + b"%%EOF\n"


# --------------------------------------------------------------------------
# Contract loading and route construction
# --------------------------------------------------------------------------


def load_contract(path: str = CONTRACT_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _path_regex(full_path: str) -> re.Pattern:
    parts = []
    for token in re.split(r"(\{[a-zA-Z0-9_]+\})", full_path):
        if token.startswith("{") and token.endswith("}"):
            parts.append("(?P<%s>[^/]+)" % token[1:-1])
        else:
            parts.append(re.escape(token))
    return re.compile("^" + "".join(parts) + "$")


class ContractRoutes:
    """Route table built from - and only from - the contract's operations."""

    def __init__(self, contract: dict, handlers: dict):
        self.contract = contract
        self.wire = contract["wire"]
        self.async_rules = contract["asyncPolling"]
        self.operations = contract["operations"]
        missing = sorted(set(self.operations) - set(handlers))
        extra = sorted(set(handlers) - set(self.operations))
        if missing or extra:
            raise SystemExit(
                "mock is not pinned to the contract: unhandled operations %s, "
                "handlers with no contract entry %s" % (missing, extra)
            )
        self.routes = []
        for op_id, op in self.operations.items():
            self.routes.append((op["method"], _path_regex(op["fullPath"]), op_id, op))

    def match(self, method: str, path: str):
        path_known = False
        for route_method, pattern, op_id, op in self.routes:
            found = pattern.match(path)
            if found:
                path_known = True
                if route_method == method:
                    return op_id, op, found.groupdict()
        return (None, None, {"__path_known__": path_known}) if path_known else (None, None, {})


# --------------------------------------------------------------------------
# Server state
# --------------------------------------------------------------------------


class MockState:
    def __init__(self):
        self.lock = threading.Lock()
        self.tokens = set()
        self.token_seq = 0
        self.report_seq = 0
        self.reports = {}

    def new_token(self) -> str:
        self.token_seq += 1
        token = "a1b2c3d4-0000-4000-8000-%012d" % self.token_seq
        self.tokens.add(token)
        return token

    def new_report(self, definition_id: str, body: dict) -> dict:
        self.report_seq += 1
        report_id = "b7e11f00-0000-4000-9000-%012d" % self.report_seq
        record = {
            "id": report_id,
            "reportDefinitionId": definition_id,
            "resourceId": body["resourceId"],
            "status": "QUEUED",
            "owner": VALID_USERNAME,
            "polls": 0,
            "outcome": REPORT_DEFINITIONS[definition_id]["outcome"],
        }
        for optional in ("name", "description", "subject", "publish"):
            if optional in body:
                record[optional] = body[optional]
        record.setdefault("name", REPORT_DEFINITIONS[definition_id]["name"])
        self.reports[report_id] = record
        return record

    def advance(self, report_id: str) -> dict:
        """One getReport observation. Advances the generation state machine."""
        record = self.reports[report_id]
        record["polls"] += 1
        if record["polls"] == 1:
            record["status"] = "QUEUED"
        elif record["polls"] < TERMINAL_AFTER_POLLS:
            record["status"] = "RUNNING"
        elif record["outcome"] == "STUCK":
            record["status"] = "RUNNING"
        else:
            record["status"] = record["outcome"]
            record.setdefault("completionTime", "2026-05-13T08:19:58Z")
        return record


def public_report(record: dict) -> dict:
    out = {
        "id": record["id"],
        "reportDefinitionId": record["reportDefinitionId"],
        "resourceId": record["resourceId"],
        "status": record["status"],
        "owner": record["owner"],
        "name": record["name"],
        "links": [
            {"href": "/suite-api/api/reports/%s" % record["id"], "rel": "SELF"},
        ],
    }
    for optional in ("description", "subject", "publish", "completionTime"):
        if optional in record:
            out[optional] = record[optional]
    return out


# --------------------------------------------------------------------------
# Request handling
# --------------------------------------------------------------------------


class ApiError(Exception):
    def __init__(self, status: int, message: str, violation: str):
        super().__init__(message)
        self.status = status
        self.message = message
        self.violation = violation


class Request:
    def __init__(self, method, raw_path, headers, body_bytes):
        self.method = method
        self.raw_path = raw_path
        split = urlsplit(raw_path)
        self.path = split.path
        self.query = split.query
        self.query_params = dict(parse_qsl(split.query, keep_blank_values=True))
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.body_bytes = body_bytes

    def header(self, name, default=""):
        return self.headers.get(name.lower(), default)


def require_auth(req: Request, state: MockState, op: dict, wire: dict) -> str:
    if op.get("security") == []:
        return ""
    prefix = wire["authorizationHeaderFormat"].split("{")[0]  # "OpsToken "
    value = req.header("authorization")
    if not value:
        raise ApiError(401, "Authorization header is required", "missing_authorization")
    if not value.startswith(prefix):
        raise ApiError(
            401,
            "Authorization header must use the %r scheme, got %r"
            % (prefix.strip(), value.split(" ")[0]),
            "wrong_authorization_scheme",
        )
    token = value[len(prefix) :]
    if token not in state.tokens:
        raise ApiError(401, "token is not valid or has been released", "invalid_token")
    return token


def parse_json_body(req: Request) -> dict:
    content_type = req.header("content-type").split(";")[0].strip()
    if content_type != "application/json":
        raise ApiError(
            415,
            "Content-Type must be application/json, got %r" % (content_type or "<absent>"),
            "wrong_content_type",
        )
    accept = req.header("accept")
    if accept != "application/json":
        raise ApiError(
            406,
            "Accept must be exactly application/json, got %r" % (accept or "<absent>"),
            "wrong_accept",
        )
    if not req.body_bytes:
        raise ApiError(400, "a JSON request body is required", "missing_body")
    try:
        payload = json.loads(req.body_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ApiError(400, "request body is not valid JSON: %s" % exc, "malformed_json")
    if not isinstance(payload, dict):
        raise ApiError(400, "request body must be a JSON object", "malformed_json")
    return payload


def validate_body(payload: dict, body_spec: dict) -> None:
    """Enforce the contract's request-body rules, including field omission."""
    required = set(body_spec["requiredProperties"])
    settable = set(body_spec.get("callerSettableOptionalProperties", body_spec["optionalProperties"]))
    server_assigned = set(body_spec.get("serverAssignedProperties", []))
    allowed = required | settable

    for key in sorted(payload):
        if key in server_assigned:
            raise ApiError(
                400,
                "%r is assigned by the server and must not be sent in the request body" % key,
                "server_assigned_property_sent",
            )
        if key not in allowed:
            raise ApiError(
                400,
                "%r is not an accepted property for this request; accepted: %s"
                % (key, sorted(allowed)),
                "unknown_property",
            )

    for key in sorted(required - set(payload)):
        raise ApiError(400, "required property %r is missing" % key, "missing_required_property")

    for key in sorted(set(payload) - required):
        value = payload[key]
        if value is None or value == "" or value == []:
            raise ApiError(
                400,
                "optional property %r is unset and must be omitted from the request body "
                "entirely rather than sent as %s" % (key, json.dumps(value)),
                "empty_optional_sent",
            )

    for key in sorted(required):
        value = payload[key]
        if not isinstance(value, str) or not value:
            raise ApiError(
                400, "required property %r must be a non-empty string" % key, "bad_required_property"
            )


def require_no_body(req: Request) -> None:
    if req.body_bytes:
        raise ApiError(
            400,
            "this operation defines no request body; send no payload at all",
            "unexpected_body",
        )
    if req.header("content-type"):
        raise ApiError(
            400,
            "this operation defines no request body; do not send a Content-Type header",
            "unexpected_content_type",
        )


# -- operation handlers ----------------------------------------------------


def op_acquire_token(req, state, op, contract, params):
    payload = parse_json_body(req)
    validate_body(payload, op["requestBody"])
    if (
        payload["username"] != VALID_USERNAME
        or payload["password"] != VALID_PASSWORD
        or payload.get("authSource", VALID_AUTH_SOURCE) != VALID_AUTH_SOURCE
    ):
        raise ApiError(401, "Authentication failed", "bad_credentials")
    with state.lock:
        token = state.new_token()
    return 200, "application/json", {
        "token": token,
        "validity": 1778660398000,
        "expiresAt": "2026-05-13 14:19:58 UTC",
        "roles": ["ReportAdmin"],
    }


def op_release_token(req, state, op, contract, params):
    require_no_body(req)
    token = req.header("authorization").split(" ", 1)[1]
    with state.lock:
        state.tokens.discard(token)
    return 200, None, None


def op_create_report(req, state, op, contract, params):
    payload = parse_json_body(req)
    validate_body(payload, op["requestBody"])
    definition_id = payload["reportDefinitionId"]
    if definition_id not in REPORT_DEFINITIONS:
        raise ApiError(404, "no report definition %r" % definition_id, "unknown_report_definition")
    if payload["resourceId"] not in KNOWN_RESOURCES:
        raise ApiError(404, "no resource %r" % payload["resourceId"], "unknown_resource")
    with state.lock:
        record = state.new_report(definition_id, payload)
    return 200, "application/json", public_report(record)


def op_get_report(req, state, op, contract, params):
    report_id = params["id"]
    with state.lock:
        if report_id not in state.reports:
            raise ApiError(404, "no report %r" % report_id, "unknown_report")
        record = state.advance(report_id)
        body = public_report(record)
    return 200, "application/json", body


def op_download_report(req, state, op, contract, params):
    report_id = params["id"]
    default_format = op["defaultFormat"]
    accept_by_format = op["acceptHeaderByFormat"]

    if "format" in req.query_params:
        requested = req.query_params["format"]
        if requested == "":
            raise ApiError(
                400,
                "the optional 'format' query parameter is unset and must be omitted from the "
                "request URI entirely rather than sent empty",
                "empty_optional_sent",
            )
        if requested not in op["formatValues"]:
            raise ApiError(
                400,
                "unsupported format %r; supported: %s" % (requested, op["formatValues"]),
                "bad_format_value",
            )
        effective = requested
    else:
        effective = default_format

    for extra in sorted(set(req.query_params) - {"format"}):
        raise ApiError(400, "unknown query parameter %r" % extra, "unknown_query_parameter")

    expected_accept = accept_by_format[effective]
    accept = req.header("accept")
    if accept != expected_accept:
        raise ApiError(
            406,
            "Accept must correspond to the effective format %r, expected %r, got %r"
            % (effective, expected_accept, accept or "<absent>"),
            "accept_format_mismatch",
        )

    with state.lock:
        if report_id not in state.reports:
            raise ApiError(404, "no report %r" % report_id, "unknown_report")
        record = state.reports[report_id]
        status = record["status"]
    if status != contract["asyncPolling"]["successStatus"]:
        raise ApiError(
            409,
            "report %s is in status %s; it can only be downloaded once getReport has reported %s"
            % (report_id, status, contract["asyncPolling"]["successStatus"]),
            "download_before_terminal",
        )
    return 200, expected_accept, report_payload(report_id, effective)


HANDLERS = {
    "acquireToken": op_acquire_token,
    "releaseToken": op_release_token,
    "createReport": op_create_report,
    "getReport": op_get_report,
    "downloadReport": op_download_report,
}


# --------------------------------------------------------------------------
# HTTP plumbing
# --------------------------------------------------------------------------


class RequestLog:
    def __init__(self, path: str):
        self.path = path
        self.lock = threading.Lock()
        self.seq = 0
        with open(self.path, "w", encoding="utf-8"):
            pass

    def append(self, record: dict) -> None:
        with self.lock:
            self.seq += 1
            record["seq"] = self.seq
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())


def make_handler(routes: ContractRoutes, state: MockState, log: RequestLog):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "vcfops-mock/1.0"

        def log_message(self, fmt, *args):  # silence stderr access logging
            pass

        def _dispatch(self, method):
            length = int(self.headers.get("Content-Length") or 0)
            body_bytes = self.rfile.read(length) if length else b""
            req = Request(method, self.path, dict(self.headers.items()), body_bytes)

            record = {
                "operationId": None,
                "method": method,
                "path": req.path,
                "rawPath": req.raw_path,
                "query": req.query,
                "queryParams": req.query_params,
                "pathParams": {},
                "headers": req.headers,
                "bodyRaw": body_bytes.decode("utf-8", "replace"),
                "bodyJson": None,
                "bodyKeys": None,
                "monotonic": round(time.monotonic(), 6),
            }
            try:
                record["bodyJson"] = json.loads(body_bytes.decode("utf-8"))
                if isinstance(record["bodyJson"], dict):
                    record["bodyKeys"] = sorted(record["bodyJson"])
            except Exception:
                pass

            op_id, op, path_params = routes.match(method, req.path)
            if op_id is None:
                known = path_params.get("__path_known__", False)
                status, violation = (405, "wrong_method") if known else (404, "unknown_operation")
                message = (
                    "method %s is not served for %s by this contract-pinned mock" % (method, req.path)
                    if known
                    else "%s is not an operation named by docs/contract.json" % req.path
                )
                record.update({"status": status, "contractViolation": violation})
                log.append(record)
                self._respond_json(status, {"message": message, "contractViolation": violation})
                return

            record["operationId"] = op_id
            record["pathParams"] = path_params

            try:
                require_auth(req, state, op, routes.wire)
                status, content_type, payload = HANDLERS[op_id](
                    req, state, op, routes.contract, path_params
                )
                record.update({"status": status, "contractViolation": None})
                log.append(record)
                if payload is None:
                    self._respond_empty(status)
                elif isinstance(payload, bytes):
                    self._respond_bytes(status, content_type, payload)
                else:
                    self._respond_json(status, payload)
            except ApiError as exc:
                record.update({"status": exc.status, "contractViolation": exc.violation})
                log.append(record)
                self._respond_json(
                    exc.status,
                    {
                        "message": exc.message,
                        "httpStatusCode": exc.status,
                        "contractViolation": exc.violation,
                    },
                )
            except Exception as exc:  # never drop a connection without a reply
                record.update({"status": 500, "contractViolation": "mock_internal_error"})
                log.append(record)
                self._respond_json(
                    500,
                    {
                        "message": "mock failed handling %s: %r" % (op_id, exc),
                        "httpStatusCode": 500,
                        "contractViolation": "mock_internal_error",
                    },
                )

        def _respond_bytes(self, status, content_type, payload):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _respond_json(self, status, payload):
            self._respond_bytes(status, "application/json", json.dumps(payload).encode("utf-8"))

        def _respond_empty(self, status):
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

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

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--log", required=True, help="path to write the JSON Lines request log")
    parser.add_argument("--contract", default=CONTRACT_PATH, help="path to docs/contract.json")
    parser.add_argument("--port", type=int, default=0, help="port to bind (0 picks a free one)")
    args = parser.parse_args(argv)

    contract = load_contract(args.contract)
    routes = ContractRoutes(contract, HANDLERS)
    state = MockState()
    log = RequestLog(args.log)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(routes, state, log))
    server.daemon_threads = True
    port = server.server_address[1]
    sys.stdout.write(
        "VCFOPS_MOCK_READY %s\n"
        % json.dumps(
            {
                "port": port,
                "baseUrl": "http://127.0.0.1:%d%s" % (port, contract["basePath"]),
                "log": os.path.abspath(args.log),
                "operations": sorted(contract["operations"]),
            }
        )
    )
    sys.stdout.flush()
    try:
        server.serve_forever(poll_interval=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
