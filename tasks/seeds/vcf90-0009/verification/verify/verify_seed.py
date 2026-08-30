#!/usr/bin/env python3
"""Protected acceptance check for the bundle-download integration.

Runs ``sddcbundle`` against the loopback mock in ``tools/mock_sddc_manager.py``
and asserts the exact wire shape of every request it makes, plus the internal
consistency of the derived artifacts in ``docs/``.

No live VMware endpoint is contacted: the only socket opened is 127.0.0.1.

Usage:  python3 verify/verify_seed.py
Exit code 0 means every check passed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import traceback

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOCK = os.path.join(REPO_ROOT, "tools", "mock_sddc_manager.py")
CONTRACT_PATH = os.path.join(REPO_ROOT, "docs", "contract.json")
SOURCES_PATH = os.path.join(REPO_ROOT, "docs", "official_sources.json")

sys.path.insert(0, REPO_ROOT)

USERNAME = "administrator@vsphere.local"
PASSWORD = "VMw@re1!VMw@re1!"
ACCESS_TOKEN = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.mock-access-token"
REFRESH_ID = "0f5bd4f0-9a11-4e0e-9a2f-8d1ff5c9a001"

BUNDLE_A = "e6ba8240-d9b7-11ef-bf62-63832c57ab1a"
TASK_A = "7b3f1a54-2c8e-4a6b-9d21-0c4e5f8a1b30"
BUNDLE_B = "a1c93312-1f44-11f0-9c3d-0242ac120002"
TASK_B = "c9d20e77-51aa-4bd0-8f0e-6a2c7b91d4e5"

# The 9.1.0.0 revision of the same file is a different (wrong) source for this
# integration; its commit is rejected explicitly.
TAG_9_1_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/sddc-manager/sddc-manager-openapi.json"
SPEC_TAG = "9.0.0.0"
SPEC_REPO = "vmware/vcf-api-specs"
SPEC_COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
SPEC_PERMALINK = (
    "https://github.com/vmware/vcf-api-specs/blob/%s/%s" % (SPEC_COMMIT, SPEC_PATH)
)

EXPECTED_OPERATIONS = {
    "createToken": {
        "method": "POST",
        "path": "/v1/tokens",
        "success_status": 201,
        "path_parameters": [],
        "request_schema": "TokenCreationSpec",
        "response_schema": "TokenPair",
    },
    "startBundleDownloadByID": {
        "method": "PATCH",
        "path": "/v1/bundles/{id}",
        "success_status": 202,
        "path_parameters": ["id"],
        "request_schema": "BundleUpdateSpec",
        "response_schema": "Task",
    },
    "getTask": {
        "method": "GET",
        "path": "/v1/tasks/{id}",
        "success_status": 200,
        "path_parameters": ["id"],
        "response_schema": "Task",
    },
}

# Exact projection of required lists, property names, JSON types and component
# references from the pinned 9.0.0.0 specification. Descriptions and other
# explanatory metadata may be included by a solution, but cannot substitute
# for or alter this contract surface.
EXPECTED_SCHEMAS = {
    "BundleDownloadSpec": {
        "required": [],
        "properties": {
            "scheduledTimestamp": {"type": "string"},
            "downloadNow": {"type": "boolean"},
            "cancelNow": {"type": "boolean"},
        },
    },
    "BundleUpdateSpec": {
        "required": [],
        "properties": {
            "bundleDownloadSpec": {"type": "object", "schema": "BundleDownloadSpec"},
        },
    },
    "DocumentationLink": {
        "required": [],
        "properties": {"url": {"type": "string"}, "label": {"type": "string"}},
    },
    "Error": {
        "required": [],
        "properties": {
            "errorCode": {"type": "string"},
            "errorType": {"type": "string"},
            "arguments": {"type": "array", "items": {"type": "string"}},
            "context": {"type": "object"},
            "message": {"type": "string"},
            "remediationMessage": {"type": "string"},
            "causes": {
                "type": "array",
                "items": {"type": "object", "schema": "ErrorCause"},
            },
            "nestedErrors": {
                "type": "array",
                "items": {"type": "object", "schema": "Error"},
            },
            "referenceToken": {"type": "string"},
            "label": {"type": "string"},
            "remediationUrl": {"type": "string"},
        },
    },
    "ErrorCause": {
        "required": [],
        "properties": {"type": {"type": "string"}, "message": {"type": "string"}},
    },
    "MessagePack": {
        "required": ["messageKey"],
        "properties": {
            "component": {"type": "string"},
            "messageKey": {"type": "string"},
            "arguments": {"type": "array", "items": {"type": "string"}},
            "message": {"type": "string"},
            "bundle": {"type": "string"},
        },
    },
    "RefreshToken": {"required": [], "properties": {"id": {"type": "string"}}},
    "Resource": {
        "required": ["resourceId", "type"],
        "properties": {
            "resourceId": {"type": "string"},
            "fqdn": {"type": "string"},
            "type": {"type": "string"},
            "name": {"type": "string"},
            "sans": {"type": "array", "items": {"type": "string"}},
        },
    },
    "Stage": {
        "required": ["creationTimestamp", "description", "name", "status", "type"],
        "properties": {
            "name": {"type": "string"},
            "type": {"type": "string"},
            "description": {"type": "string"},
            "status": {"type": "string"},
            "creationTimestamp": {"type": "string"},
            "completionTimestamp": {"type": "string"},
            "errors": {
                "type": "array",
                "items": {"type": "object", "schema": "Error"},
            },
        },
    },
    "SubTask": {
        "required": ["creationTimestamp", "description", "name", "status"],
        "properties": {
            "name": {"type": "string"},
            "type": {"type": "string"},
            "description": {"type": "string"},
            "status": {"type": "string"},
            "creationTimestamp": {"type": "string"},
            "completionTimestamp": {"type": "string"},
            "stages": {
                "type": "array",
                "items": {"type": "object", "schema": "Stage"},
            },
            "errors": {
                "type": "array",
                "items": {"type": "object", "schema": "Error"},
            },
            "resources": {
                "type": "array",
                "items": {"type": "object", "schema": "Resource"},
            },
            "subTasks": {
                "type": "array",
                "items": {"type": "object", "schema": "SubTask"},
            },
            "documentationLink": {"type": "object", "schema": "DocumentationLink"},
        },
    },
    "Task": {
        "required": ["creationTimestamp", "id", "name", "status"],
        "properties": {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "localizableDescriptionPack": {"type": "object", "schema": "MessagePack"},
            "type": {"type": "string"},
            "status": {"type": "string"},
            "creationTimestamp": {"type": "string"},
            "completionTimestamp": {"type": "string"},
            "subTasks": {
                "type": "array",
                "items": {"type": "object", "schema": "SubTask"},
            },
            "errors": {
                "type": "array",
                "items": {"type": "object", "schema": "Error"},
            },
            "resources": {
                "type": "array",
                "items": {"type": "object", "schema": "Resource"},
            },
            "resolutionStatus": {"type": "string"},
            "isCancellable": {"type": "boolean"},
            "isRetryable": {"type": "boolean"},
        },
    },
    "TokenCreationSpec": {
        "required": [],
        "properties": {
            "username": {"type": "string"},
            "password": {"type": "string"},
            "apiKey": {"type": "string"},
            "idToken": {"type": "string"},
        },
    },
    "TokenPair": {
        "required": [],
        "properties": {
            "accessToken": {"type": "string"},
            "refreshToken": {"type": "object", "schema": "RefreshToken"},
        },
    },
}

JSON_TYPES = {
    "string": str,
    "boolean": bool,
    "integer": int,
    "number": float,
    "object": dict,
    "array": list,
}


class Checker:
    def __init__(self):
        self.failures = []
        self.passed = 0

    def ok(self, name):
        self.passed += 1
        print("  PASS  %s" % name)

    def fail(self, name, detail):
        self.failures.append((name, detail))
        print("  FAIL  %s\n        %s" % (name, detail))

    def check(self, name, condition, detail=""):
        if condition:
            self.ok(name)
        else:
            self.fail(name, detail or "condition was false")
        return bool(condition)

    def equal(self, name, actual, expected):
        return self.check(
            name,
            actual == expected,
            "expected %r, got %r" % (expected, actual),
        )

    def section(self, title):
        print("\n== %s" % title)


class Mock:
    """Runs tools/mock_sddc_manager.py as a subprocess on an ephemeral port."""

    def __init__(self, tmpdir, name, scenario):
        self.scenario_path = os.path.join(tmpdir, "%s-scenario.json" % name)
        self.log_path = os.path.join(tmpdir, "%s-requests.jsonl" % name)
        with open(self.scenario_path, "w", encoding="utf-8") as fh:
            json.dump(scenario, fh)
        self.proc = None
        self.base_url = None

    def __enter__(self):
        self.proc = subprocess.Popen(
            [
                sys.executable,
                MOCK,
                "--scenario",
                self.scenario_path,
                "--log",
                self.log_path,
                "--port",
                "0",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        line = self.proc.stdout.readline()
        if not line.startswith("MOCK_READY"):
            raise RuntimeError("mock did not start: %r %s" % (line, self.proc.stderr.read()))
        port = json.loads(line.split(" ", 1)[1])["port"]
        self.base_url = "http://127.0.0.1:%d" % port
        return self

    def __exit__(self, *exc):
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                self.proc.kill()
        return False

    def log(self):
        if not os.path.exists(self.log_path):
            return []
        with open(self.log_path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]


def scenario(bundles):
    return {
        "username": USERNAME,
        "password": PASSWORD,
        "access_token": ACCESS_TOKEN,
        "refresh_token_id": REFRESH_ID,
        "bundles": bundles,
    }


def bundle_cfg(task_id, statuses, errors=None):
    cfg = {"task_id": task_id, "task_name": "Downloading Bundle", "statuses": statuses}
    if errors:
        cfg["errors"] = errors
    return cfg


def client_for(mod, mock, **kwargs):
    return mod.BundleDownloadClient(mock.base_url, USERNAME, PASSWORD, **kwargs)


# --------------------------------------------------------------------------
# shared wire-shape assertions
# --------------------------------------------------------------------------
def assert_clean_traffic(chk, prefix, log):
    """Nothing off-contract, nothing rejected."""
    off = [e for e in log if e.get("operation") not in
           ("op_create_token", "op_start_bundle_download", "op_get_task")]
    chk.check(
        "%s: every request hit a contracted operation" % prefix,
        not off,
        "off-contract requests: %s" % [(e["method"], e["path"]) for e in off],
    )
    rejected = [e for e in log if e["response_status"] >= 300]
    chk.check(
        "%s: mock accepted every request" % prefix,
        not rejected,
        "rejected: %s"
        % [(e["method"], e["path"], e["response_status"], e.get("body")) for e in rejected],
    )
    bad_accept = [
        e for e in log if "application/json" not in (e["headers"].get("accept") or "")
    ]
    chk.check(
        "%s: every request sent Accept: application/json" % prefix,
        not bad_accept,
        "missing/incorrect Accept on: %s" % [(e["method"], e["path"]) for e in bad_accept],
    )
    with_query = [e for e in log if e["query"]]
    chk.check(
        "%s: no identifiers smuggled into the query string" % prefix,
        not with_query,
        "requests with a query string: %s" % [(e["path"], e["query"]) for e in with_query],
    )


def assert_token_request(chk, prefix, log, expected_count=1):
    tokens = [e for e in log if e["operation"] == "op_create_token"]
    chk.equal("%s: POST /v1/tokens issued exactly %d time(s)" % (prefix, expected_count),
              len(tokens), expected_count)
    if not tokens:
        return
    entry = tokens[0]
    chk.equal("%s: token request method/path" % prefix,
              (entry["method"], entry["path"]), ("POST", "/v1/tokens"))
    chk.equal(
        "%s: TokenCreationSpec carries username+password only "
        "(apiKey/idToken omitted, not empty)" % prefix,
        entry["body"],
        {"username": USERNAME, "password": PASSWORD},
    )
    chk.check(
        "%s: token request declares a JSON content type" % prefix,
        (entry["headers"].get("content-type") or "").startswith("application/json"),
        "content-type was %r" % entry["headers"].get("content-type"),
    )
    chk.check(
        "%s: token request carries no Authorization header" % prefix,
        entry["headers"].get("authorization") is None,
        "authorization was %r" % entry["headers"].get("authorization"),
    )


def assert_start_request(chk, prefix, log, bundle_id, expected_spec):
    starts = [e for e in log if e["operation"] == "op_start_bundle_download"
              and e["path"] == "/v1/bundles/%s" % bundle_id]
    if not chk.equal("%s: exactly one PATCH /v1/bundles/{id}" % prefix, len(starts), 1):
        return
    entry = starts[0]
    chk.equal("%s: start uses PATCH on the bundle resource path" % prefix,
              (entry["method"], entry["path"]), ("PATCH", "/v1/bundles/%s" % bundle_id))
    chk.equal(
        "%s: BundleUpdateSpec body is exactly the fields that were set" % prefix,
        entry["body"],
        {"bundleDownloadSpec": expected_spec},
    )
    chk.equal(
        "%s: start request presents the issued bearer token" % prefix,
        entry["headers"].get("authorization"),
        "Bearer %s" % ACCESS_TOKEN,
    )
    chk.check(
        "%s: start request declares a JSON content type" % prefix,
        (entry["headers"].get("content-type") or "").startswith("application/json"),
        "content-type was %r" % entry["headers"].get("content-type"),
    )


def assert_polling(chk, prefix, log, task_id, expected_polls):
    polls = [e for e in log if e["operation"] == "op_get_task"]
    chk.equal("%s: polled GET /v1/tasks/{id} exactly %d time(s)" % (prefix, expected_polls),
              len(polls), expected_polls)
    wrong_target = [e for e in polls if e["path"] != "/v1/tasks/%s" % task_id]
    chk.check(
        "%s: polled the task id returned by the 202, not the bundle id" % prefix,
        not wrong_target,
        "polled: %s" % [e["path"] for e in wrong_target],
    )
    with_body = [e for e in polls if e["body"] is not None or e["body_raw"]]
    chk.check(
        "%s: poll requests carry no body" % prefix,
        not with_body,
        "bodies: %s" % [e["body_raw"] for e in with_body],
    )
    unauth = [e for e in polls if e["headers"].get("authorization") != "Bearer %s" % ACCESS_TOKEN]
    chk.check(
        "%s: every poll presents the issued bearer token" % prefix,
        not unauth,
        "bad authorization on %d poll(s)" % len(unauth),
    )
    if polls:
        last = max(e["seq"] for e in polls)
        after = [e for e in log if e["seq"] > last]
        chk.check(
            "%s: no further calls once the task reached a terminal state" % prefix,
            not after,
            "requests after the last poll: %s" % [(e["method"], e["path"]) for e in after],
        )


# --------------------------------------------------------------------------
# scenarios
# --------------------------------------------------------------------------
def scenario_immediate(chk, mod, tmpdir):
    chk.section("Immediate download, polled to SUCCESSFUL")
    sc = scenario({BUNDLE_A: bundle_cfg(TASK_A, ["PENDING", "In Progress", "Successful"])})
    with Mock(tmpdir, "immediate", sc) as mock:
        client = client_for(mod, mock, poll_interval=0.01, poll_timeout=10)
        result = client.download_bundle(BUNDLE_A, download_now=True)
        log = mock.log()

    assert_clean_traffic(chk, "immediate", log)
    assert_token_request(chk, "immediate", log)
    assert_start_request(chk, "immediate", log, BUNDLE_A, {"downloadNow": True})
    assert_polling(chk, "immediate", log, TASK_A, 3)

    order = [e["operation"] for e in sorted(log, key=lambda e: e["seq"])]
    chk.equal(
        "immediate: authenticate, then start, then poll",
        order,
        ["op_create_token", "op_start_bundle_download", "op_get_task", "op_get_task",
         "op_get_task"],
    )
    chk.equal("immediate: result task id", result.task_id, TASK_A)
    chk.equal("immediate: result bundle id", result.bundle_id, BUNDLE_A)
    chk.equal("immediate: normalized terminal status", result.status, "SUCCESSFUL")
    chk.equal("immediate: raw status preserved", result.raw_status, "Successful")
    chk.equal("immediate: poll count reported", result.polls, 3)
    chk.check(
        "immediate: terminal task body returned",
        isinstance(result.task, dict) and result.task.get("id") == TASK_A
        and result.task.get("status") == "Successful",
        "task body was %r" % (result.task,),
    )
    return result


def scenario_scheduled(chk, mod, tmpdir):
    chk.section("Scheduled download: downloadNow/cancelNow omitted entirely")
    stamp = "2026-03-01T10:00:00Z"
    sc = scenario({BUNDLE_A: bundle_cfg(TASK_A, ["Pending", "Successful"])})
    with Mock(tmpdir, "scheduled", sc) as mock:
        client = client_for(mod, mock, poll_interval=0.01, poll_timeout=10)
        result = client.download_bundle(BUNDLE_A, scheduled_timestamp=stamp)
        log = mock.log()

    assert_clean_traffic(chk, "scheduled", log)
    assert_start_request(chk, "scheduled", log, BUNDLE_A, {"scheduledTimestamp": stamp})
    assert_polling(chk, "scheduled", log, TASK_A, 2)
    chk.equal("scheduled: normalized terminal status", result.status, "SUCCESSFUL")


def scenario_cancel(chk, mod, tmpdir):
    chk.section("Cancel a download: only cancelNow on the wire")
    sc = scenario({BUNDLE_A: bundle_cfg(TASK_A, ["In Progress", "Cancelled"],
                                        errors=[{"errorCode": "BUNDLE_DOWNLOAD_CANCELLED",
                                                 "errorType": "TASK",
                                                 "message": "Download cancelled by user"}])})
    with Mock(tmpdir, "cancel", sc) as mock:
        client = client_for(mod, mock, poll_interval=0.01, poll_timeout=10)
        raised = None
        try:
            client.download_bundle(BUNDLE_A, cancel_now=True)
        except mod.TaskFailedError as exc:
            raised = exc
        log = mock.log()

    assert_clean_traffic(chk, "cancel", log)
    assert_start_request(chk, "cancel", log, BUNDLE_A, {"cancelNow": True})
    assert_polling(chk, "cancel", log, TASK_A, 2)
    chk.check("cancel: CANCELLED is treated as a non-success terminal state",
              raised is not None, "download_bundle returned instead of raising TaskFailedError")
    if raised is not None:
        chk.equal("cancel: raised status", raised.status, "CANCELLED")
        chk.equal("cancel: raised task id", raised.task_id, TASK_A)


def scenario_failure(chk, mod, tmpdir):
    chk.section("Failed task: surfaced, not swallowed")
    errors = [{"errorCode": "BUNDLE_DOWNLOAD_FAILED", "errorType": "TASK",
               "message": "Insufficient space on the SDDC Manager appliance"}]
    sc = scenario({BUNDLE_A: bundle_cfg(TASK_A, ["PENDING", "IN_PROGRESS", "FAILED"],
                                        errors=errors)})
    with Mock(tmpdir, "failure", sc) as mock:
        client = client_for(mod, mock, poll_interval=0.01, poll_timeout=10)
        raised = None
        try:
            client.download_bundle(BUNDLE_A, download_now=True)
        except mod.TaskFailedError as exc:
            raised = exc
        log = mock.log()

    assert_clean_traffic(chk, "failure", log)
    assert_polling(chk, "failure", log, TASK_A, 3)
    if not chk.check("failure: TaskFailedError raised", raised is not None,
                     "download_bundle did not raise"):
        return None
    chk.equal("failure: raised status", raised.status, "FAILED")
    chk.equal("failure: raised task id", raised.task_id, TASK_A)
    chk.equal("failure: task errors carried on the exception", raised.errors, errors)
    return raised.task


def scenario_warning(chk, mod, tmpdir):
    chk.section("COMPLETED_WITH_WARNING is terminal and successful")
    sc = scenario({BUNDLE_A: bundle_cfg(TASK_A, ["In Progress", "COMPLETED_WITH_WARNING"])})
    with Mock(tmpdir, "warning", sc) as mock:
        client = client_for(mod, mock, poll_interval=0.01, poll_timeout=10)
        result = client.download_bundle(BUNDLE_A, download_now=True)
        log = mock.log()

    assert_clean_traffic(chk, "warning", log)
    assert_polling(chk, "warning", log, TASK_A, 2)
    chk.equal("warning: normalized status", result.status, "COMPLETED_WITH_WARNING")
    chk.equal("warning: poll count", result.polls, 2)


def scenario_unknown_then_skipped(chk, mod, tmpdir):
    chk.section("Unknown status keeps polling; SKIPPED is terminal non-success")
    sc = scenario({BUNDLE_A: bundle_cfg(TASK_A, ["Vendor Queued", "Skipped"])})
    with Mock(tmpdir, "skipped", sc) as mock:
        client = client_for(mod, mock, poll_interval=0.01, poll_timeout=10)
        raised = None
        try:
            client.download_bundle(BUNDLE_A, download_now=True)
        except mod.TaskFailedError as exc:
            raised = exc
        log = mock.log()

    assert_clean_traffic(chk, "skipped", log)
    assert_polling(chk, "skipped", log, TASK_A, 2)
    chk.check("skipped: unknown first status did not end polling", raised is not None,
              "download_bundle returned or failed before observing SKIPPED")
    if raised is not None:
        chk.equal("skipped: normalized failure status", raised.status, "SKIPPED")
        chk.equal("skipped: task id carried", raised.task_id, TASK_A)


def scenario_explicit_false(chk, mod, tmpdir):
    chk.section("Explicit boolean false values remain present and boolean")
    sc = scenario({BUNDLE_A: bundle_cfg(TASK_A, ["Successful"])})
    with Mock(tmpdir, "false-values", sc) as mock:
        client = client_for(mod, mock, poll_interval=0.01, poll_timeout=10)
        result = client.download_bundle(BUNDLE_A, download_now=False, cancel_now=False)
        log = mock.log()

    assert_clean_traffic(chk, "false-values", log)
    assert_start_request(chk, "false-values", log, BUNDLE_A,
                         {"downloadNow": False, "cancelNow": False})
    assert_polling(chk, "false-values", log, TASK_A, 1)
    chk.equal("false-values: download completed", result.status, "SUCCESSFUL")


def scenario_call_overrides(chk, mod, tmpdir):
    chk.section("Per-call polling settings override constructor defaults")
    sc = scenario({BUNDLE_A: bundle_cfg(TASK_A, ["PENDING", "Successful"])})
    with Mock(tmpdir, "overrides", sc) as mock:
        client = client_for(mod, mock, poll_interval=0, poll_timeout=0)
        result = client.download_bundle(
            BUNDLE_A,
            download_now=True,
            poll_interval=0.01,
            poll_timeout=10,
        )
        log = mock.log()

    assert_clean_traffic(chk, "overrides", log)
    assert_polling(chk, "overrides", log, TASK_A, 2)
    chk.equal("overrides: call-specific timeout allowed terminal result",
              result.status, "SUCCESSFUL")


def scenario_token_reuse(chk, mod, tmpdir):
    chk.section("One token pair per client, reused across operations")
    sc = scenario({
        BUNDLE_A: bundle_cfg(TASK_A, ["PENDING", "Successful"]),
        BUNDLE_B: bundle_cfg(TASK_B, ["In Progress", "Successful"]),
    })
    with Mock(tmpdir, "reuse", sc) as mock:
        client = client_for(mod, mock, poll_interval=0.01, poll_timeout=10)
        first = client.download_bundle(BUNDLE_A, download_now=True)
        second = client.download_bundle(BUNDLE_B, download_now=True)
        log = mock.log()

    assert_clean_traffic(chk, "reuse", log)
    assert_token_request(chk, "reuse", log, expected_count=1)
    assert_start_request(chk, "reuse", log, BUNDLE_A, {"downloadNow": True})
    assert_start_request(chk, "reuse", log, BUNDLE_B, {"downloadNow": True})
    chk.equal("reuse: both downloads reached SUCCESSFUL",
              (first.status, second.status), ("SUCCESSFUL", "SUCCESSFUL"))
    chk.equal("reuse: each download polled its own task",
              sorted({e["path"] for e in log if e["operation"] == "op_get_task"}),
              sorted(["/v1/tasks/%s" % TASK_A, "/v1/tasks/%s" % TASK_B]))


def scenario_timeout(chk, mod, tmpdir):
    chk.section("Poll timeout: never assume completion")
    sc = scenario({BUNDLE_A: bundle_cfg(TASK_A, ["PENDING"])})
    with Mock(tmpdir, "timeout", sc) as mock:
        client = client_for(mod, mock, poll_interval=0, poll_timeout=0)
        raised = None
        try:
            client.download_bundle(BUNDLE_A, download_now=True)
        except mod.TaskTimeoutError as exc:
            raised = exc
        log = mock.log()

    assert_clean_traffic(chk, "timeout", log)
    polls = [e for e in log if e["operation"] == "op_get_task"]
    chk.equal("timeout: polled once before observing the exhausted deadline", len(polls), 1)
    chk.check("timeout: TaskTimeoutError raised", raised is not None,
              "download_bundle returned or raised something else")
    if raised is not None:
        chk.equal("timeout: task id reported", raised.task_id, TASK_A)
        chk.equal("timeout: last observed status reported",
                  (raised.last_status or "").replace(" ", "_").upper(), "PENDING")
        chk.equal("timeout: poll count reported", raised.polls, 1)


def scenario_no_spec(chk, mod, tmpdir):
    chk.section("Refuse to send an empty BundleDownloadSpec")
    sc = scenario({BUNDLE_A: bundle_cfg(TASK_A, ["Successful"])})
    with Mock(tmpdir, "nospec", sc) as mock:
        client = client_for(mod, mock, poll_interval=0.01, poll_timeout=10)
        raised = None
        try:
            client.download_bundle(BUNDLE_A)
        except ValueError as exc:
            raised = exc
        log = mock.log()

    chk.check("no-spec: ValueError raised locally", raised is not None,
              "expected ValueError when no download option is set")
    chk.equal("no-spec: no request left the client", log, [])


def scenario_empty_timestamp(chk, mod, tmpdir):
    chk.section("Refuse an empty optional string before any request")
    sc = scenario({BUNDLE_A: bundle_cfg(TASK_A, ["Successful"])})
    with Mock(tmpdir, "empty-timestamp", sc) as mock:
        client = client_for(mod, mock, poll_interval=0.01, poll_timeout=10)
        raised = None
        try:
            client.download_bundle(BUNDLE_A, scheduled_timestamp="")
        except ValueError as exc:
            raised = exc
        log = mock.log()

    chk.check("empty-timestamp: ValueError raised locally", raised is not None,
              "expected ValueError for an empty scheduled_timestamp")
    chk.equal("empty-timestamp: no request left the client", log, [])


def scenario_bad_credentials(chk, mod, tmpdir):
    chk.section("Rejected credentials surface as AuthenticationError")
    sc = scenario({BUNDLE_A: bundle_cfg(TASK_A, ["Successful"])})
    with Mock(tmpdir, "badcreds", sc) as mock:
        client = mod.BundleDownloadClient(
            mock.base_url, USERNAME, "wrong-password", poll_interval=0.01, poll_timeout=10
        )
        raised = None
        try:
            client.download_bundle(BUNDLE_A, download_now=True)
        except mod.AuthenticationError as exc:
            raised = exc
        log = mock.log()

    chk.check("bad-credentials: AuthenticationError raised", raised is not None,
              "download_bundle did not raise AuthenticationError")
    if raised is not None:
        chk.equal("bad-credentials: status code carried", raised.status_code, 401)
    chk.equal("bad-credentials: nothing attempted after the failed token call",
              [e["operation"] for e in log], ["op_create_token"])


# --------------------------------------------------------------------------
# derived artifacts
# --------------------------------------------------------------------------
def load_json(chk, path, label):
    if not os.path.exists(path):
        chk.fail("%s exists" % label, "%s is missing" % path)
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except ValueError as exc:
        chk.fail("%s is valid JSON" % label, str(exc))
        return None
    chk.ok("%s exists and parses" % label)
    return data


def path_pattern(template):
    parts = re.split(r"\{[^}]+\}", template)
    return re.compile("^" + "[^/]+".join(re.escape(p) for p in parts) + "$")


def schema_problems(value, schema_name, schemas, path="$"):
    """Return every way ``value`` disagrees with the contract's schema graph."""
    schema = schemas.get(schema_name)
    if not isinstance(schema, dict):
        return ["contract schema %r is not defined" % schema_name]
    problems = []
    props = schema.get("properties") or {}
    if not isinstance(props, dict):
        return ["%s.properties must be an object" % schema_name]
    for name in schema.get("required") or []:
        if name not in value:
            problems.append("%s.%s is required by %s but was absent"
                            % (path, name, schema_name))
    for key, item in value.items():
        decl = props.get(key)
        if not isinstance(decl, dict):
            problems.append("%s.%s is not declared as a property of %s in docs/contract.json"
                            % (path, key, schema_name))
            continue
        expected = decl.get("type")
        if expected in JSON_TYPES:
            if expected == "boolean":
                good = isinstance(item, bool)
            elif expected in ("integer", "number"):
                good = isinstance(item, (int, float)) and not isinstance(item, bool)
            else:
                good = isinstance(item, JSON_TYPES[expected])
            if not good:
                problems.append("%s.%s is declared %s but the wire value was %r"
                                % (path, key, expected, item))
                continue
        if isinstance(item, dict) and decl.get("schema"):
            problems.extend(schema_problems(item, decl["schema"], schemas,
                                            "%s.%s" % (path, key)))
        if isinstance(item, list):
            items_decl = decl.get("items") or {}
            if isinstance(items_decl, dict) and items_decl.get("schema"):
                for index, element in enumerate(item):
                    if isinstance(element, dict):
                        problems.extend(schema_problems(
                            element, items_decl["schema"], schemas,
                            "%s.%s[%d]" % (path, key, index)))
    return problems


def check_artifacts(chk, mod, sample_log, success_task, failed_task):
    chk.section("Derived artifacts in docs/")
    contract = load_json(chk, CONTRACT_PATH, "docs/contract.json")
    sources = load_json(chk, SOURCES_PATH, "docs/official_sources.json")
    if contract is None or sources is None:
        return

    # -- official_sources.json --------------------------------------------
    entries = sources.get("sources")
    if not chk.check("official_sources.json lists exactly one source",
                     isinstance(entries, list) and len(entries) == 1,
                     "got %r" % (entries,)):
        return
    src = entries[0]
    chk.equal("source repository", src.get("repository"), SPEC_REPO)
    chk.equal("source license", src.get("license"), "Apache-2.0")
    chk.equal("source tag", src.get("tag"), SPEC_TAG)
    chk.equal("source spec path", src.get("spec_path"), SPEC_PATH)
    sha = src.get("commit_sha") or ""
    chk.equal("source commit_sha is the commit tagged 9.0.0.0", sha, SPEC_COMMIT)
    chk.check("source commit_sha is not the 9.1.0.0 revision", sha != TAG_9_1_COMMIT,
              "this is the commit tagged 9.1.0.0; record the commit tagged 9.0.0.0")
    permalink = src.get("permalink") or ""
    chk.equal("permalink pins the exact 9.0.0.0 commit and spec path",
              permalink, SPEC_PERMALINK)

    op_ids = src.get("operation_ids")
    chk.equal("operation_ids names exactly the operations this integration calls",
              sorted(op_ids) if isinstance(op_ids, list) else op_ids,
              sorted(EXPECTED_OPERATIONS))

    # -- contract.json ------------------------------------------------------
    operations = contract.get("operations")
    schemas = contract.get("schemas")
    if not chk.check("contract declares operations and schemas objects",
                     isinstance(operations, dict) and isinstance(schemas, dict),
                     "operations=%r schemas=%r" % (type(operations), type(schemas))):
        return
    chk.equal("contract source block matches official_sources.json",
              contract.get("source"),
              {"tag": SPEC_TAG, "commit_sha": sha, "spec_path": SPEC_PATH})
    if isinstance(op_ids, list):
        chk.equal("operation_ids match the operations keyed in the contract",
                  sorted(op_ids), sorted(operations))
    chk.equal("contract names exactly the three pinned operationIds",
              sorted(operations), sorted(EXPECTED_OPERATIONS))

    for op_id, op in operations.items():
        if not isinstance(op, dict):
            chk.fail("operation %s is an object" % op_id, "got %r" % (op,))
            continue
        missing = [k for k in ("method", "path", "success_status") if k not in op]
        chk.check("operation %s declares method/path/success_status" % op_id, not missing,
                  "missing %s" % missing)
        for key in ("request_schema", "response_schema"):
            name = op.get(key)
            if name is not None:
                chk.check("operation %s %s %r is defined in schemas" % (op_id, key, name),
                          name in schemas, "%r is not in contract.schemas" % name)

    for op_id, expected in EXPECTED_OPERATIONS.items():
        op = operations.get(op_id)
        if not isinstance(op, dict):
            continue
        actual = {key: op.get(key) for key in expected}
        chk.equal("operation %s matches the pinned method/path/status/schema contract" % op_id,
                  actual, expected)

    def property_shape(decl):
        if not isinstance(decl, dict):
            return decl
        shape = {key: decl[key] for key in ("type", "schema") if key in decl}
        if "items" in decl:
            items = decl.get("items")
            if isinstance(items, dict):
                shape["items"] = {
                    key: items[key] for key in ("type", "schema") if key in items
                }
            else:
                shape["items"] = items
        return shape

    for schema_name, expected in EXPECTED_SCHEMAS.items():
        schema = schemas.get(schema_name)
        if not chk.check("pinned schema %s is present" % schema_name,
                         isinstance(schema, dict), "schema is missing or not an object"):
            continue
        actual_props = schema.get("properties")
        if isinstance(actual_props, dict):
            actual_props = {name: property_shape(decl)
                            for name, decl in actual_props.items()}
        actual_required = schema.get("required") or []
        comparable_required = (
            sorted(actual_required) if isinstance(actual_required, list) else actual_required
        )
        chk.equal("schema %s required names match 9.0.0.0" % schema_name,
                  comparable_required, sorted(expected["required"]))
        chk.equal("schema %s properties match 9.0.0.0" % schema_name,
                  actual_props, expected["properties"])

    # every schema referenced by a property must exist
    for schema_name, schema in schemas.items():
        for prop, decl in (schema.get("properties") or {}).items():
            for ref in (decl.get("schema"), (decl.get("items") or {}).get("schema")):
                if ref is not None:
                    chk.check("schema %s.%s references a defined schema %r"
                              % (schema_name, prop, ref),
                              ref in schemas, "%r is not in contract.schemas" % ref)

    # -- contract vs. observed traffic -------------------------------------
    chk.section("Contract matches the traffic the client actually produced")
    for entry in sample_log:
        observed = "%s %s" % (entry["method"], entry["path"])
        matches = [
            (op_id, op) for op_id, op in operations.items()
            if isinstance(op, dict) and op.get("method") == entry["method"]
            and path_pattern(str(op.get("path", ""))).match(entry["path"])
        ]
        if not chk.check("contract covers %s exactly once" % observed, len(matches) == 1,
                         "%d contract operation(s) matched" % len(matches)):
            continue
        op_id, op = matches[0]
        chk.equal("%s: %s success_status" % (observed, op_id),
                  op.get("success_status"), entry["response_status"])
        if entry["body"] is not None:
            schema_name = op.get("request_schema")
            if chk.check("%s: %s declares a request_schema" % (observed, op_id),
                         bool(schema_name), "request_schema missing"):
                problems = schema_problems(entry["body"], schema_name, schemas)
                chk.check("%s: request body conforms to contract schema %s"
                          % (observed, schema_name), not problems, "; ".join(problems))

    get_task_ops = [op for op in operations.values()
                    if isinstance(op, dict) and op.get("method") == "GET"
                    and path_pattern(str(op.get("path", ""))).match("/v1/tasks/x")]
    if chk.check("contract covers the task-polling operation", len(get_task_ops) == 1,
                 "%d matched" % len(get_task_ops)):
        schema_name = get_task_ops[0].get("response_schema")
        if chk.check("task-polling operation declares a response_schema", bool(schema_name),
                     "response_schema missing"):
            for label, task in (("successful", success_task), ("failed", failed_task)):
                if task:
                    problems = schema_problems(task, schema_name, schemas)
                    chk.check("%s task body conforms to contract schema %s"
                              % (label, schema_name), not problems, "; ".join(problems))

    # -- status classification ---------------------------------------------
    statuses = contract.get("task_status")
    if chk.check("contract classifies task statuses", isinstance(statuses, dict),
                 "task_status was %r" % (statuses,)):
        groups = {
            "non_terminal": mod.NON_TERMINAL_STATUSES,
            "terminal_success": mod.TERMINAL_SUCCESS_STATUSES,
            "terminal_failure": mod.TERMINAL_FAILURE_STATUSES,
        }
        seen = []
        for key, implemented in groups.items():
            declared = statuses.get(key)
            chk.check("task_status.%s is a list" % key, isinstance(declared, list),
                      "got %r" % (declared,))
            if isinstance(declared, list):
                seen.extend(declared)
                chk.equal("task_status.%s matches the implementation" % key,
                          sorted(declared), sorted(implemented))
        chk.equal("task status groups are disjoint", len(seen), len(set(seen)))


def check_no_third_party_imports(chk):
    chk.section("Packaging")
    offenders = []
    pkg_dir = os.path.join(REPO_ROOT, "sddcbundle")
    allowed = set(sys.stdlib_module_names)
    for name in sorted(os.listdir(pkg_dir)):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(pkg_dir, name), encoding="utf-8") as fh:
            source = fh.read()
        for match in re.finditer(r"^\s*(?:from|import)\s+([A-Za-z_][\w.]*)", source, re.M):
            root = match.group(1).split(".")[0]
            if root in ("sddcbundle", "") or match.group(0).lstrip().startswith("from ."):
                continue
            if root not in allowed:
                offenders.append("%s: %s" % (name, match.group(0).strip()))
    chk.check("sddcbundle imports only the standard library", not offenders,
              "third-party imports: %s" % offenders)


def main():
    chk = Checker()
    print("Verifying the SDDC Manager bundle-download integration")
    try:
        import sddcbundle as mod
    except Exception:
        chk.fail("import sddcbundle", traceback.format_exc())
        print("\n%d passed, %d failed" % (chk.passed, len(chk.failures)))
        return 1

    with tempfile.TemporaryDirectory() as tmpdir:
        sample_log = []
        success_task = None
        failed_task = None
        stages = [
            ("immediate", scenario_immediate),
            ("scheduled", scenario_scheduled),
            ("cancel", scenario_cancel),
            ("failure", scenario_failure),
            ("warning", scenario_warning),
            ("unknown-skipped", scenario_unknown_then_skipped),
            ("false-values", scenario_explicit_false),
            ("overrides", scenario_call_overrides),
            ("reuse", scenario_token_reuse),
            ("timeout", scenario_timeout),
            ("no-spec", scenario_no_spec),
            ("empty-timestamp", scenario_empty_timestamp),
            ("bad-credentials", scenario_bad_credentials),
        ]
        for name, fn in stages:
            try:
                outcome = fn(chk, mod, tmpdir)
            except Exception:
                chk.fail("scenario %s completed" % name, traceback.format_exc())
                continue
            if name == "immediate" and outcome is not None:
                success_task = outcome.task
            if name == "failure":
                failed_task = outcome

        # a clean, representative log for the contract cross-check
        sc = scenario({BUNDLE_A: bundle_cfg(TASK_A, ["PENDING", "Successful"])})
        try:
            with Mock(tmpdir, "contract-sample", sc) as mock:
                client = client_for(mod, mock, poll_interval=0.01, poll_timeout=10)
                client.download_bundle(BUNDLE_A, download_now=True)
                sample_log = mock.log()
        except Exception:
            chk.fail("collect a request log for the contract cross-check",
                     traceback.format_exc())

        try:
            check_artifacts(chk, mod, sample_log, success_task, failed_task)
        except Exception:
            chk.fail("artifact checks completed", traceback.format_exc())

    check_no_third_party_imports(chk)

    print("\n%d passed, %d failed" % (chk.passed, len(chk.failures)))
    if chk.failures:
        print("\nFailed checks:")
        for name, detail in chk.failures:
            print("  - %s: %s" % (name, detail.splitlines()[0] if detail else ""))
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
