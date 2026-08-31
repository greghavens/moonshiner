#!/usr/bin/env python3
"""Loopback mock of the VMware Cloud Foundation Operations API.

The callable route table is built exclusively from docs/contract.json: an
operation that the contract does not name is not served, and a request that
reaches an unlisted route is answered with 404 and recorded as off-contract.
Every received request is appended to a JSON Lines log that the verifier reads.

Usage:
    contract_mock.py --contract <path> --log <path> --port-file <path>

Binds 127.0.0.1 on an ephemeral port and writes the chosen port to --port-file
once the listener is accepting connections. An ephemeral port is required: a
hard-coded port collides with unrelated listeners and would silently answer
from the wrong process.
"""

import argparse
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# ---------------------------------------------------------------------------
# Fixtures. Every response below is a fixed function of the request, so the
# whole run is deterministic and no external state is involved.
# ---------------------------------------------------------------------------

USERNAME = "svc-notify"
PASSWORD = "VMw@re123!Ops"
AUTH_SOURCE = "vIDMAuthSource"
SESSION_TOKEN = "8f2c41d7a05b4e9ab6d33c7e15f08a24::9a1f"

SUPPORTED_PLUGIN_TYPES = [
    "Actions",
    "SNMP Trap",
    "GenericRestPlugin-slack",
    "Log File",
    "StandardEmailPlugin",
    "GenericRestPlugin-servicenow",
    "WebhookPlugin",
]

# Templates returned by getNotificationTemplates, filtered by the optional
# name query parameter when the client sends one.
TEMPLATES = [
    {
        "templateId": "d3139f94-3f04-420c-ab71-ba447be0f687",
        "name": "Default Email Template",
        "pluginTypeId": "StandardEmailPlugin",
        "templateType": "ALERT",
        "description": "Description for Default Email Template",
        "editable": False,
        "attachedRuleCount": 0,
    },
]

# The pluginId the server assigns to whichever plugin instance is created.
ASSIGNED_PLUGIN_ID = "5d1b7f60-8a24-4c39-b0e7-2f96ac41d853"

VERSION = {
    "releaseName": "VCF Operations 9.1.0.0",
    "major": 9,
    "minor": 1,
    "minorMinor": 0,
    "patch": 0,
    "buildNumber": 25541561,
    "description": None,
    "releasedDate": 1772539200000,
    "humanlyReadableReleaseDate": "Tuesday, March 3, 2026 at 12:00:00 PM Coordinated Universal Time",
}


def load_routes(contract_path):
    """Build the route table from the contract. Nothing else is servable."""
    with open(contract_path, encoding="utf-8") as handle:
        contract = json.load(handle)
    base = contract["base_path"].rstrip("/")
    routes = []
    for op in contract["operations"]:
        template = base + op["path_template"]
        pattern = "^" + re.sub(
            r"\{([A-Za-z_][A-Za-z0-9_]*)\}",
            lambda m: "(?P<%s>[^/]+)" % m.group(1),
            template,
        ) + "$"
        routes.append({
            "operation_id": op["operation_id"],
            "method": op["method"].upper(),
            "regex": re.compile(pattern),
            "allowed_query": set((op.get("query") or {}).keys()),
            "auth": op.get("authentication"),
        })
    auth = contract.get("auth") or {}
    return {
        "routes": routes,
        "auth_header": auth.get("header", "Authorization"),
        "auth_prefix": auth.get("header_prefix", "OpsToken "),
    }


class Recorder:
    """Append-only JSON Lines request log."""

    def __init__(self, path):
        self._path = path
        self._lock = threading.Lock()
        with open(path, "w", encoding="utf-8"):
            pass

    def record(self, entry):
        line = json.dumps(entry, sort_keys=True)
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "VcfOpsContractMock/1.0"
    sys_version = ""

    # -- plumbing ----------------------------------------------------------

    def log_message(self, *args):
        return

    def _read_body(self):
        raw = self.headers.get("Content-Length")
        if raw is None:
            return None
        try:
            length = int(raw)
        except ValueError:
            return None
        if length <= 0:
            return ""
        return self.rfile.read(length).decode("utf-8")

    def _respond(self, status, payload):
        if payload is None:
            body = b""
        else:
            body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        if body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    # -- dispatch ----------------------------------------------------------

    def _dispatch(self, method):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        body_text = self._read_body()
        body_json = None
        if body_text:
            try:
                body_json = json.loads(body_text)
            except ValueError:
                body_json = None

        cfg = self.server.contract
        matched = None
        path_matched = False
        for route in cfg["routes"]:
            if route["regex"].match(parsed.path):
                path_matched = True
                if route["method"] == method:
                    matched = route
                    break

        entry = {
            "sequence": self.server.next_sequence(),
            "method": method,
            "target": self.path,
            "path": parsed.path,
            "raw_query": parsed.query,
            "has_query_delimiter": "?" in self.path,
            "query": {k: v for k, v in query.items()},
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body_text": body_text,
            "body_json": body_json,
            "operation_id": matched["operation_id"] if matched else None,
            "off_contract": matched is None,
        }
        self.server.recorder.record(entry)

        if matched is None:
            status = 405 if path_matched else 404
            self._respond(status, {
                "message": "Operation is not named by docs/contract.json.",
                "httpStatusCode": status,
                "method": method,
                "path": parsed.path,
            })
            return

        # Unknown query parameters are a contract violation, not a filter.
        unknown = set(query) - matched["allowed_query"]
        if unknown:
            self._respond(400, {
                "message": "Query parameter(s) not declared for %s: %s" % (
                    matched["operation_id"], ", ".join(sorted(unknown))),
                "httpStatusCode": 400,
            })
            return

        if matched["auth"] and matched["auth"] != "none":
            presented = self.headers.get(cfg["auth_header"])
            expected = cfg["auth_prefix"] + SESSION_TOKEN
            if presented != expected:
                self._respond(401, {
                    "message": "Missing or invalid %s header." % cfg["auth_header"],
                    "httpStatusCode": 401,
                })
                return

        handler = getattr(self, "op_" + matched["operation_id"])
        handler(query, body_json)

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

    # -- operations --------------------------------------------------------

    def op_acquireToken(self, query, body):
        if not isinstance(body, dict):
            self._respond(400, {"message": "A username-password body is required.",
                                "httpStatusCode": 400})
            return
        if body.get("username") != USERNAME or body.get("password") != PASSWORD:
            self._respond(401, {"message": "Invalid credentials.",
                                "httpStatusCode": 401})
            return
        source = body.get("authSource")
        if source is not None and source != AUTH_SOURCE:
            self._respond(401, {"message": "Unknown auth source.",
                                "httpStatusCode": 401})
            return
        self._respond(200, {
            "token": SESSION_TOKEN,
            "validity": 1788098400000,
            "expiresAt": "Sunday, August 30, 2026 at 12:00:00 PM Coordinated Universal Time",
            "roles": [],
        })

    def op_getCurrentVersionOfServer(self, query, body):
        self._respond(200, dict(VERSION))

    def op_getAlertPluginTypes(self, query, body):
        self._respond(200, {
            "notificationPluginType": list(SUPPORTED_PLUGIN_TYPES),
            "links": [
                {
                    "href": "/suite-api/api/alertplugins/types/" + plugin_type.replace(" ", "%20"),
                    "rel": "RELATED",
                    "name": "linkToPluginType",
                }
                for plugin_type in SUPPORTED_PLUGIN_TYPES
            ],
        })

    def op_createAlertPlugin(self, query, body):
        if not isinstance(body, dict):
            self._respond(400, {"message": "A notification-plugin body is required.",
                                "httpStatusCode": 400})
            return
        for required in ("name", "pluginTypeId"):
            if not body.get(required):
                self._respond(400, {
                    "message": "notification-plugin.%s is required." % required,
                    "httpStatusCode": 400,
                })
                return
        if body["pluginTypeId"] not in SUPPORTED_PLUGIN_TYPES:
            self._respond(400, {
                "message": "Unsupported pluginTypeId %s." % body["pluginTypeId"],
                "httpStatusCode": 400,
            })
            return
        created = dict(body)
        created["pluginId"] = ASSIGNED_PLUGIN_ID
        created["version"] = 1788098745156
        created["enabled"] = False
        self._respond(201, created)

    def op_getNotificationTemplates(self, query, body):
        results = list(TEMPLATES)
        wanted = query.get("name", [None])[0]
        if wanted is not None:
            results = [t for t in results if t["name"] == wanted]
        payload = {
            "notificationTemplates": results,
            "pageInfo": {"page": 0, "pageSize": 1000, "totalCount": len(results)},
            "links": [],
        }
        self._respond(200, payload)

    def op_createNotificationPluginRule(self, query, body):
        if not isinstance(body, dict):
            self._respond(400, {"message": "A notification-rule body is required.",
                                "httpStatusCode": 400})
            return
        for required in ("name", "pluginId"):
            if not body.get(required):
                self._respond(400, {
                    "message": "notification-rule.%s is required." % required,
                    "httpStatusCode": 400,
                })
                return
        if body["pluginId"] != ASSIGNED_PLUGIN_ID:
            self._respond(404, {
                "message": "No plugin instance %s exists." % body["pluginId"],
                "httpStatusCode": 404,
            })
            return
        properties = body.get("properties") or []
        if not any(
            isinstance(item, dict) and item.get("name") == "emailaddr"
            for item in properties
        ):
            self._respond(500, {
                "type": "Error",
                "message": "Internal Server error, cause unknown.",
                "moreInformation": [
                    {"name": "errorMessage", "value": "property key is required: emailaddr"},
                    {"name": "localizedMessage", "value": "property key is required: emailaddr;"},
                ],
                "httpStatusCode": 500,
                "apiErrorCode": 500,
            })
            return
        created = dict(body)
        created["id"] = "a2f47c98-6b31-4d05-8e7a-19c4f0b3d276"
        created.setdefault("enabled", True)
        created.setdefault("templateId", "d3139f94-3f04-420c-ab71-ba447be0f687")
        created.setdefault("alertControlStates", [])
        created.setdefault("alertStatuses", [])
        created.setdefault("criticalities", [])
        created.setdefault("resourceKindFilters", [])
        created.setdefault("resourceFilters", [])
        created.setdefault("alertTypeFilters", [])
        created.setdefault("sendHeartbeat", False)
        created.setdefault("ruleType", "ALERT")
        created.setdefault("actionStatuses", [])
        created.setdefault("links", [{
            "href": "/suite-api/api/notifications/rules/" + created["id"],
            "rel": "SELF",
            "name": "linkToSelf",
        }])
        self._respond(201, created)


class MockServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, contract, recorder):
        super().__init__(address, Handler)
        self.contract = contract
        self.recorder = recorder
        self._sequence = 0
        self._sequence_lock = threading.Lock()

    def next_sequence(self):
        with self._sequence_lock:
            self._sequence += 1
            return self._sequence


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--port-file", required=True)
    args = parser.parse_args(argv)

    contract = load_routes(args.contract)
    recorder = Recorder(args.log)
    server = MockServer(("127.0.0.1", 0), contract, recorder)
    port = server.server_address[1]

    tmp = args.port_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(str(port))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, args.port_file)

    sys.stderr.write("contract mock listening on 127.0.0.1:%d\n" % port)
    sys.stderr.flush()
    try:
        server.serve_forever(poll_interval=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
