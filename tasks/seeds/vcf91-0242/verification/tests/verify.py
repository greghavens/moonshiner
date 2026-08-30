#!/usr/bin/env python3
"""Protected verifier for VsanDpProtect.

Starts a contract-pinned loopback mock of the vSAN Data Protection snapshot
appliance, runs the module twice with runtime-only identifiers, and asserts the
exact request wire shape recorded in the mock's flushed JSONL request log.

No live VMware endpoint is contacted.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from collections import OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MODULE_DIR = os.path.join(ROOT, "VsanDpProtect")
MANIFEST = os.path.join(MODULE_DIR, "VsanDpProtect.psd1")
SOLUTION = os.path.join(MODULE_DIR, "VsanDpProtect.psm1")
CONTRACT = os.path.join(ROOT, "docs", "contract.json")
MOCK = os.path.join(HERE, "mock_server.py")
INVOKE = os.path.join(HERE, "invoke_case.ps1")

SDK_MODULE = "VMware.Sdk.Vcf.SddcManager"
SDK_VERSION = "13.5.0.25380678"

FORBIDDEN_SNIPPETS = [
    "invoke-restmethod",
    "invoke-webrequest",
    "system.net.webclient",
    "net.webclient",
    "httpwebrequest",
    "[net.http.httpclient]",
    "[system.net.http.httpclient]",
    "start-process",
    "curl ",
    "wget ",
]

OP_SESSION = "Snapservice.Sessions_create"
OP_PG_CREATE = "Snapservice.Clusters.ProtectionGroups_create$Task"
OP_SNAP_CREATE = "Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task"
OP_TASK_GET = "Snapservice.Tasks_get"
OP_PG_LIST = "Snapservice.Clusters.ProtectionGroups_list"

RESULT_PROPERTIES = [
    "clusterId",
    "protectionGroupId",
    "protectionGroupName",
    "protectionGroupStatus",
    "createTaskId",
    "snapshotTaskId",
    "snapshotId",
    "snapshotName",
    "sessionCreateCount",
    "tokenRefreshCount",
]


class Failure(Exception):
    pass


def fail(message):
    raise Failure(message)


def check(condition, message):
    if not condition:
        fail(message)


def header_values(entry, name):
    lowered = name.lower()
    return [value for key, value in entry["headers"] if key.lower() == lowered]


def media_type(value):
    return value.split(";")[0].strip().lower()


def token_id(hexlen=8):
    return secrets.token_hex(hexlen)


def build_case(index, expire_after):
    suffix = token_id(5)
    case = OrderedDict()
    case["label"] = "case%d" % index
    case["expire_after"] = expire_after
    case["polls_before_success"] = 4
    case["username"] = "svc-%s@vsphere.local" % token_id(4)
    case["password"] = "Pw-%s" % token_id(9)
    case["clusterId"] = "domain-c%d" % secrets.randbelow(900000)
    case["pg_id"] = "pg-%s" % suffix
    case["snapshot_id"] = "snap-%s" % token_id(5)
    case["create_task_id"] = "task-create-%s" % token_id(6)
    case["snapshot_task_id"] = "task-snap-%s" % token_id(6)
    case["protectionGroupName"] = "pgname-%s" % token_id(4)
    case["snapshotName"] = "snapname-%s" % token_id(4)
    case["probeToken"] = "probe-%s" % token_id(10)
    case["clientMarker"] = "client-%s" % token_id(10)
    case["tokens"] = ["sess-%s" % token_id(10) for _ in range(4)]
    return case


def case_one(index=1):
    case = build_case(index, expire_after=3)
    case["VmNamePattern"] = ["web-%s-*" % token_id(3), "db-%s-*" % token_id(3)]
    case["VmId"] = None
    case["locked"] = False
    case["PolicyName"] = "policy-%s" % token_id(4)
    case["PolicyIntervalUnit"] = "HOUR"
    case["PolicyInterval"] = 4
    case["PolicyRetentionUnit"] = "DAY"
    case["PolicyRetentionDuration"] = 7
    case["SnapshotRetentionUnit"] = "HOUR"
    case["SnapshotRetentionDuration"] = 12
    case["pg_body"] = json.dumps(OrderedDict([
        ("name", case["protectionGroupName"]),
        ("target_entities", OrderedDict([("vm_name_patterns", case["VmNamePattern"])])),
        ("snapshot_policies", [OrderedDict([
            ("name", case["PolicyName"]),
            ("schedule", OrderedDict([("unit", "HOUR"), ("interval", 4)])),
            ("retention", OrderedDict([("unit", "DAY"), ("duration", 7)])),
        ])]),
    ]), separators=(",", ":"))
    case["snapshot_body"] = json.dumps(OrderedDict([
        ("name", case["snapshotName"]),
        ("retention", OrderedDict([("unit", "HOUR"), ("duration", 12)])),
    ]), separators=(",", ":"))
    return case


def case_two(index=2):
    # The first run expires while polling. This one expires when the snapshot
    # POST is first attempted, proving refresh/replay is operation-agnostic.
    case = build_case(index, expire_after=5)
    case["VmNamePattern"] = None
    case["VmId"] = ["vm-%d" % secrets.randbelow(90000), "vm-%d" % secrets.randbelow(90000)]
    case["locked"] = True
    case["pg_body"] = json.dumps(OrderedDict([
        ("name", case["protectionGroupName"]),
        ("target_entities", OrderedDict([("vms", case["VmId"])])),
        ("locked", True),
    ]), separators=(",", ":"))
    case["snapshot_body"] = json.dumps(OrderedDict([("name", case["snapshotName"])]),
                                       separators=(",", ":"))
    return case


def expected_sequence(case):
    """The exact ordered wire traffic the appliance must observe."""
    cluster = case["clusterId"]
    pg_id = case["pg_id"]
    create_task = case["create_task_id"]
    snap_task = case["snapshot_task_id"]
    tok1, tok2 = case["tokens"][0], case["tokens"][1]

    create_target = "/snapservice/clusters/%s/protection-groups?vmw-task=true" % cluster
    snap_target = ("/snapservice/clusters/%s/protection-groups/%s/snapshots?vmw-task=true"
                   % (cluster, pg_id))
    list_target = "/snapservice/clusters/%s/protection-groups?pgs=%s" % (cluster, pg_id)
    create_poll = "/snapservice/tasks/%s" % create_task
    snap_poll = "/snapservice/tasks/%s" % snap_task

    def login():
        return {"operation": OP_SESSION, "method": "POST", "target": "/snapservice/sessions",
                "status": 201, "auth": "basic", "body": ""}

    def call(operation, method, target, status, token, body=""):
        return {"operation": operation, "method": method, "target": target,
                "status": status, "auth": token, "body": body}

    steps = [login(),
             call(OP_PG_CREATE, "POST", create_target, 202, tok1, case["pg_body"])]

    if case["expire_after"] == 3:
        steps += [
            call(OP_TASK_GET, "GET", create_poll, 200, tok1),
            call(OP_TASK_GET, "GET", create_poll, 200, tok1),
            call(OP_TASK_GET, "GET", create_poll, 401, tok1),
            login(),
            call(OP_TASK_GET, "GET", create_poll, 200, tok2),
            call(OP_TASK_GET, "GET", create_poll, 200, tok2),
            call(OP_SNAP_CREATE, "POST", snap_target, 202, tok2, case["snapshot_body"]),
            call(OP_TASK_GET, "GET", snap_poll, 200, tok2),
            call(OP_TASK_GET, "GET", snap_poll, 200, tok2),
            call(OP_TASK_GET, "GET", snap_poll, 200, tok2),
            call(OP_TASK_GET, "GET", snap_poll, 200, tok2),
            call(OP_PG_LIST, "GET", list_target, 200, tok2),
        ]
    else:
        steps += [
            call(OP_TASK_GET, "GET", create_poll, 200, tok1),
            call(OP_TASK_GET, "GET", create_poll, 200, tok1),
            call(OP_TASK_GET, "GET", create_poll, 200, tok1),
            call(OP_TASK_GET, "GET", create_poll, 200, tok1),
            call(OP_SNAP_CREATE, "POST", snap_target, 401, tok1, case["snapshot_body"]),
            login(),
            call(OP_SNAP_CREATE, "POST", snap_target, 202, tok2, case["snapshot_body"]),
            call(OP_TASK_GET, "GET", snap_poll, 200, tok2),
            call(OP_TASK_GET, "GET", snap_poll, 200, tok2),
            call(OP_TASK_GET, "GET", snap_poll, 200, tok2),
            call(OP_TASK_GET, "GET", snap_poll, 200, tok2),
            call(OP_PG_LIST, "GET", list_target, 200, tok2),
        ]

    steps.append(call(OP_TASK_GET, "GET", create_poll, 401, case["probeToken"]))
    return steps


def start_mock(workdir, case):
    log_path = os.path.join(workdir, "requests.jsonl")
    port_path = os.path.join(workdir, "port")
    config_path = os.path.join(workdir, "mock-config.json")
    config = OrderedDict([
        ("contract_path", CONTRACT),
        ("log_path", log_path),
        ("port_path", port_path),
        ("username", case["username"]),
        ("password", case["password"]),
        ("cluster", case["clusterId"]),
        ("pg_id", case["pg_id"]),
        ("snapshot_id", case["snapshot_id"]),
        ("create_task_id", case["create_task_id"]),
        ("snapshot_task_id", case["snapshot_task_id"]),
        ("pg_name", case["protectionGroupName"]),
        ("snapshot_name", case["snapshotName"]),
        ("tokens", case["tokens"]),
        ("expire_after", case["expire_after"]),
        ("polls_before_success", case["polls_before_success"]),
    ])
    with open(config_path, "w", encoding="utf-8") as handle:
        json.dump(config, handle)

    process = subprocess.Popen(
        [sys.executable, "-B", MOCK, config_path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    deadline = time.time() + 20.0
    while time.time() < deadline:
        if os.path.exists(port_path):
            with open(port_path, "r", encoding="utf-8") as handle:
                text = handle.read().strip()
            if text:
                return process, int(text), log_path
        if process.poll() is not None:
            stderr = process.communicate()[1].decode("utf-8", "replace")
            fail("The protected mock appliance exited before binding a port.\n" + stderr)
        time.sleep(0.05)
    process.kill()
    fail("The protected mock appliance did not bind a loopback port in time.")


def run_case(case):
    workdir = tempfile.mkdtemp(prefix="vsandp-%s-" % case["label"])
    process = None
    try:
        process, port, log_path = start_mock(workdir, case)
        base_path = "http://127.0.0.1:%d" % port

        case_payload = OrderedDict([
            ("basePath", base_path),
            ("username", case["username"]),
            ("password", case["password"]),
            ("clusterId", case["clusterId"]),
            ("protectionGroupName", case["protectionGroupName"]),
            ("snapshotName", case["snapshotName"]),
            ("probeToken", case["probeToken"]),
            ("clientMarker", case["clientMarker"]),
            ("locked", case["locked"]),
            ("VmNamePattern", case["VmNamePattern"]),
            ("VmId", case["VmId"]),
        ])
        for name in ("PolicyName", "PolicyIntervalUnit", "PolicyInterval",
                     "PolicyRetentionUnit", "PolicyRetentionDuration",
                     "SnapshotRetentionUnit", "SnapshotRetentionDuration"):
            case_payload[name] = case.get(name)

        case_path = os.path.join(workdir, "case.json")
        with open(case_path, "w", encoding="utf-8") as handle:
            json.dump(case_payload, handle)
        output_path = os.path.join(workdir, "result.json")

        completed = subprocess.run(
            ["pwsh", "-NoProfile", "-NonInteractive", "-File", INVOKE,
             "-ModuleManifest", MANIFEST,
             "-CasePath", case_path,
             "-OutputPath", output_path],
            capture_output=True, timeout=240,
        )
        stdout = completed.stdout.decode("utf-8", "replace")
        stderr = completed.stderr.decode("utf-8", "replace")
        if completed.returncode != 0 or not os.path.exists(output_path):
            fail("%s: the module did not complete.\n--- stdout ---\n%s\n--- stderr ---\n%s"
                 % (case["label"], stdout[-6000:], stderr[-6000:]))

        with open(output_path, "r", encoding="utf-8") as handle:
            result = json.load(handle, object_pairs_hook=OrderedDict)
        entries = []
        with open(log_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    entries.append(json.loads(line, object_pairs_hook=OrderedDict))
        return result, entries
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        shutil.rmtree(workdir, ignore_errors=True)


def assert_case(case, result, entries):
    label = case["label"]
    expected = expected_sequence(case)

    check(len(entries) == len(expected),
          "%s: expected exactly %d appliance requests, saw %d.\nobserved:\n%s"
          % (label, len(expected), len(entries),
             "\n".join("  %s %s -> %s" % (e["method"], e["target"], e["status"])
                       for e in entries)))

    basic = "Basic " + base64.b64encode(
        ("%s:%s" % (case["username"], case["password"])).encode("utf-8")).decode("ascii")
    stale_token = case["tokens"][0]
    refreshed_at = None

    for index, (want, got) in enumerate(zip(expected, entries), start=1):
        where = "%s request %d (%s)" % (label, index, want["operation"])
        check(got["seq"] == index, "%s: the request log is out of order." % where)
        check(got["method"] == want["method"],
              "%s: expected method %s, saw %s." % (where, want["method"], got["method"]))
        check(got["target"] == want["target"],
              "%s: expected raw target\n  %s\nsaw\n  %s" % (where, want["target"], got["target"]))
        check(got["operation_id"] == want["operation"],
              "%s: the appliance resolved this to %r." % (where, got["operation_id"]))
        check(got["status"] == want["status"],
              "%s: expected HTTP %s, saw %s." % (where, want["status"], got["status"]))

        accept = header_values(got, "Accept")
        check(len(accept) == 1 and media_type(accept[0]) == "application/json",
              "%s: exactly one Accept: application/json header is required, saw %r."
              % (where, accept))

        client_marker = header_values(got, "X-Moonshiner-Client-Marker")
        check(client_marker == [case["clientMarker"]],
              "%s: the request did not travel over the caller supplied ApiClient."
              % where)

        authorization = header_values(got, "Authorization")
        session = header_values(got, "vmware-api-session-id")
        if want["auth"] == "basic":
            check(authorization == [basic],
                  "%s: expected exactly one Authorization header carrying the supplied "
                  "credentials, saw %r." % (where, authorization))
            check(session == [],
                  "%s: a login request must not carry vmware-api-session-id, saw %r."
                  % (where, session))
        else:
            check(authorization == [],
                  "%s: credentials must not be replayed on a session authenticated "
                  "request, saw %r." % (where, authorization))
            check(len(session) == 1,
                  "%s: exactly one vmware-api-session-id header is required, saw %r."
                  % (where, session))
            check(session[0] == want["auth"],
                  "%s: expected the request to carry the %s session token."
                  % (where, "refreshed" if want["auth"] != stale_token else "current"))

        content_type = header_values(got, "Content-Type")
        if want["body"]:
            check(len(content_type) == 1 and media_type(content_type[0]) == "application/json",
                  "%s: exactly one Content-Type: application/json header is required, saw %r."
                  % (where, content_type))
            check(got["body"] == want["body"],
                  "%s: the request body must be exactly\n  %s\nbut the appliance received\n  %s"
                  % (where, want["body"], got["body"]))
        else:
            check(got["body"] == "",
                  "%s: this operation takes no request body, but %d bytes arrived."
                  % (where, len(got["body"])))

        if want["status"] == 401 and want["auth"] == stale_token:
            refreshed_at = index

    check(refreshed_at is not None,
          "%s: the run never exercised the expiring session token." % label)
    for entry in entries[refreshed_at:]:
        check(stale_token not in header_values(entry, "vmware-api-session-id"),
              "%s: the expired session token was reused after the refresh." % label)

    creates = [e for e in entries
               if e["operation_id"] == OP_PG_CREATE and e["status"] == 202]
    snapshots = [e for e in entries
                 if e["operation_id"] == OP_SNAP_CREATE and e["status"] == 202]
    logins = [e for e in entries if e["operation_id"] == OP_SESSION]
    check(len(creates) == 1,
          "%s: the protection group must be accepted exactly once; the token refresh must not "
          "repeat completed work, but %d accepted create requests arrived." % (label, len(creates)))
    check(len(snapshots) == 1,
          "%s: the snapshot must be accepted exactly once, but %d accepted requests arrived."
          % (label, len(snapshots)))
    check(len(logins) == 2,
          "%s: expected exactly two Snapservice.Sessions_create calls (initial login and one "
          "refresh), saw %d." % (label, len(logins)))

    # Result object.
    check(result["apiClientType"] == "VMware.Binding.OpenApi.Client.ApiClient",
          "%s: every request must travel over the caller supplied SDK ApiClient." % label)
    check(result["probeStatus"] == "Unauthorized",
          "%s: the caller owned ApiClient was not usable after the call (probe reported %r)."
          % (label, result["probeStatus"]))
    check(result["propertyOrder"] == ",".join(RESULT_PROPERTIES),
          "%s: expected the returned object to expose exactly\n  %s\nbut it exposed\n  %s"
          % (label, ",".join(RESULT_PROPERTIES), result["propertyOrder"]))

    wanted = OrderedDict([
        ("clusterId", case["clusterId"]),
        ("protectionGroupId", case["pg_id"]),
        ("protectionGroupName", case["protectionGroupName"]),
        ("protectionGroupStatus", "ACTIVE"),
        ("createTaskId", case["create_task_id"]),
        ("snapshotTaskId", case["snapshot_task_id"]),
        ("snapshotId", case["snapshot_id"]),
        ("snapshotName", case["snapshotName"]),
        ("sessionCreateCount", 2),
        ("tokenRefreshCount", 1),
    ])
    for name, value in wanted.items():
        check(result["result"][name] == value,
              "%s: result.%s should be %r but was %r."
              % (label, name, value, result["result"][name]))


def preflight():
    check(os.path.isfile(SOLUTION),
          "VsanDpProtect/VsanDpProtect.psm1 does not exist.")
    check(os.path.isfile(MANIFEST), "VsanDpProtect/VsanDpProtect.psd1 is missing.")
    check(os.path.isfile(CONTRACT), "docs/contract.json is missing.")
    check(shutil.which("pwsh") is not None, "pwsh (PowerShell 7.4 or later) is not on PATH.")

    with open(SOLUTION, "r", encoding="utf-8", errors="replace") as handle:
        source = handle.read()
    lowered = source.lower()
    for snippet in FORBIDDEN_SNIPPETS:
        check(snippet not in lowered,
              "VsanDpProtect.psm1 must reach the appliance only through the supplied "
              "VMware SDK ApiClient; %r is not allowed." % snippet.strip())

    probe = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-Command",
         "$m = Get-Module -ListAvailable -Name '%s' | "
         "Where-Object { $_.Version -eq '%s' }; "
         "if (-not $m) { exit 3 }" % (SDK_MODULE, SDK_VERSION)],
        capture_output=True, timeout=180,
    )
    check(probe.returncode == 0,
          "The prerequisite PowerShell module %s %s is not installed in this environment."
          % (SDK_MODULE, SDK_VERSION))


def main():
    try:
        preflight()
        for factory in (case_one, case_two):
            case = factory()
            result, entries = run_case(case)
            assert_case(case, result, entries)
    except Failure as failure:
        print("FAIL: %s" % failure, file=sys.stderr)
        return 1
    print("PASS: both runs matched the contract-pinned wire shape and refreshed the "
          "expired session token without repeating completed work.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
