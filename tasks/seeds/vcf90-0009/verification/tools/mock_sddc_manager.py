#!/usr/bin/env python3
"""Loopback mock of the three SDDC Manager operations this repo integrates with.

The mock is pinned to the contract: it serves *only* the operations named in
``docs/contract.json`` and rejects everything else with 404.  It is deliberately
strict about the request wire shape so that a client which sends empty optional
fields, wrong casing, a missing bearer token or an off-contract route fails
loudly instead of silently "working".

Every request is appended to a JSON Lines request log so tests can assert on the
exact traffic a client produced.

Usage:
    python3 tools/mock_sddc_manager.py --scenario scenario.json --log requests.jsonl [--port 0]

The server prints a single ``MOCK_READY {"port": <n>}`` line to stdout once it is
listening, then serves until terminated.  It binds 127.0.0.1 only.

Scenario file format:
    {
      "username": "administrator@vsphere.local",
      "password": "VMw@re1!",
      "access_token": "eyJhb...",
      "refresh_token_id": "0f5b...",
      "bundles": {
        "<bundleId>": {
          "task_id": "<taskId>",
          "task_name": "Downloading Bundle",
          "initial_status": "Pending",
          "statuses": ["PENDING", "In Progress", "Successful"],
          "errors": [ {"errorCode": "...", "message": "..."} ]
        }
      }
    }

``statuses[i]`` is returned by the (i+1)-th GET /v1/tasks/{id}; the last entry
repeats for any further polls.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --- the contract surface -------------------------------------------------
# (method, compiled path pattern, handler name).  Nothing else is served.
ROUTES = [
    ("POST", re.compile(r"^/v1/tokens$"), "op_create_token"),
    ("PATCH", re.compile(r"^/v1/bundles/(?P<id>[^/]+)$"), "op_start_bundle_download"),
    ("GET", re.compile(r"^/v1/tasks/(?P<id>[^/]+)$"), "op_get_task"),
]

# Property names taken from the 9.0.0.0 component schemas.  Anything outside
# these sets is a contract violation.
TOKEN_CREATION_SPEC_PROPS = {"username", "password", "apiKey", "idToken"}
BUNDLE_UPDATE_SPEC_PROPS = {"bundleDownloadSpec"}
BUNDLE_DOWNLOAD_SPEC_PROPS = {
    "scheduledTimestamp": str,
    "downloadNow": bool,
    "cancelNow": bool,
}

CREATION_TS = "2026-02-11T09:14:22.310Z"
COMPLETION_TS = "2026-02-11T09:19:48.002Z"


class MockState:
    def __init__(self, scenario, log_path):
        self.scenario = scenario
        self.log_path = log_path
        self.lock = threading.Lock()
        self.seq = 0
        self.polls = {}  # task_id -> number of GET /v1/tasks/{id} served
        self.tasks = {}  # task_id -> bundle config
        for bundle_id, cfg in scenario.get("bundles", {}).items():
            cfg = dict(cfg)
            cfg["bundle_id"] = bundle_id
            self.tasks[cfg["task_id"]] = cfg

    def next_seq(self):
        with self.lock:
            self.seq += 1
            return self.seq

    def record(self, entry):
        with self.lock:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, sort_keys=True) + "\n")
                fh.flush()

    def poll(self, task_id):
        with self.lock:
            n = self.polls.get(task_id, 0) + 1
            self.polls[task_id] = n
            return n


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "MockSddcManager/9.0.0.0"

    state: MockState = None  # set by build_server

    # -- plumbing ---------------------------------------------------------
    def log_message(self, fmt, *args):  # keep stderr quiet
        pass

    def _read_body(self):
        length = self.headers.get("Content-Length")
        if not length:
            return b""
        try:
            return self.rfile.read(int(length))
        except (ValueError, OSError):
            return b""

    def _send(self, status, payload):
        body = json.dumps(payload).encode("utf-8") if payload is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)
        return status

    def _error(self, status, code, message):
        return self._send(
            status,
            {
                "errorCode": code,
                "errorType": "VALIDATION_FAILED" if status == 400 else "MOCK",
                "message": message,
            },
        )

    def _dispatch(self, method):
        path, _, query = self.path.partition("?")
        raw = self._read_body()
        try:
            parsed_body = json.loads(raw.decode("utf-8")) if raw else None
            body_error = None
        except (ValueError, UnicodeDecodeError) as exc:
            parsed_body = None
            body_error = str(exc)

        entry = {
            "seq": self.state.next_seq(),
            "method": method,
            "path": path,
            "query": query,
            "headers": {
                "authorization": self.headers.get("Authorization"),
                "content-type": self.headers.get("Content-Type"),
                "accept": self.headers.get("Accept"),
            },
            "body": parsed_body,
            "body_raw": raw.decode("utf-8", "replace"),
            "body_parse_error": body_error,
        }

        status = None
        for route_method, pattern, handler_name in ROUTES:
            match = pattern.match(path)
            if match and route_method == method:
                entry["operation"] = handler_name
                try:
                    status = getattr(self, handler_name)(match, parsed_body, body_error)
                except Exception as exc:  # pragma: no cover - defensive
                    status = self._error(500, "MOCK_CRASH", repr(exc))
                break
            if match:
                entry["operation"] = "method_not_allowed"
                status = self._error(
                    405, "METHOD_NOT_ALLOWED", "%s is not served on %s" % (method, path)
                )
                break
        if status is None:
            entry.setdefault("operation", "off_contract")
            status = self._error(
                404, "NOT_FOUND", "%s %s is not part of the pinned contract" % (method, path)
            )

        entry["response_status"] = status
        self.state.record(entry)

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

    def do_HEAD(self):
        self._dispatch("HEAD")

    # -- shared validation -------------------------------------------------
    def _require_json_body(self, body, body_error):
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            self._error(415, "UNSUPPORTED_MEDIA_TYPE", "Content-Type must be application/json")
            return None, 415
        if body_error is not None:
            self._error(400, "MALFORMED_JSON", "Request body is not valid JSON: %s" % body_error)
            return None, 400
        if not isinstance(body, dict):
            self._error(400, "MALFORMED_JSON", "Request body must be a JSON object")
            return None, 400
        return body, None

    def _reject_empty(self, obj, schema_name, allowed):
        """Optional fields must be omitted, never sent as null/empty."""
        for key, value in obj.items():
            if key not in allowed:
                return self._error(
                    400,
                    "UNKNOWN_PROPERTY",
                    "%s has no property %r (allowed: %s)"
                    % (schema_name, key, ", ".join(sorted(allowed))),
                )
            if value is None:
                return self._error(
                    400,
                    "NULL_PROPERTY",
                    "%s.%s was sent as null; unset optional fields must be omitted"
                    % (schema_name, key),
                )
            if isinstance(value, str) and value.strip() == "":
                return self._error(
                    400,
                    "EMPTY_PROPERTY",
                    "%s.%s was sent as an empty string; unset optional fields must be omitted"
                    % (schema_name, key),
                )
            if isinstance(value, (dict, list)) and len(value) == 0:
                return self._error(
                    400,
                    "EMPTY_PROPERTY",
                    "%s.%s was sent empty; unset optional fields must be omitted"
                    % (schema_name, key),
                )
        return None

    def _require_bearer(self):
        expected = self.state.scenario["access_token"]
        header = self.headers.get("Authorization")
        if not header:
            self._error(401, "UNAUTHENTICATED", "Missing Authorization header")
            return 401
        parts = header.split(None, 1)
        if len(parts) != 2 or parts[0] != "Bearer":
            self._error(401, "UNAUTHENTICATED", "Authorization must use the Bearer scheme")
            return 401
        if parts[1].strip() != expected:
            self._error(401, "UNAUTHENTICATED", "Access token is not the one issued by POST /v1/tokens")
            return 401
        return None

    # -- operations --------------------------------------------------------
    def op_create_token(self, match, body, body_error):
        body, failed = self._require_json_body(body, body_error)
        if failed:
            return failed
        bad = self._reject_empty(body, "TokenCreationSpec", TOKEN_CREATION_SPEC_PROPS)
        if bad:
            return bad
        if "username" not in body or "password" not in body:
            return self._error(
                400, "INVALID_SPEC", "TokenCreationSpec requires username and password"
            )
        scenario = self.state.scenario
        if body["username"] != scenario["username"] or body["password"] != scenario["password"]:
            return self._error(401, "UNAUTHENTICATED", "Invalid credentials")
        return self._send(
            201,
            {
                "accessToken": scenario["access_token"],
                "refreshToken": {"id": scenario["refresh_token_id"]},
            },
        )

    def op_start_bundle_download(self, match, body, body_error):
        unauth = self._require_bearer()
        if unauth:
            return unauth
        body, failed = self._require_json_body(body, body_error)
        if failed:
            return failed
        bad = self._reject_empty(body, "BundleUpdateSpec", BUNDLE_UPDATE_SPEC_PROPS)
        if bad:
            return bad
        spec = body.get("bundleDownloadSpec")
        if spec is None:
            return self._error(
                400, "INVALID_SPEC", "BundleUpdateSpec.bundleDownloadSpec is required by this API"
            )
        if not isinstance(spec, dict):
            return self._error(400, "INVALID_SPEC", "bundleDownloadSpec must be an object")
        bad = self._reject_empty(spec, "BundleDownloadSpec", set(BUNDLE_DOWNLOAD_SPEC_PROPS))
        if bad:
            return bad
        for key, value in spec.items():
            if not isinstance(value, BUNDLE_DOWNLOAD_SPEC_PROPS[key]) or isinstance(value, bool) != (
                BUNDLE_DOWNLOAD_SPEC_PROPS[key] is bool
            ):
                return self._error(
                    400,
                    "INVALID_TYPE",
                    "BundleDownloadSpec.%s must be a %s"
                    % (key, BUNDLE_DOWNLOAD_SPEC_PROPS[key].__name__),
                )

        bundle_id = match.group("id")
        cfg = self.state.scenario.get("bundles", {}).get(bundle_id)
        if cfg is None:
            return self._error(404, "BUNDLE_NOT_FOUND", "No bundle with id %s" % bundle_id)
        return self._send(
            202,
            {
                "id": cfg["task_id"],
                "name": cfg.get("task_name", "Downloading Bundle"),
                "type": "BUNDLE_DOWNLOAD",
                "status": cfg.get("initial_status", cfg["statuses"][0]),
                "creationTimestamp": CREATION_TS,
                "isCancellable": True,
                "isRetryable": False,
                "resources": [{"resourceId": bundle_id, "type": "BUNDLE"}],
            },
        )

    def op_get_task(self, match, body, body_error):
        unauth = self._require_bearer()
        if unauth:
            return unauth
        task_id = match.group("id")
        cfg = self.state.tasks.get(task_id)
        if cfg is None:
            return self._error(404, "TASK_NOT_FOUND", "No task with id %s" % task_id)
        n = self.state.poll(task_id)
        statuses = cfg["statuses"]
        status = statuses[min(n, len(statuses)) - 1]
        task = {
            "id": task_id,
            "name": cfg.get("task_name", "Downloading Bundle"),
            "type": "BUNDLE_DOWNLOAD",
            "status": status,
            "creationTimestamp": CREATION_TS,
            "isCancellable": True,
            "isRetryable": False,
            "resources": [{"resourceId": cfg["bundle_id"], "type": "BUNDLE"}],
        }
        normalized = status.replace(" ", "_").upper()
        if normalized not in ("PENDING", "IN_PROGRESS"):
            task["completionTimestamp"] = COMPLETION_TS
        if normalized in ("FAILED", "CANCELLED") and cfg.get("errors"):
            task["errors"] = cfg["errors"]
        return self._send(200, task)


def build_server(scenario, log_path, port):
    state = MockState(scenario, log_path)
    handler = type("BoundHandler", (Handler,), {"state": state})
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)

    with open(args.scenario, encoding="utf-8") as fh:
        scenario = json.load(fh)
    open(args.log, "a", encoding="utf-8").close()

    httpd = build_server(scenario, args.log, args.port)
    sys.stdout.write("MOCK_READY %s\n" % json.dumps({"port": httpd.server_address[1]}))
    sys.stdout.flush()
    try:
        httpd.serve_forever(poll_interval=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
