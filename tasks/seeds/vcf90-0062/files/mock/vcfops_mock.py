#!/usr/bin/env python3
"""Loopback mock of the VCF Operations REST API, pinned to docs/contract.json.

It serves *only* the five operations named in the contract; anything else is a
404. Request bodies and query strings are validated against the contract, so a
request that carries an optional field it should have omitted (``null``, ``""``
or ``[]``) is rejected with 400 rather than quietly accepted.

Every request is appended to a JSONL request log so a test can assert the exact
wire shape after the fact.

    python3 mock/vcfops_mock.py --contract docs/contract.json \
        --scenario mock/scenarios/token-expiry.json --port 18900 \
        --log /tmp/requests.jsonl

The socket is bound to 127.0.0.1 only. No VMware endpoint is contacted.
"""

import argparse
import json
import pathlib
import re
import signal
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
ACTIVE_STATUSES = ("NEW", "ACTIVE", "UPDATED")


class BadRequest(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class TokenStore:
    """Issues access tokens with a fixed budget of authenticated calls.

    Expiry is driven by the call budget rather than by the wall clock so that a
    run is reproducible: token N dies on exactly the same request every time.
    """

    def __init__(self, budgets):
        self.budgets = list(budgets) or [2 ** 31]
        self.issued = 0
        self.tokens = {}
        self.current = None

    def issue(self):
        self.issued += 1
        budget = self.budgets[min(self.issued - 1, len(self.budgets) - 1)]
        token = "ops-token-%d" % self.issued
        self.tokens[token] = {"remaining": budget, "state": "active"}
        self.current = token
        return token, budget

    def spend(self, token):
        """Return (ok, reason). Marks the token expired once its budget runs out."""
        entry = self.tokens.get(token)
        if entry is None:
            return False, "Unknown access token"
        if entry["state"] == "released":
            return False, "Access token was released"
        if entry["state"] == "expired" or entry["remaining"] <= 0:
            entry["state"] = "expired"
            return False, "Access token has expired"
        entry["remaining"] -= 1
        return True, None

    def release(self, token):
        self.tokens[token]["state"] = "released"


class Contract:
    def __init__(self, doc):
        self.doc = doc
        self.prefix = doc["securityScheme"]["valuePrefix"]
        self.routes = {}
        for op in doc["operations"].values():
            self.routes[(op["method"], op["requestPath"])] = op
        self.models = doc["models"]

    def lookup(self, method, path):
        return self.routes.get((method, path))

    # -- validation ---------------------------------------------------------
    def check_query(self, op, pairs):
        declared = op.get("queryParameters", {})
        seen = {}
        for key, value in pairs:
            if key not in declared:
                raise BadRequest(400, "unknown query parameter %r for %s" % (key, op["operationId"]))
            if key in seen:
                raise BadRequest(400, "duplicated query parameter %r" % key)
            if value == "":
                raise BadRequest(
                    400,
                    "query parameter %r was sent empty; optional parameters must be omitted" % key,
                )
            if declared[key]["type"] == "integer":
                try:
                    value = int(value)
                except ValueError:
                    raise BadRequest(400, "query parameter %r must be an integer" % key)
            seen[key] = value
        for key, meta in declared.items():
            if meta.get("required") and key not in seen:
                raise BadRequest(400, "missing required query parameter %r" % key)
        return seen

    def check_body(self, op, parsed, raw):
        spec = op.get("requestBody")
        if spec is None:
            if raw.strip():
                raise BadRequest(400, "%s does not take a request body" % op["operationId"])
            return None
        if parsed is None:
            raise BadRequest(400, "%s requires a JSON request body" % op["operationId"])
        if not isinstance(parsed, dict):
            raise BadRequest(400, "request body must be a JSON object")
        props = spec["properties"]
        for key in parsed:
            if key not in props:
                raise BadRequest(
                    400, "unknown property %r in %s body" % (key, spec["schema"])
                )
        for key in spec["requiredProperties"]:
            if key not in parsed:
                raise BadRequest(400, "missing required property %r in %s body" % (key, spec["schema"]))
        for key, value in parsed.items():
            self._check_value(spec["schema"], key, props[key], value)
        return parsed

    def _check_value(self, schema_name, key, prop, value):
        where = "%s.%s" % (schema_name, key)
        if value is None:
            raise BadRequest(
                400, "%s was sent as null; unset optional fields must be omitted" % where
            )
        kind = prop.get("type")
        if kind == "array":
            if not isinstance(value, list):
                raise BadRequest(400, "%s must be an array" % where)
            if not value:
                raise BadRequest(
                    400, "%s was sent as an empty array; unset optional fields must be omitted" % where
                )
            item = prop.get("items", {})
            for element in value:
                if element is None:
                    raise BadRequest(400, "%s contains a null element" % where)
                if item.get("enum") and element not in item["enum"]:
                    raise BadRequest(400, "%s contains %r which is not one of %s" % (where, element, item["enum"]))
                if item.get("format") == "uuid" and not UUID_RE.match(str(element)):
                    raise BadRequest(400, "%s contains %r which is not a UUID" % (where, element))
                if item.get("type") == "string" and not isinstance(element, str):
                    raise BadRequest(400, "%s contains a non-string element" % where)
        elif kind == "boolean":
            if not isinstance(value, bool):
                raise BadRequest(400, "%s must be a boolean" % where)
        elif kind == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise BadRequest(400, "%s must be an integer" % where)
        elif kind == "object":
            if not isinstance(value, dict):
                raise BadRequest(400, "%s must be an object" % where)
            if not value:
                raise BadRequest(400, "%s was sent empty; unset optional fields must be omitted" % where)
        else:
            if not isinstance(value, str):
                raise BadRequest(400, "%s must be a string" % where)
            if value == "":
                raise BadRequest(
                    400, "%s was sent empty; unset optional fields must be omitted" % where
                )
            if prop.get("enum") and value not in prop["enum"]:
                raise BadRequest(400, "%s must be one of %s" % (where, prop["enum"]))


class Scenario:
    def __init__(self, doc):
        self.name = doc["name"]
        self.credentials = doc["credentials"]
        self.version = doc["version"]
        self.alerts = doc["alerts"]
        self.by_id = {a["alertId"]: a for a in self.alerts}
        self.allowed_actions = doc["allowedActions"]
        self.action_rules = doc.get("actionRules", {})
        self.tokens = TokenStore(doc["tokenBudgets"])
        self.acted = {}


class RequestLog:
    def __init__(self, path):
        self.path = pathlib.Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("")
        self.lock = threading.Lock()
        self.seq = 0

    def append(self, entry):
        with self.lock:
            self.seq += 1
            entry["seq"] = self.seq
            with self.path.open("a") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")
                handle.flush()


def make_handler(contract, scenario, log):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "vcfops-mock/1.0"

        def log_message(self, *_args):
            pass

        def _respond(self, status, payload, entry, error=None):
            entry["status"] = status
            if error:
                entry["error"] = error
            log.append(entry)
            if payload is None:
                body = b""
            else:
                body = json.dumps(payload).encode()
            self.send_response(status)
            if body:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _dispatch(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            split = urllib.parse.urlsplit(self.path)
            pairs = urllib.parse.parse_qsl(split.query, keep_blank_values=True)
            auth = self.headers.get("Authorization") or ""
            token = auth[len(contract.prefix):] if auth.startswith(contract.prefix) else None
            try:
                parsed = json.loads(raw) if raw.strip() else None
            except ValueError:
                parsed = None

            entry = {
                "method": self.command,
                "path": split.path,
                "queryString": split.query,
                "query": dict(pairs),
                "queryKeys": [k for k, _ in pairs],
                "operationId": None,
                "userAgent": self.headers.get("User-Agent"),
                "accept": self.headers.get("Accept"),
                "contentType": self.headers.get("Content-Type"),
                "authorizationScheme": contract.prefix.strip() if auth.startswith(contract.prefix) else (auth.split(" ")[0] if auth else None),
                "tokenId": token,
                "requestBodyRaw": raw,
                "requestBody": parsed,
                "requestBodyKeys": sorted(parsed) if isinstance(parsed, dict) else None,
            }

            op = contract.lookup(self.command, split.path)
            if op is None:
                return self._respond(
                    404,
                    {"message": "no operation is contracted for %s %s" % (self.command, split.path)},
                    entry,
                    error="uncontracted-operation",
                )
            entry["operationId"] = op["operationId"]

            try:
                query = contract.check_query(op, pairs)
                if op["authorization"] == "bearer":
                    if token is None:
                        raise BadRequest(401, "missing %s authorization header" % contract.prefix.strip())
                    ok, reason = scenario.tokens.spend(token)
                    if not ok:
                        raise BadRequest(401, reason)
                body = contract.check_body(op, parsed, raw)
                status, payload = HANDLERS[op["operationId"]](self, query, body, token)
            except BadRequest as exc:
                return self._respond(
                    exc.status,
                    {"message": exc.message, "httpStatusCode": exc.status},
                    entry,
                    error=exc.message,
                )
            return self._respond(status, payload, entry)

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _dispatch

        # -- operation handlers --------------------------------------------
        def op_acquire_token(self, query, body, token):
            creds = scenario.credentials
            if body.get("username") != creds["username"] or body.get("password") != creds["password"]:
                raise BadRequest(401, "invalid credentials")
            expected_source = creds.get("authSource")
            if expected_source is None:
                if "authSource" in body:
                    raise BadRequest(401, "this deployment has no named auth source")
            elif body.get("authSource") != expected_source:
                raise BadRequest(401, "unknown auth source %r" % body.get("authSource"))
            issued, _budget = scenario.tokens.issue()
            return 200, {
                "token": issued,
                "validity": 1786060800000,
                "expiresAt": "Wednesday, August 12, 2026 4:00:00 PM UTC",
                "roles": ["ContentAdmin"],
            }

        def op_release_token(self, query, body, token):
            scenario.tokens.release(token)
            return 200, None

        def op_version(self, query, body, token):
            return 200, scenario.version

        def op_query_alert(self, query, body, token):
            body = body or {}
            selected = list(scenario.alerts)
            if body.get("activeOnly"):
                selected = [a for a in selected if a["status"] in ACTIVE_STATUSES]
            if "alertCriticality" in body:
                selected = [a for a in selected if a["alertLevel"] in body["alertCriticality"]]
            if "alertStatus" in body:
                selected = [a for a in selected if a["status"] in body["alertStatus"]]
            if "alertName" in body:
                needle = body["alertName"].lower()
                selected = [a for a in selected if needle in a.get("alertDefinitionName", "").lower()]
            page = query.get("page", 0)
            page_size = query.get("pageSize", 1000)
            if page_size <= 0:
                raise BadRequest(400, "pageSize must be positive")
            window = selected[page * page_size:(page + 1) * page_size]
            return 200, {
                "pageInfo": {"totalCount": len(selected), "page": page, "pageSize": page_size},
                "alerts": window,
            }

        def op_modify_alerts(self, query, body, token):
            action = query["action"]
            if action not in scenario.allowed_actions:
                raise BadRequest(400, "action %r is not supported by this deployment" % action)
            rules = scenario.action_rules.get(action, {})
            for name in rules.get("require", []):
                if name not in query:
                    raise BadRequest(400, "action %r requires the %r query parameter" % (action, name))
            for name in rules.get("forbid", []):
                if name in query:
                    raise BadRequest(
                        400, "action %r must not carry the %r query parameter" % (action, name)
                    )
            uuids = body["uuids"]
            if len(set(uuids)) != len(uuids):
                raise BadRequest(400, "uuid-values.uuids contains duplicates")
            for alert_id in uuids:
                if alert_id not in scenario.by_id:
                    raise BadRequest(404, "no such alert %s" % alert_id)
                if alert_id in scenario.acted:
                    raise BadRequest(
                        409,
                        "alert %s already had %r applied; work was replayed"
                        % (alert_id, scenario.acted[alert_id]),
                    )
            updated = []
            for alert_id in uuids:
                scenario.acted[alert_id] = action
                alert = dict(scenario.by_id[alert_id])
                if action == "suspend":
                    alert["controlState"] = "SUSPENDED"
                    alert["suspendUntilTimeUTC"] = 1786060800000 + int(query["minutes"]) * 60000
                elif action == "assignownership":
                    alert["controlState"] = "ASSIGNED"
                    alert["ownerId"] = query["userAccountID"]
                updated.append(alert)
            return 200, {
                "pageInfo": {"totalCount": len(updated), "page": 0, "pageSize": 1000},
                "alerts": updated,
            }

    HANDLERS = {
        "acquireToken": Handler.op_acquire_token,
        "releaseToken": Handler.op_release_token,
        "getCurrentVersionOfServer": Handler.op_version,
        "queryAlert": Handler.op_query_alert,
        "modifyAlerts": Handler.op_modify_alerts,
    }
    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--contract", required=True)
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=18900)
    ap.add_argument("--log", required=True)
    ap.add_argument("--ready-file", help="JSON file written once the socket is listening")
    args = ap.parse_args()

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        sys.exit("the mock only binds loopback addresses")

    contract = Contract(json.loads(pathlib.Path(args.contract).read_text()))
    scenario = Scenario(json.loads(pathlib.Path(args.scenario).read_text()))
    log = RequestLog(args.log)

    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(contract, scenario, log))
    port = httpd.server_address[1]
    if args.ready_file:
        pathlib.Path(args.ready_file).write_text(json.dumps({"port": port, "scenario": scenario.name}))
    print("vcfops-mock listening on http://%s:%d (scenario %s)" % (args.host, port, scenario.name), flush=True)

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
