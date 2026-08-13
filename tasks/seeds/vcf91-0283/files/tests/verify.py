#!/usr/bin/env python3
"""Deterministic verification for Save-VcfOnDiscoveredApplication.

Starts one contract-pinned loopback mock per scenario, drives the module once
through tests/exercise.ps1, then asserts the exact wire shape of every request
that reached the mock. No live VMware endpoint is contacted: every request in
this verification goes to 127.0.0.1.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
MODULE_DIR = os.path.join(REPO_ROOT, "src", "VcfOpsNetworks.Applications")
MODULE_MANIFEST = os.path.join(MODULE_DIR, "VcfOpsNetworks.Applications.psd1")
MODULE_SCRIPT = os.path.join(MODULE_DIR, "VcfOpsNetworks.Applications.psm1")
CONTRACT_PATH = os.path.join(REPO_ROOT, "docs", "contract.json")
SOURCES_PATH = os.path.join(REPO_ROOT, "docs", "official_sources.json")
MOCK = os.path.join(HERE, "mock_vcf_ops_networks.py")
EXERCISE = os.path.join(HERE, "exercise.ps1")

REQUIRED_MODULE = "VMware.Sdk.Vcf.Ops"

USERNAME = "admin@vcfon.local"
PASSWORD = "VMw@re1!secret"
TOKEN = "Mgs2YX0ZSY+gHW6RYypeeA=="
AUTH_HEADER = "NetworkInsight " + TOKEN
REQUEST_ID = "TASK_PROGRESS_application.APP_BULK_SAVE.1641371956491.0.007518507960020182"
PROGRESS_PATH = "/api/ni/groups/task/progress/" + REQUEST_ID
ENTITY_IDS = [
    "18203:565:2854896465419091802",
    "18203:565:3896568950496372144",
    "18203:565:7712445190380122731",
]
SAVED_APPLICATIONS = [
    {
        "entity_id": ENTITY_IDS[0],
        "name": "support-app-web",
        "response_code": "SUCCESS",
    },
    {
        "entity_id": ENTITY_IDS[1],
        "name": "support-app-db",
        "response_code": "SUCCESS",
    },
    {
        "entity_id": ENTITY_IDS[2],
        "name": "billing-app",
        "response_code": "ALREADY_SAVED_APPLICATION",
        "error_message": "Application billing-app is already saved.",
    },
]

POLL_INTERVAL = 0.1
JOB_TIMEOUT = 15.0
STALL_TIMEOUT = 1.0
MIN_POLL_GAP = POLL_INTERVAL * 0.8

FAILURES: list[str] = []


def fail(message: str) -> None:
    FAILURES.append(message)


def check(condition: bool, message: str) -> bool:
    if not condition:
        fail(message)
    return bool(condition)


def check_equal(actual, expected, label: str) -> bool:
    return check(
        actual == expected,
        "%s: expected %r, got %r" % (label, expected, actual),
    )


def summarize(sequence: list, limit: int = 10) -> str:
    """Render a request sequence without flooding the report."""
    if len(sequence) <= limit:
        return repr(sequence)
    return "%r ... and %d more (%d total)" % (
        sequence[:limit],
        len(sequence) - limit,
        len(sequence),
    )


def check_sequence(actual: list, expected: list, label: str) -> bool:
    return check(
        actual == expected,
        "%s: expected %s, got %s" % (label, summarize(expected), summarize(actual)),
    )


def die(message: str) -> None:
    print("VERIFY ERROR: " + message, file=sys.stderr)
    sys.exit(2)


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

def preflight() -> None:
    if shutil.which("pwsh") is None:
        die("pwsh is required but was not found on PATH.")
    for path in (MODULE_MANIFEST, MODULE_SCRIPT, CONTRACT_PATH, SOURCES_PATH, MOCK, EXERCISE):
        if not os.path.isfile(path):
            die("missing required file: " + os.path.relpath(path, REPO_ROOT))

    probe = subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "(Get-Module -ListAvailable -Name '%s' | Sort-Object Version -Descending | "
            "Select-Object -First 1).Version.ToString()" % REQUIRED_MODULE,
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    version = probe.stdout.strip().splitlines()[-1].strip() if probe.stdout.strip() else ""
    if not version:
        die(
            "the %s PowerShell module is an environment prerequisite and is not "
            "installed. It must not be vendored into this repository." % REQUIRED_MODULE
        )
    print("prerequisite %s %s" % (REQUIRED_MODULE, version))


def check_contract_integrity() -> None:
    with open(CONTRACT_PATH, encoding="utf-8") as handle:
        contract = json.load(handle)
    with open(SOURCES_PATH, encoding="utf-8") as handle:
        sources = json.load(handle)

    expected_operations = {
        "create": ("POST", "/auth/token"),
        "getDiscoveredApplications": ("GET", "/groups/discovered-applications"),
        "saveDiscoveredApplications": ("POST", "/groups/discovered-applications/save"),
        "getBulkApplicationTaskProgress": ("GET", "/groups/task/progress/{requestId}"),
    }
    operations = contract.get("operations", {})
    check_equal(sorted(operations), sorted(expected_operations), "contract operation ids")
    for operation_id, (method, path) in expected_operations.items():
        operation = operations.get(operation_id, {})
        check_equal(operation.get("method"), method, "contract %s method" % operation_id)
        check_equal(operation.get("path"), path, "contract %s path" % operation_id)
    check_equal(contract.get("server", {}).get("base_path"), "/api/ni", "contract base path")

    primary = sources.get("primary_source", {})
    check_equal(
        primary.get("path"),
        "specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml",
        "official_sources spec path",
    )
    check_equal(
        primary.get("commit_sha"),
        "c3f3b52c845dd967cabbc21680e893292077d5ba",
        "official_sources commit sha",
    )
    check_equal(
        sorted(primary.get("operation_ids", [])),
        sorted(expected_operations),
        "official_sources operation ids",
    )


BANNED_PATTERNS = [
    (r"\bInvoke-RestMethod\b", "Invoke-RestMethod"),
    (r"\bInvoke-WebRequest\b", "Invoke-WebRequest"),
    (r"\bSystem\.Net\.WebClient\b", "System.Net.WebClient"),
    (r"\bSystem\.Net\.HttpWebRequest\b", "System.Net.HttpWebRequest"),
    (r"\bSystem\.Net\.WebRequest\b", "System.Net.WebRequest"),
    (r"\bSystem\.Net\.Sockets\.TcpClient\b", "System.Net.Sockets.TcpClient"),
    (r"\bcurl\b", "curl"),
    (r"\bwget\b", "wget"),
]


def check_implementation_source() -> None:
    with open(MODULE_SCRIPT, encoding="utf-8") as handle:
        source = handle.read()

    for pattern, label in BANNED_PATTERNS:
        if re.search(pattern, source):
            fail(
                "%s uses %s. Every request must go through the PowerCLI OpenAPI "
                "binding layer via Invoke-NiRequest."
                % (os.path.relpath(MODULE_SCRIPT, REPO_ROOT), label)
            )
    check(
        source.count("Invoke-NiRequest") >= 8,
        "%s does not route all four operations through Invoke-NiRequest."
        % os.path.relpath(MODULE_SCRIPT, REPO_ROOT),
    )
    check(
        source.count("System.Net.Http.HttpClient") == 2,
        "%s adds or changes a direct HttpClient use outside the already-written "
        "New-NiApiConnection helper." % os.path.relpath(MODULE_SCRIPT, REPO_ROOT),
    )
    check(
        "NotImplementedException" not in source,
        "%s still throws NotImplementedException."
        % os.path.relpath(MODULE_SCRIPT, REPO_ROOT),
    )

    for root, dirs, _files in os.walk(os.path.join(REPO_ROOT, "src")):
        for name in dirs:
            if name.startswith("VMware."):
                fail(
                    "a VMware module appears to be vendored at %s; it is an "
                    "environment prerequisite and must not be committed."
                    % os.path.relpath(os.path.join(root, name), REPO_ROOT)
                )


# ---------------------------------------------------------------------------
# Mock lifecycle
# ---------------------------------------------------------------------------

class Mock:
    def __init__(self, name: str, scenario: str, workdir: str):
        self.name = name
        self.log = os.path.join(workdir, "requests-%s.jsonl" % name)
        open(self.log, "w", encoding="utf-8").close()
        self.process = subprocess.Popen(
            [sys.executable, "-B", MOCK, "--scenario", scenario, "--log", self.log],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        line = self.process.stdout.readline().strip()
        if not line.startswith("PORT "):
            stderr = self.process.stderr.read()
            die("mock %s did not start: %s %s" % (name, line, stderr))
        self.port = int(line.split()[1])
        self.server = "http://127.0.0.1:%d" % self.port

    def stop(self) -> None:
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()

    def records(self) -> list:
        with open(self.log, encoding="utf-8") as handle:
            entries = [json.loads(line) for line in handle if line.strip()]
        entries.sort(key=lambda entry: entry["seq"])
        return entries


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------

def body_json(record: dict, label: str):
    raw = record.get("body")
    if raw is None:
        fail("%s: no request body was sent" % label)
        return None
    try:
        return json.loads(raw)
    except ValueError:
        fail("%s: request body is not valid JSON: %r" % (label, raw))
        return None


def header(record: dict, name: str):
    for key, value in record.get("headers", {}).items():
        if key.lower() == name.lower():
            return value
    return None


def query_pairs(record: dict) -> list:
    return [tuple(pair) for pair in record.get("query_pairs", [])]


def assert_authorized(record: dict, label: str) -> None:
    check_equal(header(record, "Authorization"), AUTH_HEADER, "%s Authorization header" % label)


def assert_loopback(records: list, port: int, label: str) -> None:
    for record in records:
        host = header(record, "Host") or ""
        if host.split(":")[0] not in ("127.0.0.1", "localhost"):
            fail("%s: request went to non-loopback host %r" % (label, host))


def operation_sequence(records: list) -> list:
    return [record.get("operation_id") for record in records]


def assert_common_prefix(records: list, label: str, expected_page1_query, expected_page2_query):
    """Assert the create + two list requests that every scenario shares."""
    create = records[0]
    check_equal(create.get("operation_id"), "create", "%s request 1 operation" % label)
    check_equal(create.get("method"), "POST", "%s create method" % label)
    check_equal(create.get("path"), "/api/ni/auth/token", "%s create path" % label)
    check_equal(query_pairs(create), [], "%s create query string" % label)
    check(
        header(create, "Authorization") is None,
        "%s: the create operation declares `security: []` and must be sent without "
        "an Authorization header, but one was present." % label,
    )
    content_type = header(create, "Content-Type") or ""
    check(
        content_type.startswith("application/json"),
        "%s create Content-Type: expected application/json, got %r" % (label, content_type),
    )

    page1 = records[1]
    check_equal(
        page1.get("operation_id"), "getDiscoveredApplications", "%s request 2 operation" % label
    )
    check_equal(
        page1.get("path"), "/api/ni/groups/discovered-applications", "%s list page 1 path" % label
    )
    check_equal(
        sorted(query_pairs(page1)), sorted(expected_page1_query), "%s list page 1 query" % label
    )
    check(page1.get("body") in (None, ""), "%s: list page 1 sent a request body" % label)
    assert_authorized(page1, "%s list page 1" % label)

    page2 = records[2]
    check_equal(
        page2.get("operation_id"), "getDiscoveredApplications", "%s request 3 operation" % label
    )
    check_equal(
        page2.get("path"), "/api/ni/groups/discovered-applications", "%s list page 2 path" % label
    )
    check_equal(
        sorted(query_pairs(page2)), sorted(expected_page2_query), "%s list page 2 query" % label
    )
    assert_authorized(page2, "%s list page 2" % label)

    for index, record in enumerate(records[1:], start=2):
        for key, value in query_pairs(record):
            check(
                value != "",
                "%s request %d sent query parameter %r with an empty value; unset "
                "optional parameters must be omitted." % (label, index, key),
            )


def assert_save_request(
    record: dict,
    label: str,
    expected_keys: list,
    expect_enable_intent,
    expected_discovery_type: str = "FLOW_BASED_DISCOVERY",
    expected_entity_ids: list | None = None,
):
    check_equal(record.get("operation_id"), "saveDiscoveredApplications", "%s save operation" % label)
    check_equal(record.get("method"), "POST", "%s save method" % label)
    check_equal(
        record.get("path"),
        "/api/ni/groups/discovered-applications/save",
        "%s save path" % label,
    )
    check_equal(query_pairs(record), [], "%s save query string" % label)
    assert_authorized(record, "%s save" % label)

    body = body_json(record, "%s save" % label)
    if body is None:
        return
    check_equal(sorted(body), sorted(expected_keys), "%s save body keys" % label)
    check_equal(
        body.get("discovery_type"), expected_discovery_type, "%s save discovery_type" % label
    )

    apps = body.get("discovered_apps")
    if not check(isinstance(apps, list), "%s save discovered_apps is not an array" % label):
        return
    check_equal(
        apps,
        [
            {"source_entity_id": entity_id}
            for entity_id in (ENTITY_IDS if expected_entity_ids is None else expected_entity_ids)
        ],
        "%s save discovered_apps" % label,
    )

    if expect_enable_intent is None:
        check(
            "enable_intent" not in body,
            "%s: enable_intent was not bound by the caller and must be omitted from "
            "the request body, but the body contained %r."
            % (label, body.get("enable_intent")),
        )
    else:
        check(
            body.get("enable_intent") is expect_enable_intent,
            "%s save enable_intent: expected the JSON boolean %s, got %r"
            % (label, str(expect_enable_intent).lower(), body.get("enable_intent")),
        )


def assert_poll_request(record: dict, label: str) -> None:
    check_equal(record.get("operation_id"), "getBulkApplicationTaskProgress", "%s poll operation" % label)
    check_equal(record.get("method"), "GET", "%s poll method" % label)
    check_equal(record.get("path"), PROGRESS_PATH, "%s poll path" % label)
    check_equal(query_pairs(record), [], "%s poll query string" % label)
    check(record.get("body") in (None, ""), "%s: poll sent a request body" % label)
    assert_authorized(record, "%s poll" % label)
    encoded = record.get("encoded_path") or ""
    check(
        urllib.parse.unquote(encoded) == PROGRESS_PATH,
        "%s poll encoded path does not decode to the contract path: %r" % (label, encoded),
    )


def assert_poll_spacing(records: list, label: str) -> None:
    polls = [
        record
        for record in records
        if record.get("operation_id") == "getBulkApplicationTaskProgress"
    ]
    for previous, current in zip(polls, polls[1:]):
        gap = current["received_monotonic"] - previous["received_monotonic"]
        check(
            gap >= MIN_POLL_GAP,
            "%s: progress requests were only %.3f second(s) apart; "
            "PollIntervalSeconds was %.3f" % (label, gap, POLL_INTERVAL),
        )


# ---------------------------------------------------------------------------
# Scenario assertions
# ---------------------------------------------------------------------------

def verify_minimal(mock: Mock, job: dict) -> None:
    label = "minimal"
    records = mock.records()
    assert_loopback(records, mock.port, label)

    for record in records:
        check(
            record.get("operation_id") is not None,
            "%s: request %s %s does not match any operation named by the contract"
            % (label, record.get("method"), record.get("raw_path")),
        )

    expected = [
        "create",
        "getDiscoveredApplications",
        "getDiscoveredApplications",
        "saveDiscoveredApplications",
        "getBulkApplicationTaskProgress",
        "getBulkApplicationTaskProgress",
        "getBulkApplicationTaskProgress",
    ]
    if not check_sequence(operation_sequence(records), expected, "%s request sequence" % label):
        return

    assert_common_prefix(
        records,
        label,
        expected_page1_query=[("discovery_type", "FLOW_BASED_DISCOVERY")],
        expected_page2_query=[("discovery_type", "FLOW_BASED_DISCOVERY"), ("cursor", "MTA=")],
    )

    create_body = body_json(records[0], "%s create" % label)
    if create_body is not None:
        check_equal(sorted(create_body), ["password", "username"], "%s create body keys" % label)
        check_equal(create_body.get("username"), USERNAME, "%s create username" % label)
        check_equal(create_body.get("password"), PASSWORD, "%s create password" % label)
        check(
            "domain" not in create_body,
            "%s: no domain was requested, so the domain field must be omitted from "
            "the credential body." % label,
        )

    assert_save_request(
        records[3],
        label,
        expected_keys=["discovered_apps", "discovery_type"],
        expect_enable_intent=None,
    )
    for record in records[4:]:
        assert_poll_request(record, label)
    assert_poll_spacing(records, label)

    if not check(job.get("ok") is True, "%s: the command failed: %s" % (label, job.get("error"))):
        return
    result = job.get("result") or {}
    check_equal(result.get("RequestId"), REQUEST_ID, "%s result RequestId" % label)
    check_equal(result.get("Status"), "FINISHED", "%s result Status" % label)
    check_equal(float(result.get("Progress") or 0), 100.0, "%s result Progress" % label)
    check_equal(result.get("TaskName"), "APP_BULK_SAVE", "%s result TaskName" % label)
    check_equal(int(result.get("PollCount") or 0), 3, "%s result PollCount" % label)
    check_equal(list(result.get("DiscoveredEntityIds") or []), ENTITY_IDS, "%s result ids" % label)
    check_equal(
        list(result.get("SavedApplications") or []),
        SAVED_APPLICATIONS,
        "%s result SavedApplications",
    )


def verify_full(mock: Mock, job: dict) -> None:
    label = "full"
    records = mock.records()
    assert_loopback(records, mock.port, label)

    expected = [
        "create",
        "getDiscoveredApplications",
        "getDiscoveredApplications",
        "saveDiscoveredApplications",
        "getBulkApplicationTaskProgress",
        "getBulkApplicationTaskProgress",
        "getBulkApplicationTaskProgress",
    ]
    if not check_sequence(operation_sequence(records), expected, "%s request sequence" % label):
        return

    page1_query = [
        ("discovery_type", "FLOW_BASED_DISCOVERY"),
        ("granularity", "COARSE"),
    ]
    assert_common_prefix(
        records,
        label,
        expected_page1_query=page1_query,
        expected_page2_query=page1_query + [("cursor", "MTA=")],
    )

    create_body = body_json(records[0], "%s create" % label)
    if create_body is not None:
        check_equal(
            sorted(create_body), ["domain", "password", "username"], "%s create body keys" % label
        )
        domain = create_body.get("domain")
        if check(isinstance(domain, dict), "%s create domain is not an object" % label):
            check_equal(sorted(domain), ["domain_type"], "%s create domain keys" % label)
            check_equal(domain.get("domain_type"), "LOCAL", "%s create domain_type" % label)
            check(
                "value" not in domain,
                "%s: DomainValue was not bound, so domain.value must be omitted." % label,
            )

    assert_save_request(
        records[3],
        label,
        expected_keys=["discovered_apps", "discovery_type", "enable_intent"],
        expect_enable_intent=False,
    )
    for record in records[4:]:
        assert_poll_request(record, label)
    assert_poll_spacing(records, label)

    if not check(job.get("ok") is True, "%s: the command failed: %s" % (label, job.get("error"))):
        return
    result = job.get("result") or {}
    check_equal(result.get("Status"), "FINISHED", "%s result Status" % label)
    check_equal(int(result.get("PollCount") or 0), 3, "%s result PollCount" % label)


def verify_failed_task(mock: Mock, job: dict) -> None:
    label = "failed_task"
    records = mock.records()
    assert_loopback(records, mock.port, label)

    expected = [
        "create",
        "getDiscoveredApplications",
        "getDiscoveredApplications",
        "saveDiscoveredApplications",
        "getBulkApplicationTaskProgress",
        "getBulkApplicationTaskProgress",
    ]
    if not check_sequence(
        operation_sequence(records),
        expected,
        "%s request sequence (FAILED is terminal even though progress is 60, so polling "
        "must stop at the second poll)" % label,
    ):
        return
    assert_poll_spacing(records, label)

    check(
        job.get("ok") is False,
        "%s: a FAILED task must surface as a terminating error, but the command "
        "returned successfully." % label,
    )
    message = (job.get("error") or "")
    check(
        "FAILED" in message.upper(),
        "%s: the error should name the terminal status FAILED, got %r" % (label, message),
    )
    check(
        "NotImplemented" not in (job.get("error_type") or ""),
        "%s: the command is not implemented." % label,
    )


def verify_auth_rejected(mock: Mock, job: dict) -> None:
    label = "auth_rejected"
    records = mock.records()
    assert_loopback(records, mock.port, label)

    if not check_sequence(
        operation_sequence(records),
        ["create"],
        "%s request sequence (a rejected token request must stop the workflow)" % label,
    ):
        return
    check_equal(records[0].get("response_status"), 401, "%s create response status" % label)
    check(
        job.get("ok") is False,
        "%s: HTTP 401 from the create operation must raise. The PowerCLI OpenAPI "
        "client does not throw on a non-success status, so the status has to be "
        "inspected explicitly." % label,
    )
    message = (job.get("error") or "")
    check(
        "create" in message.lower() and "401" in message,
        "%s: the error should name operation create and HTTP 401, got %r" % (label, message),
    )


def verify_stalled(mock: Mock, job: dict) -> None:
    label = "stalled"
    records = mock.records()
    assert_loopback(records, mock.port, label)

    sequence = operation_sequence(records)
    polls = [entry for entry in sequence if entry == "getBulkApplicationTaskProgress"]
    check(
        len(polls) >= 2,
        "%s: expected the task to be polled repeatedly before giving up, got %d poll(s)"
        % (label, len(polls)),
    )
    assert_poll_spacing(records, label)
    check(
        job.get("ok") is False,
        "%s: a task that never reaches a terminal state must abort once TimeoutSeconds "
        "elapses, but the command returned successfully." % label,
    )
    message = (job.get("error") or "")
    check(
        "timeout" in message.lower() or "timed out" in message.lower(),
        "%s: the error should report the timeout, got %r" % (label, message),
    )


def verify_value_only(mock: Mock, job: dict) -> None:
    label = "value_only"
    records = mock.records()
    assert_loopback(records, mock.port, label)

    expected = [
        "create",
        "getDiscoveredApplications",
        "getDiscoveredApplications",
        "saveDiscoveredApplications",
        "getBulkApplicationTaskProgress",
    ]
    if not check_sequence(operation_sequence(records), expected, "%s request sequence" % label):
        return

    page1_query = [("discovery_type", "SERVICE_NOW"), ("size", "7")]
    assert_common_prefix(
        records,
        label,
        expected_page1_query=page1_query,
        expected_page2_query=page1_query + [("cursor", "MTA=")],
    )

    create_body = body_json(records[0], "%s create" % label)
    if create_body is not None:
        check_equal(
            sorted(create_body), ["domain", "password", "username"], "%s create body keys" % label
        )
        domain = create_body.get("domain")
        if check(isinstance(domain, dict), "%s create domain is not an object" % label):
            check_equal(sorted(domain), ["value"], "%s create domain keys" % label)
            check_equal(domain.get("value"), "vcfon.example", "%s create domain value" % label)

    assert_save_request(
        records[3],
        label,
        expected_keys=["discovered_apps", "discovery_type", "enable_intent"],
        expect_enable_intent=True,
        expected_discovery_type="SERVICE_NOW",
    )
    assert_poll_request(records[4], label)

    if not check(job.get("ok") is True, "%s: the command failed: %s" % (label, job.get("error"))):
        return
    result = job.get("result") or {}
    check_equal(result.get("Status"), "FINISHED", "%s result Status" % label)
    check_equal(float(result.get("Progress") or 0), 37.0, "%s result Progress" % label)
    check_equal(int(result.get("PollCount") or 0), 1, "%s result PollCount" % label)
    check_equal(list(result.get("SavedApplications") or []), [], "%s result SavedApplications" % label)


def verify_empty(mock: Mock, job: dict) -> None:
    label = "empty"
    records = mock.records()
    assert_loopback(records, mock.port, label)
    expected = [
        "create",
        "getDiscoveredApplications",
        "saveDiscoveredApplications",
        "getBulkApplicationTaskProgress",
    ]
    if not check_sequence(operation_sequence(records), expected, "%s request sequence" % label):
        return

    list_request = records[1]
    check_equal(
        query_pairs(list_request),
        [("discovery_type", "FLOW_BASED_DISCOVERY")],
        "%s list query" % label,
    )
    check(list_request.get("body") in (None, ""), "%s: list sent a request body" % label)
    assert_authorized(list_request, "%s list" % label)
    assert_save_request(
        records[2],
        label,
        expected_keys=["discovered_apps", "discovery_type"],
        expect_enable_intent=None,
        expected_entity_ids=[],
    )
    assert_poll_request(records[3], label)

    if not check(job.get("ok") is True, "%s: the command failed: %s" % (label, job.get("error"))):
        return
    result = job.get("result") or {}
    check_equal(list(result.get("DiscoveredEntityIds") or []), [], "%s result ids" % label)
    check_equal(list(result.get("SavedApplications") or []), [], "%s result SavedApplications" % label)
    check_equal(int(result.get("PollCount") or 0), 1, "%s result PollCount" % label)


def verify_progress_terminal(mock: Mock, job: dict) -> None:
    label = "progress_terminal"
    records = mock.records()
    assert_loopback(records, mock.port, label)
    expected = [
        "create",
        "getDiscoveredApplications",
        "getDiscoveredApplications",
        "saveDiscoveredApplications",
        "getBulkApplicationTaskProgress",
    ]
    if not check_sequence(operation_sequence(records), expected, "%s request sequence" % label):
        return
    check(job.get("ok") is False, "%s: progress 100 with RUNNING status must not succeed" % label)
    message = job.get("error") or ""
    check(
        "RUNNING" in message.upper(),
        "%s: the terminal error should name status RUNNING, got %r" % (label, message),
    )


def verify_timeout_ceiling(mock: Mock, job: dict) -> None:
    label = "timeout_ceiling"
    records = mock.records()
    assert_loopback(records, mock.port, label)
    expected = [
        "create",
        "getDiscoveredApplications",
        "getDiscoveredApplications",
        "saveDiscoveredApplications",
        "getBulkApplicationTaskProgress",
    ]
    if not check_sequence(
        operation_sequence(records),
        expected,
        "%s request sequence (no second poll may start after TimeoutSeconds)" % label,
    ):
        return
    check(job.get("ok") is False, "%s: a task finishing only after the timeout succeeded" % label)
    message = job.get("error") or ""
    check(
        "timeout" in message.lower() or "timed out" in message.lower(),
        "%s: the error should report the timeout, got %r" % (label, message),
    )


def verify_http_failure(
    mock: Mock, job: dict, label: str, expected_sequence: list, operation_id: str
) -> None:
    records = mock.records()
    assert_loopback(records, mock.port, label)
    if not check_sequence(operation_sequence(records), expected_sequence, "%s request sequence" % label):
        return
    check_equal(records[-1].get("response_status"), 500, "%s response status" % label)
    check(job.get("ok") is False, "%s: HTTP 500 did not terminate the workflow" % label)
    message = job.get("error") or ""
    check(
        operation_id.lower() in message.lower() and "500" in message,
        "%s: the error must name operation %s and HTTP 500, got %r"
        % (label, operation_id, message),
    )


def verify_list_error(mock: Mock, job: dict) -> None:
    verify_http_failure(
        mock,
        job,
        "list_error",
        ["create", "getDiscoveredApplications"],
        "getDiscoveredApplications",
    )


def verify_save_error(mock: Mock, job: dict) -> None:
    verify_http_failure(
        mock,
        job,
        "save_error",
        ["create", "getDiscoveredApplications", "getDiscoveredApplications", "saveDiscoveredApplications"],
        "saveDiscoveredApplications",
    )


def verify_poll_error(mock: Mock, job: dict) -> None:
    verify_http_failure(
        mock,
        job,
        "poll_error",
        [
            "create",
            "getDiscoveredApplications",
            "getDiscoveredApplications",
            "saveDiscoveredApplications",
            "getBulkApplicationTaskProgress",
        ],
        "getBulkApplicationTaskProgress",
    )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

SCENARIOS = [
    ("minimal", "success", verify_minimal),
    ("full", "success", verify_full),
    ("value_only", "finished_below_100", verify_value_only),
    ("empty", "empty", verify_empty),
    ("failed_task", "failure", verify_failed_task),
    ("progress_terminal", "progress_100_running", verify_progress_terminal),
    ("auth_rejected", "authfail", verify_auth_rejected),
    ("list_error", "listfail", verify_list_error),
    ("save_error", "savefail", verify_save_error),
    ("poll_error", "pollfail", verify_poll_error),
    ("stalled", "stalled", verify_stalled),
    ("timeout_ceiling", "late_finish", verify_timeout_ceiling),
]


def build_jobs(mocks: dict) -> list:
    base = {
        "Username": USERNAME,
        "Password": PASSWORD,
        "DiscoveryType": "FLOW_BASED_DISCOVERY",
        "PollIntervalSeconds": POLL_INTERVAL,
        "TimeoutSeconds": JOB_TIMEOUT,
    }
    jobs = []

    minimal = dict(base, Name="minimal", Server=mocks["minimal"].server)
    jobs.append(minimal)

    full = dict(
        base,
        Name="full",
        Server=mocks["full"].server,
        DomainType="LOCAL",
        Granularity="COARSE",
        EnableIntent=False,
    )
    jobs.append(full)

    jobs.append(
        dict(
            base,
            Name="value_only",
            Server=mocks["value_only"].server,
            DiscoveryType="SERVICE_NOW",
            DomainValue="vcfon.example",
            PageSize=7,
            EnableIntent=True,
        )
    )
    jobs.append(dict(base, Name="empty", Server=mocks["empty"].server))
    jobs.append(dict(base, Name="failed_task", Server=mocks["failed_task"].server))
    jobs.append(dict(base, Name="progress_terminal", Server=mocks["progress_terminal"].server))
    jobs.append(dict(base, Name="auth_rejected", Server=mocks["auth_rejected"].server))
    jobs.append(dict(base, Name="list_error", Server=mocks["list_error"].server))
    jobs.append(dict(base, Name="save_error", Server=mocks["save_error"].server))
    jobs.append(dict(base, Name="poll_error", Server=mocks["poll_error"].server))
    jobs.append(
        dict(base, Name="stalled", Server=mocks["stalled"].server, TimeoutSeconds=STALL_TIMEOUT)
    )
    jobs.append(
        dict(
            base,
            Name="timeout_ceiling",
            Server=mocks["timeout_ceiling"].server,
            PollIntervalSeconds=2.0,
            TimeoutSeconds=1.0,
        )
    )
    return jobs


def main() -> int:
    preflight()
    check_contract_integrity()
    check_implementation_source()

    workdir = tempfile.mkdtemp(prefix="vcfon-verify-")
    mocks = {}
    try:
        for name, scenario, _ in SCENARIOS:
            mocks[name] = Mock(name, scenario, workdir)

        job_file = os.path.join(workdir, "jobs.json")
        result_file = os.path.join(workdir, "results.json")
        with open(job_file, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "Module": MODULE_MANIFEST,
                    "Output": result_file,
                    "Jobs": build_jobs(mocks),
                },
                handle,
                indent=2,
            )

        started = time.monotonic()
        completed = subprocess.run(
            [
                "pwsh",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                EXERCISE,
                "-JobFile",
                job_file,
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
        elapsed = time.monotonic() - started
        print("exercise.ps1 exit=%d in %.1fs" % (completed.returncode, elapsed))

        if not os.path.isfile(result_file):
            die(
                "exercise.ps1 produced no results.\nstdout:\n%s\nstderr:\n%s"
                % (completed.stdout, completed.stderr)
            )

        with open(result_file, encoding="utf-8") as handle:
            document = json.load(handle)
        jobs = {entry["name"]: entry for entry in document.get("jobs", [])}

        for name, _scenario, verifier in SCENARIOS:
            job = jobs.get(name)
            if job is None:
                fail("%s: exercise.ps1 produced no result for this scenario" % name)
                continue
            verifier(mocks[name], job)
    finally:
        for mock in mocks.values():
            mock.stop()
        shutil.rmtree(workdir, ignore_errors=True)

    if FAILURES:
        print("\nFAILED (%d check(s)):" % len(FAILURES))
        for message in FAILURES:
            print("  - " + message)
        return 1

    print("\nPASSED: every request matched the contract-derived wire shape.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
