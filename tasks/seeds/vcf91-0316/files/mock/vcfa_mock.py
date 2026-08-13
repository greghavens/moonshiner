"""Loopback mock of a VCF Automation 9.1 appliance.

The route table is built by reading ``docs/contract.json``: the mock serves the
five operations that contract names and nothing else. Any other method/path pair
is answered 404 and recorded in the request log as unmatched, so a test can prove
the client under test stayed inside the contract.

Every request is appended to a JSONL request log with its exact wire shape --
raw body, parsed body, raw query string, ordered parameter pairs, and the headers
that matter -- so the log, not the mock's leniency, is what assertions are made
against. The mock deliberately does NOT validate optional-field omission; that is
the verifier's job.

Binds 127.0.0.1 on an ephemeral port. Contacts nothing.
"""

import base64
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CONTRACT_PATH = _REPO_ROOT / "docs" / "contract.json"
_FIXTURES_PATH = Path(__file__).resolve().parent / "fixtures.json"


def _load_routes(contract):
    """Build the route table from the contract's operations list."""
    routes = []
    for op in contract["operations"]:
        segments = [s for s in op["path"].split("/") if s]
        routes.append(
            {
                "operation_id": op["id"],
                "method": op["method"],
                "segments": segments,
                "path": op["path"],
            }
        )
    return routes


def _match(routes, method, path):
    segments = [s for s in path.split("/") if s]
    for route in routes:
        if route["method"] != method or len(route["segments"]) != len(segments):
            continue
        params = {}
        for pattern, actual in zip(route["segments"], segments):
            if pattern.startswith("{") and pattern.endswith("}"):
                params[pattern[1:-1]] = actual
            elif pattern != actual:
                break
        else:
            return route, params
    return None, None


class _State:
    """Mutable appliance state. All access is guarded by the server lock."""

    def __init__(self, fixtures):
        self.fixtures = fixtures
        self.client_id = fixtures["client"]["client_id"]
        self.client_secret = fixtures["client"]["client_secret"]
        self.refresh_token = fixtures["refresh_token"]
        self.budgets = list(fixtures["access_token_call_budgets"])
        self.tokens = {}          # token value -> {"id", "calls_left", "revoked"}
        self.tokens_issued = 0
        self.deployments = {}     # deployment id -> record
        self.requests = {}        # request id -> record
        self.by_deployment = {}   # deployment id -> request id

    # -- tokens ---------------------------------------------------------
    def issue_token(self):
        self.tokens_issued += 1
        token_id = "at-%d" % self.tokens_issued
        for record in self.tokens.values():
            record["revoked"] = True
        if self.budgets:
            budget = self.budgets.pop(0)
        else:
            budget = None
        value = "eyJmaXh0dXJlIjoi%s" % uuid.uuid4().hex
        self.tokens[value] = {"id": token_id, "calls_left": budget, "revoked": False}
        return value

    def check_bearer(self, token):
        """Return (token_id, reason). reason is None when the token authorises."""
        record = self.tokens.get(token)
        if record is None:
            return None, "unknown_token"
        if record["revoked"]:
            return record["id"], "revoked_token"
        if record["calls_left"] is not None:
            if record["calls_left"] <= 0:
                return record["id"], "expired_token"
            record["calls_left"] -= 1
        return record["id"], None

    # -- deployments ----------------------------------------------------
    def create_deployment(self, catalog_item_id, name, project_id):
        deployment_id = "dep-%s" % uuid.uuid4()
        request_id = "req-%s" % uuid.uuid4()
        scripts = self.fixtures["request_scripts"]
        script = scripts["by_deployment_name"].get(name, scripts["default"])
        self.deployments[deployment_id] = {
            "id": deployment_id,
            "name": name,
            "projectId": project_id,
            "catalogItemId": catalog_item_id,
        }
        self.requests[request_id] = {
            "id": request_id,
            "name": "Create deployment",
            "deploymentId": deployment_id,
            "catalogItemId": catalog_item_id,
            "requestedBy": "fixture-service-account",
            "script": list(script),
            "index": 0,
            "polls": 0,
        }
        self.by_deployment[deployment_id] = request_id
        return deployment_id, request_id

    def request_body(self, record, advance):
        script = record["script"]
        index = record["index"]
        status = script[index]
        if advance and index + 1 < len(script):
            record["index"] = index + 1
        record["polls"] += 1
        total = len(script)
        completed = index + 1 if status in ("SUCCESSFUL", "FAILED") else index
        details = self.fixtures["request_details"].get(status, "")
        return {
            "id": record["id"],
            "name": record["name"],
            "deploymentId": record["deploymentId"],
            "catalogItemId": record["catalogItemId"],
            "requestedBy": record["requestedBy"],
            "status": status,
            "details": details,
            "completedTasks": completed,
            "totalTasks": total,
            "cancelable": status not in ("SUCCESSFUL", "FAILED", "ABORTED", "APPROVAL_REJECTED"),
            "dismissed": False,
        }


def _page(items):
    """Wrap items in the paged envelope the contract documents."""
    return {
        "content": items,
        "empty": len(items) == 0,
        "first": True,
        "last": True,
        "number": 0,
        "numberOfElements": len(items),
        "pageable": {"offset": 0, "pageNumber": 0, "pageSize": 20, "paged": True, "unpaged": False},
        "size": 20,
        "sort": {"empty": True, "sorted": False, "unsorted": True},
        "totalElements": len(items),
        "totalPages": 1 if items else 0,
    }


class MockAppliance:
    """Start/stop wrapper around the mock HTTP server."""

    def __init__(self, log_path, contract_path=None, fixtures_path=None):
        self.contract = json.loads(Path(contract_path or _CONTRACT_PATH).read_text())
        self.fixtures = json.loads(Path(fixtures_path or _FIXTURES_PATH).read_text())
        # Catalog item ids are regenerated per run so they cannot be hard-coded
        # from the fixture file and must be resolved by name over the API.
        for item in self.fixtures["catalog_items"]:
            item["id"] = "ci-%s" % uuid.uuid4()
        self.routes = _load_routes(self.contract)
        self.log_path = Path(log_path)
        self.log_path.write_text("")
        self.state = _State(self.fixtures)
        self.lock = threading.Lock()
        self.seq = 0
        self._server = None
        self._thread = None
        self.base_url = None

    # -- lifecycle ------------------------------------------------------
    def start(self):
        appliance = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # silence stderr chatter
                pass

            def _handle(self, method):
                appliance._dispatch(self, method)

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

            def do_PUT(self):
                self._handle("PUT")

            def do_DELETE(self):
                self._handle("DELETE")

            def do_PATCH(self):
                self._handle("PATCH")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        host, port = self._server.server_address[:2]
        self.base_url = "http://%s:%d" % (host, port)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.base_url

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._thread.join(timeout=5)
            self._server = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()

    # -- request log ----------------------------------------------------
    def entries(self):
        """Parsed request log, in order."""
        text = self.log_path.read_text()
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def _record(self, entry):
        with self.log_path.open("a") as handle:
            handle.write(json.dumps(entry) + "\n")

    # -- dispatch -------------------------------------------------------
    def _dispatch(self, handler, method):
        split = urlsplit(handler.path)
        path = split.path
        raw_query = split.query
        length = int(handler.headers.get("Content-Length") or 0)
        raw_body = handler.rfile.read(length).decode("utf-8") if length else ""

        with self.lock:
            self.seq += 1
            seq = self.seq
            route, params = _match(self.routes, method, path)
            entry = {
                "seq": seq,
                "method": method,
                "path": path,
                "operation_id": route["operation_id"] if route else None,
                "matched_contract_operation": route is not None,
                "path_params": params or {},
                "query_raw": raw_query,
                "query_pairs": parse_qsl(raw_query, keep_blank_values=True),
                "headers": {
                    "authorization": handler.headers.get("Authorization"),
                    "content-type": handler.headers.get("Content-Type"),
                    "accept": handler.headers.get("Accept"),
                },
                "body_raw": raw_body,
                "body_json": None,
                "form_pairs": None,
                "auth_token_id": None,
            }
            content_type = (handler.headers.get("Content-Type") or "").split(";")[0].strip()
            if raw_body and content_type == "application/json":
                try:
                    entry["body_json"] = json.loads(raw_body)
                except ValueError:
                    entry["body_json"] = None
            if raw_body and content_type == "application/x-www-form-urlencoded":
                entry["form_pairs"] = parse_qsl(raw_body, keep_blank_values=True)

            status, payload = self._respond(route, params, entry, handler)
            entry["status"] = status
            self._record(entry)

        body = json.dumps(payload).encode("utf-8") if payload is not None else b""
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        if body:
            handler.wfile.write(body)

    def _respond(self, route, params, entry, handler):
        if route is None:
            return 404, {"message": "No such operation on this appliance."}

        operation_id = route["operation_id"]
        if operation_id == "issueAccessToken":
            return self._issue_token(entry)

        # Everything else is a bearer-protected operation.
        auth = entry["headers"]["authorization"] or ""
        if not auth.startswith("Bearer "):
            return 401, {"message": "Missing or malformed bearer token."}
        token_id, reason = self.state.check_bearer(auth[len("Bearer "):].strip())
        entry["auth_token_id"] = token_id
        if reason is not None:
            entry["auth_failure"] = reason
            # The published contract defines no 401 response body. Returning
            # an empty body ensures clients recover from the status code alone
            # rather than depending on an undocumented error-message shape.
            return 401, None

        if operation_id == "listCatalogItems":
            return 200, _page(self.fixtures["catalog_items"])

        if operation_id == "requestCatalogItem":
            return self._request_catalog_item(params, entry)

        if operation_id == "listDeploymentRequests":
            deployment_id = params["deploymentId"]
            request_id = self.state.by_deployment.get(deployment_id)
            if request_id is None:
                return 404, {"message": "Deployment not found."}
            record = self.state.requests[request_id]
            return 200, _page([self.state.request_body(record, advance=False)])

        if operation_id == "getRequest":
            record = self.state.requests.get(params["requestId"])
            if record is None:
                return 404, {"message": "Request not found."}
            return 200, self.state.request_body(record, advance=True)

        return 404, {"message": "No such operation on this appliance."}

    def _issue_token(self, entry):
        auth = entry["headers"]["authorization"] or ""
        if not auth.startswith("Basic "):
            return 400, {"message": "Invalid authorization header."}
        try:
            decoded = base64.b64decode(auth[len("Basic "):].strip()).decode("utf-8")
            client_id, _, client_secret = decoded.partition(":")
        except Exception:
            return 400, {"message": "Invalid authorization header."}
        if client_id != self.state.client_id or client_secret != self.state.client_secret:
            return 400, {"message": "Invalid authorization header."}

        content_type = (entry["headers"]["content-type"] or "").split(";")[0].strip()
        if content_type != "application/x-www-form-urlencoded":
            return 400, {"message": "Invalid request body."}
        form = dict(entry["form_pairs"] or [])
        if form.get("grant_type") != "refresh_token":
            return 400, {"message": "Invalid request body."}
        if form.get("refresh_token") != self.state.refresh_token:
            return 400, {"message": "Invalid request body."}

        access_token = self.state.issue_token()
        return 200, {
            "access_token": access_token,
            "refresh_token": self.state.refresh_token,
            "id_token": "eyJpZCI6ImZpeHR1cmUifQ",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "csp:org_member",
        }

    def _request_catalog_item(self, params, entry):
        catalog_item_id = params["id"]
        known = {item["id"] for item in self.fixtures["catalog_items"]}
        if catalog_item_id not in known:
            return 404, {"message": "Catalog item not found."}
        body = entry["body_json"]
        if not isinstance(body, dict):
            return 400, {"message": "Invalid request body."}
        name = body.get("deploymentName")
        project_id = body.get("projectId")
        if not name or not project_id:
            return 400, {"message": "deploymentName and projectId are required."}
        count = body.get("bulkRequestCount", 1) or 1
        created = []
        for index in range(count):
            instance_name = name if count == 1 else "%s-%d" % (name, index + 1)
            deployment_id, _ = self.state.create_deployment(
                catalog_item_id, instance_name, project_id
            )
            created.append({"deploymentId": deployment_id, "deploymentName": instance_name})
        return 200, created


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Run the VCF Automation mock appliance.")
    parser.add_argument("--log", default="mock-requests.jsonl")
    args = parser.parse_args()
    appliance = MockAppliance(log_path=args.log)
    url = appliance.start()
    print("mock appliance listening on %s" % url)
    print("request log: %s" % appliance.log_path)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        appliance.stop()


if __name__ == "__main__":
    main()
