"""Acceptance harness for the SAP Cloud Integration artifact deployer.

A loopback fake tenant serves the Integration Content OData API wire contract
pinned in docs/contract.json (CSRF fetch flow, design-time artifact upload,
DeployIntegrationDesigntimeArtifact, BuildAndDeployStatus polling, runtime
artifact status, error-information $value). No real tenant, no credentials,
no sleeps — all waiting goes through an injected callable.

Run with: python3 test_cpi_deploy.py
Protected — do not modify this file or anything under docs/.
"""

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

USER = "cpi-deployer"
PASS = "dummy-fixture-secret-a41"
AUTH = "Basic " + base64.b64encode(f"{USER}:{PASS}".encode()).decode()
CSRF_TOKEN = "f31d9c1e-dummy-token-77"
COOKIE = "JSESSIONID=8B1FDA7E2C4D5A; Path=/; Secure"

CHECKS = 0


def check(cond, msg):
    global CHECKS
    if not cond:
        raise AssertionError(msg)
    CHECKS += 1


def check_eq(got, want, msg):
    check(got == want, f"{msg} — got {got!r}, want {want!r}")


class MockTenant:
    """Records every request and answers from a per-test script."""

    def __init__(self, serve):
        self.requests = []
        self._serve = serve
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _handle(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                rec = {
                    "method": self.command,
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": body,
                }
                n = len(outer.requests)
                outer.requests.append(rec)
                try:
                    status, headers, payload = outer._serve(n, rec)
                except Exception as e:  # scripting mistake, not client behavior
                    status, headers, payload = 599, {}, str(e).encode()
                if isinstance(payload, str):
                    payload = payload.encode()
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = _handle

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def modifying(self):
        return [r for r in self.requests if r["method"] in ("POST", "PUT", "DELETE")]


def d_results(rows, **extra):
    body = {"results": rows}
    body.update(extra)
    return json.dumps({"d": body})


def versions_feed(*versions):
    rows = [
        {
            "__metadata": {"type": "com.sap.hci.api.IntegrationDesigntimeArtifact"},
            "Id": "Order_Relay",
            "Version": v,
            "Name": "Order Relay",
            "PackageId": "LogisticsIntegration",
        }
        for v in versions
    ]
    return d_results(rows)


def is_csrf_fetch(rec):
    return (
        rec["method"] == "GET"
        and (rec["headers"].get("x-csrf-token") or "").lower() == "fetch"
    )


CSRF_REPLY = (200, {"X-CSRF-Token": CSRF_TOKEN, "Set-Cookie": COOKIE}, "{}")


def check_common(rec):
    check_eq(rec["headers"].get("authorization"), AUTH, "basic auth on every request")


def check_modifying(rec):
    check_common(rec)
    check_eq(rec["headers"].get("x-csrf-token"), CSRF_TOKEN, "cached CSRF token on modifying call")
    cookie = rec["headers"].get("cookie") or ""
    check("JSESSIONID=8B1FDA7E2C4D5A" in cookie, f"session cookie forwarded, got {cookie!r}")


# ----------------------------------------------------------------- scenarios


def test_list_versions_query_and_ordering():
    def serve(n, rec):
        check_common(rec)
        check_eq(
            rec["path"],
            "/api/v1/IntegrationDesigntimeArtifacts?$format=json&$filter=Id%20eq%20'Order_Relay'",
            "documented $filter query, percent-encoded",
        )
        return 200, {"Content-Type": "application/json"}, versions_feed("1.9.0", "1.10.0", "1.2.3")

    mock = MockTenant(serve)
    try:
        from cpideploy.client import CpiClient
        from cpideploy.artifacts import list_versions

        client = CpiClient(mock.base_url, USER, PASS)
        rows = list_versions(client, "Order_Relay")
        check_eq([r["Version"] for r in rows], ["1.2.3", "1.9.0", "1.10.0"],
                 "versions ordered numerically (1.10 > 1.9, not string order)")
        check_eq(rows[0]["Id"], "Order_Relay", "Id preserved")
        check_eq(rows[0]["PackageId"], "LogisticsIntegration", "PackageId preserved")
        check_eq(len(mock.requests), 1, "reads need no CSRF pre-flight")
    finally:
        mock.close()


PAYLOAD = bytes(range(256)) * 3 + b"\x00\xff\x00PK\x03\x04 not really a zip"


def test_upload_flow_binary_payload_and_csrf():
    def serve(n, rec):
        if rec["method"] == "GET" and "$filter" in rec["path"]:
            check_common(rec)
            return 200, {"Content-Type": "application/json"}, versions_feed("1.3.9")
        if is_csrf_fetch(rec):
            check_common(rec)
            return CSRF_REPLY
        if rec["method"] == "PUT":
            check_modifying(rec)
            check_eq(
                rec["path"],
                "/api/v1/IntegrationDesigntimeArtifacts(Id='Order_Relay',Version='active')",
                "draft update targets the documented (Id,Version='active') key",
            )
            check_eq(rec["headers"].get("content-type"), "application/json", "JSON body for the draft update")
            body = json.loads(rec["body"].decode())
            check_eq(body.get("Name"), "Order Relay", "artifact Name carried in the PUT body")
            content = body.get("ArtifactContent")
            check(isinstance(content, str), "ArtifactContent must be a base64 string")
            check("\n" not in content and "\r" not in content, "base64 must be unwrapped (no newlines)")
            check_eq(base64.b64decode(content), PAYLOAD, "payload survives base64 byte-exactly")
            return 200, {"Content-Type": "application/json"}, "{}"
        if rec["method"] == "POST":
            check_modifying(rec)
            check_eq(
                rec["path"],
                "/api/v1/IntegrationDesigntimeArtifactSaveAsVersion?Id='Order_Relay'&SaveAsVersion='1.4.0'",
                "save-as-version uses single-quoted query parameters",
            )
            return 201, {"Content-Type": "application/json"}, "{}"
        return 599, {}, "unexpected request %r" % rec["path"]

    mock = MockTenant(serve)
    try:
        from cpideploy.client import CpiClient
        from cpideploy.artifacts import upload_new_version

        client = CpiClient(mock.base_url, USER, PASS)
        report = upload_new_version(
            client, "Order_Relay", "Order Relay", "1.4.0", PAYLOAD
        )
        check_eq(report["id"], "Order_Relay", "report names the artifact")
        check_eq(report["version"], "1.4.0", "report names the version")
        methods = [r["method"] for r in mock.requests]
        check_eq(methods.count("PUT"), 1, "exactly one draft update")
        check_eq(methods.count("POST"), 1, "exactly one save-as-version")
        fetches = [r for r in mock.requests if is_csrf_fetch(r)]
        check_eq(len(fetches), 1, "CSRF token fetched once and cached across both writes")
        put_i = methods.index("PUT")
        fetch_i = mock.requests.index(fetches[0])
        check(fetch_i < put_i, "token fetch precedes the first modifying call")
    finally:
        mock.close()


def test_conflict_refuses_to_overwrite_newer_version():
    def serve(n, rec):
        if rec["method"] == "GET" and "$filter" in rec["path"]:
            return 200, {"Content-Type": "application/json"}, versions_feed("1.3.0", "1.4.0")
        if is_csrf_fetch(rec):
            return CSRF_REPLY
        return 599, {}, "no modifying call may happen on conflict"

    mock = MockTenant(serve)
    try:
        from cpideploy.client import CpiClient
        from cpideploy.artifacts import upload_new_version, VersionConflictError

        client = CpiClient(mock.base_url, USER, PASS)
        try:
            upload_new_version(client, "Order_Relay", "Order Relay", "1.4.0", b"zip")
            check(False, "equal remote version must raise VersionConflictError")
        except VersionConflictError as e:
            check("1.4.0" in str(e), "conflict names the target version")
        check_eq(mock.modifying(), [], "no PUT/POST issued on an equal version")

        mock.requests.clear()
        try:
            upload_new_version(client, "Order_Relay", "Order Relay", "1.3.10", b"zip")
            check(False, "older target than remote 1.4.0 must raise VersionConflictError")
        except VersionConflictError as e:
            check("1.4.0" in str(e), "conflict reports the newer remote version")
        check_eq(mock.modifying(), [], "no PUT/POST issued when remote is newer")
    finally:
        mock.close()


def test_deploy_poll_and_runtime_success():
    state = {"status_polls": 0, "runtime_polls": 0}

    def serve(n, rec):
        if is_csrf_fetch(rec):
            return CSRF_REPLY
        if rec["method"] == "POST":
            check_modifying(rec)
            check_eq(
                rec["path"],
                "/api/v1/DeployIntegrationDesigntimeArtifact?Id='Order_Relay'&Version='1.4.0'",
                "deploy action with single-quoted Id/Version query parameters",
            )
            return 202, {"Content-Type": "text/plain"}, "9f2a77aa-task-0001"
        if rec["path"].startswith("/api/v1/BuildAndDeployStatus"):
            check_common(rec)
            check_eq(
                rec["path"],
                "/api/v1/BuildAndDeployStatus(TaskId='9f2a77aa-task-0001')?$format=json",
                "status polled by the returned TaskId",
            )
            state["status_polls"] += 1
            status = "DEPLOYING" if state["status_polls"] < 3 else "SUCCESS"
            return 200, {"Content-Type": "application/json"}, json.dumps(
                {"d": {"TaskId": "9f2a77aa-task-0001", "Status": status}}
            )
        if rec["path"].startswith("/api/v1/IntegrationRuntimeArtifacts"):
            check_common(rec)
            check_eq(
                rec["path"],
                "/api/v1/IntegrationRuntimeArtifacts('Order_Relay')?$format=json",
                "runtime status read for the deployed artifact",
            )
            state["runtime_polls"] += 1
            status = "STARTING" if state["runtime_polls"] < 2 else "STARTED"
            return 200, {"Content-Type": "application/json"}, json.dumps(
                {"d": {"Id": "Order_Relay", "Version": "1.4.0", "Status": status}}
            )
        return 599, {}, "unexpected %r" % rec["path"]

    mock = MockTenant(serve)
    try:
        from cpideploy.client import CpiClient
        from cpideploy.artifacts import deploy_and_wait

        waits = []
        client = CpiClient(mock.base_url, USER, PASS)
        report = deploy_and_wait(
            client, "Order_Relay", "1.4.0", wait=waits.append, max_polls=10
        )
        check_eq(report["task_id"], "9f2a77aa-task-0001", "task id from the deploy response body")
        check_eq(report["build_status"], "SUCCESS", "terminal build status reported")
        check_eq(report["runtime_status"], "STARTED", "terminal runtime status reported")
        check_eq(state["status_polls"], 3, "polls until the first terminal build status")
        check_eq(state["runtime_polls"], 2, "polls until the runtime status is final")
        check_eq(len(waits), 3, "one injected wait per non-terminal poll (2 DEPLOYING + 1 STARTING)")
        check(all(isinstance(w, (int, float)) and w > 0 for w in waits), "waits are positive durations")
    finally:
        mock.close()


def test_deploy_build_failure_stops_before_runtime():
    def serve(n, rec):
        if is_csrf_fetch(rec):
            return CSRF_REPLY
        if rec["method"] == "POST":
            return 202, {"Content-Type": "text/plain"}, "task-fail-01"
        if rec["path"].startswith("/api/v1/BuildAndDeployStatus"):
            return 200, {"Content-Type": "application/json"}, json.dumps(
                {"d": {"TaskId": "task-fail-01", "Status": "FAIL"}}
            )
        return 599, {}, "runtime must not be queried after FAIL: %r" % rec["path"]

    mock = MockTenant(serve)
    try:
        from cpideploy.client import CpiClient
        from cpideploy.artifacts import deploy_and_wait, DeploymentError

        client = CpiClient(mock.base_url, USER, PASS)
        try:
            deploy_and_wait(client, "Order_Relay", "1.4.0", wait=lambda s: None, max_polls=5)
            check(False, "FAIL build status must raise DeploymentError")
        except DeploymentError as e:
            check("FAIL" in str(e), "error names the terminal build status")
        check(
            not any("IntegrationRuntimeArtifacts" in r["path"] for r in mock.requests),
            "no runtime queries after a failed build",
        )
    finally:
        mock.close()


def test_runtime_error_surfaces_error_information_value():
    def serve(n, rec):
        if is_csrf_fetch(rec):
            return CSRF_REPLY
        if rec["method"] == "POST":
            return 202, {"Content-Type": "text/plain"}, "task-err-02"
        if rec["path"].startswith("/api/v1/BuildAndDeployStatus"):
            return 200, {"Content-Type": "application/json"}, json.dumps(
                {"d": {"TaskId": "task-err-02", "Status": "SUCCESS"}}
            )
        if rec["path"] == "/api/v1/IntegrationRuntimeArtifacts('Order_Relay')?$format=json":
            return 200, {"Content-Type": "application/json"}, json.dumps(
                {"d": {"Id": "Order_Relay", "Version": "1.4.0", "Status": "ERROR"}}
            )
        if rec["path"] == "/api/v1/IntegrationRuntimeArtifacts('Order_Relay')/ErrorInformation/$value":
            check_common(rec)
            return 200, {"Content-Type": "application/json"}, json.dumps(
                {"message": {"subsystemName": "CONTENT_DEPLOYMENT", "messageId": "Credential 'wms_api' not found"}}
            )
        return 599, {}, "unexpected %r" % rec["path"]

    mock = MockTenant(serve)
    try:
        from cpideploy.client import CpiClient
        from cpideploy.artifacts import deploy_and_wait, DeploymentError

        client = CpiClient(mock.base_url, USER, PASS)
        try:
            deploy_and_wait(client, "Order_Relay", "1.4.0", wait=lambda s: None, max_polls=5)
            check(False, "runtime ERROR must raise DeploymentError")
        except DeploymentError as e:
            check("Credential 'wms_api' not found" in str(e),
                  "the ErrorInformation/$value detail is surfaced to the operator")
        check(
            any(r["path"].endswith("/ErrorInformation/$value") for r in mock.requests),
            "error detail read from the documented $value resource",
        )
    finally:
        mock.close()


def test_polling_gives_up_after_max_polls():
    polls = {"n": 0}

    def serve(n, rec):
        if is_csrf_fetch(rec):
            return CSRF_REPLY
        if rec["method"] == "POST":
            return 202, {"Content-Type": "text/plain"}, "task-slow-03"
        if rec["path"].startswith("/api/v1/BuildAndDeployStatus"):
            polls["n"] += 1
            return 200, {"Content-Type": "application/json"}, json.dumps(
                {"d": {"TaskId": "task-slow-03", "Status": "DEPLOYING"}}
            )
        return 599, {}, "unexpected %r" % rec["path"]

    mock = MockTenant(serve)
    try:
        from cpideploy.client import CpiClient
        from cpideploy.artifacts import deploy_and_wait, DeploymentError

        waits = []
        client = CpiClient(mock.base_url, USER, PASS)
        try:
            deploy_and_wait(client, "Order_Relay", "1.4.0", wait=waits.append, max_polls=3)
            check(False, "a never-terminal status must raise DeploymentError")
        except DeploymentError as e:
            check("task-slow-03" in str(e), "timeout error names the task id")
        check_eq(polls["n"], 3, "exactly max_polls status reads")
        check_eq(len(waits), 3, "waiting is injected, never slept inline")
    finally:
        mock.close()


def test_odata_error_document_preserved():
    def serve(n, rec):
        if is_csrf_fetch(rec):
            return CSRF_REPLY
        if rec["method"] == "GET":
            return 200, {"Content-Type": "application/json"}, versions_feed("1.0.0")
        if rec["method"] == "PUT":
            return 200, {"Content-Type": "application/json"}, "{}"
        if rec["method"] == "POST":
            return (
                400,
                {"Content-Type": "application/json"},
                json.dumps(
                    {
                        "error": {
                            "code": "Save as version failed",
                            "message": {"lang": "en", "value": "Version 2.0.0 already exists for Order_Relay"},
                            "innererror": {"application": {"component_id": "BC-CP-IS-CI"}},
                        }
                    }
                ),
            )
        return 599, {}, "unexpected"

    mock = MockTenant(serve)
    try:
        from cpideploy.client import CpiClient, CpiApiError
        from cpideploy.artifacts import upload_new_version

        client = CpiClient(mock.base_url, USER, PASS)
        try:
            upload_new_version(client, "Order_Relay", "Order Relay", "2.0.0", b"zip")
            check(False, "server-side rejection must raise CpiApiError")
        except CpiApiError as e:
            check_eq(e.status, 400, "HTTP status preserved")
            check_eq(e.code, "Save as version failed", "OData error.code preserved")
            check("Version 2.0.0 already exists" in str(e), "message.value surfaced")
            check_eq(
                e.error_body["innererror"]["application"]["component_id"],
                "BC-CP-IS-CI",
                "SAP innererror preserved verbatim",
            )
            check(PASS not in str(e) and AUTH not in str(e), "credentials never leak into errors")
    finally:
        mock.close()


def main():
    tests = [
        test_list_versions_query_and_ordering,
        test_upload_flow_binary_payload_and_csrf,
        test_conflict_refuses_to_overwrite_newer_version,
        test_deploy_poll_and_runtime_success,
        test_deploy_build_failure_stops_before_runtime,
        test_runtime_error_surfaces_error_information_value,
        test_polling_gives_up_after_max_polls,
        test_odata_error_document_preserved,
    ]
    for t in tests:
        t()
        print(f"ok   {t.__name__}")
    print(f"OK — {len(tests)} scenarios, {CHECKS} checks")


if __name__ == "__main__":
    main()
