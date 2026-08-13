#!/usr/bin/env python3
"""Protected verifier for the VCF Operations for Networks data source seed.

Starts one contract-pinned loopback mock per case, drives the candidate
PowerShell module through .protected/invoke_cases.ps1, then asserts the exact
wire shape recorded in each mock's request log.

Tokens, node identifiers and data source identifiers are generated freshly on
every run, so no expected value can be hard coded into the module.

No live VMware endpoint is contacted.
"""

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROTECTED = ROOT / ".protected"
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MODULE_DIR = ROOT / "src" / "VcfOpsNetworks.DataSource"
MODULE_PSM1 = MODULE_DIR / "VcfOpsNetworks.DataSource.psm1"
MODULE_PSD1 = MODULE_DIR / "VcfOpsNetworks.DataSource.psd1"

COMMAND_NAME = "Add-VcfNetworksVcenterDataSource"

EXPECTED_COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
EXPECTED_SPEC = "specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml"
EXPECTED_OPERATION_IDS = [
    "create",
    "listExpandedNodes",
    "validateVCenter",
    "addVcenterDatasource",
    "delete",
]

API_USER = "svc-vrni@vsphere.local"
API_PASSWORD = "P1atf0rm!Secret"
VC_USER = "readonly@vsphere.local"
VC_PASSWORD = "vC3nter!Secret"

FAILURES = []
CHECKS = [0]


def check(condition, message):
    CHECKS[0] += 1
    if not condition:
        FAILURES.append(message)
    return bool(condition)


def rand(prefix):
    return "%s-%s" % (prefix, secrets.token_hex(8))


# ---------------------------------------------------------------------------
# static checks
# ---------------------------------------------------------------------------
def static_checks():
    check(CONTRACT_PATH.is_file(), "docs/contract.json is missing")
    check(SOURCES_PATH.is_file(), "docs/official_sources.json is missing")
    check(MODULE_PSM1.is_file(), "src/VcfOpsNetworks.DataSource/VcfOpsNetworks.DataSource.psm1 is missing")
    check(MODULE_PSD1.is_file(), "src/VcfOpsNetworks.DataSource/VcfOpsNetworks.DataSource.psd1 is missing")
    if FAILURES:
        return None

    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))

    check(
        contract["source"]["repositoryCommitSha"] == EXPECTED_COMMIT,
        "docs/contract.json is not pinned to commit %s" % EXPECTED_COMMIT,
    )
    check(
        contract["source"]["specPath"] == EXPECTED_SPEC,
        "docs/contract.json does not name the expected specification path",
    )
    check(
        [op["operationId"] for op in contract["operations"]] == EXPECTED_OPERATION_IDS,
        "docs/contract.json does not name exactly the expected operationIds in order",
    )
    check(
        sources["repositoryCommitSha"] == EXPECTED_COMMIT
        and sources["specPath"] == EXPECTED_SPEC
        and sources["operationIds"] == EXPECTED_OPERATION_IDS,
        "docs/official_sources.json does not record the pinned spec path, commit sha and operationIds",
    )

    manifest = MODULE_PSD1.read_text(encoding="utf-8")
    check(
        "VMware.Sdk.Vcf.Ops" in manifest and "RequiredModules" in manifest,
        "the module manifest no longer declares the environment-provided VMware.Sdk.Vcf PowerCLI dependency",
    )

    vendored = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(".git/"):
            continue
        name = path.name.lower()
        if name.endswith((".dll", ".nupkg")) or name.startswith(("vmware.sdk.", "vmware.openapi", "vmware.bindings")):
            vendored.append(rel)
    check(not vendored, "the seed must not vendor PowerCLI SDK payload: %s" % vendored)

    source = MODULE_PSM1.read_text(encoding="utf-8")
    check(
        COMMAND_NAME in source,
        "the module no longer defines %s" % COMMAND_NAME,
    )
    shadowed = re.search(
        r"function\s+(?:[A-Za-z]+-Vcf(?:Ops|Installer|SddcManager|CloudBuilder)[A-Z]\w*"
        r"|Invoke-WebRequest|Invoke-RestMethod)\b",
        source,
    )
    check(
        shadowed is None,
        "the module must not redefine or shadow VMware PowerCLI or PowerShell web commands (found %r)"
        % (shadowed.group(0) if shadowed else None),
    )
    return contract


# ---------------------------------------------------------------------------
# cases
# ---------------------------------------------------------------------------
def build_cases():
    platform_id = rand("18230:901:platform")
    proxy_ok_id = rand("18230:901:proxy-ok")
    proxy_bad_id = rand("18230:901:proxy-bad")
    proxy_duplicate_id = rand("18230:901:proxy-duplicate")
    decoy_id = rand("18230:901:decoy")

    def nodes():
        return [
            {
                "id": platform_id,
                "entity_type": "Node",
                "node_type": "PLATFORM_VM",
                "name": "Platform-1",
                "ip_address": "10.10.0.10",
                "version": "9.1.0.0",
                "health": {"health_status": "HEALTHY", "health_details": []},
            },
            {
                "id": decoy_id,
                "entity_type": "Node",
                "node_type": "PLATFORM_VM",
                "name": "Collector-A",
                "ip_address": "10.10.0.12",
                "version": "9.1.0.0",
                "health": {"health_status": "HEALTHY", "health_details": []},
            },
            {
                "id": proxy_ok_id,
                "entity_type": "Node",
                "node_type": "PROXY_VM",
                # Exercise the prompt's case-insensitive, trimmed collector
                # name matching rule in every path that resolves Collector-A.
                "name": "  cOlLeCtOr-a  ",
                "ip_address": "10.10.0.11",
                "version": "9.1.0.0",
                "health": {"health_status": "HEALTHY", "health_details": []},
            },
            {
                "id": proxy_bad_id,
                "entity_type": "Node",
                "node_type": "PROXY_VM",
                "name": "Collector-B",
                "ip_address": "10.10.0.13",
                "version": "9.1.0.0",
                "health": {
                    "health_status": "UNHEALTHY",
                    "health_details": [{"message": "Collector unreachable", "code": "1001"}],
                },
            },
        ]

    def nodes_with_duplicate_collector():
        result = nodes()
        result.append(
            {
                "id": proxy_duplicate_id,
                "entity_type": "Node",
                "node_type": "PROXY_VM",
                "name": "COLLECTOR-A",
                "ip_address": "10.10.0.14",
                "version": "9.1.0.0",
                "health": {"health_status": "HEALTHY", "health_details": []},
            }
        )
        return result

    validate_ok = {"status": 200, "body": {"code": 200, "message": "Validation successful."}}
    validate_logical_failure = {
        "status": 200,
        "body": {"code": 401, "message": "Invalid credentials for the vCenter data source."},
    }
    validate_http_failure = {
        "status": 400,
        "body": {"code": 400, "message": "proxy_id is not reachable", "details": []},
    }
    validate_missing_code = {
        "status": 200,
        "body": {"message": "Validation response did not include a result code."},
    }

    base_params = {
        "Server": None,  # filled in once the mock port is known
        "Credential": {"username": API_USER, "password": API_PASSWORD},
        "CollectorName": "Collector-A",
        "VcenterCredential": {"username": VC_USER, "password": VC_PASSWORD},
        "Nickname": "vc-dc1",
    }

    def params(**overrides):
        merged = dict(base_params)
        merged.update(overrides)
        return merged

    cases = [
        {
            "name": "args-both-hosts",
            "params": params(VcenterFqdn="vc01.corp.example.com", VcenterIp="10.20.30.40"),
            "scenario": {"nodes": nodes(), "validate": validate_ok},
            "expect": {
                "exception": "System.ArgumentException",
                "operations": [],
            },
        },
        {
            "name": "args-no-host",
            "params": params(),
            "scenario": {"nodes": nodes(), "validate": validate_ok},
            "expect": {
                "exception": "System.ArgumentException",
                "operations": [],
            },
        },
        {
            "name": "collector-unhealthy",
            "params": params(VcenterFqdn="vc01.corp.example.com", CollectorName="Collector-B"),
            "scenario": {"nodes": nodes(), "validate": validate_ok},
            "expect": {
                "exception": "System.InvalidOperationException",
                "operations": ["create", "listExpandedNodes", "delete"],
            },
        },
        {
            "name": "collector-missing",
            "params": params(VcenterFqdn="vc01.corp.example.com", CollectorName="Collector-Z"),
            "scenario": {"nodes": nodes(), "validate": validate_ok},
            "expect": {
                "exception": "System.InvalidOperationException",
                "operations": ["create", "listExpandedNodes", "delete"],
            },
        },
        {
            "name": "collector-duplicate",
            "params": params(VcenterFqdn="vc01.corp.example.com"),
            "scenario": {"nodes": nodes_with_duplicate_collector(), "validate": validate_ok},
            "expect": {
                "exception": "System.InvalidOperationException",
                "operations": ["create", "listExpandedNodes", "delete"],
            },
        },
        {
            "name": "precheck-code-not-200",
            "params": params(VcenterFqdn="vc01.corp.example.com"),
            "scenario": {"nodes": nodes(), "validate": validate_logical_failure},
            "expect": {
                "exception": "System.InvalidOperationException",
                "operations": ["create", "listExpandedNodes", "validateVCenter", "delete"],
                "validateBody": {
                    "fqdn": "vc01.corp.example.com",
                    "proxy_id": proxy_ok_id,
                    "credentials": {"username": VC_USER, "password": VC_PASSWORD},
                },
            },
        },
        {
            "name": "precheck-http-400",
            "params": params(VcenterFqdn="vc01.corp.example.com"),
            "scenario": {"nodes": nodes(), "validate": validate_http_failure},
            "expect": {
                "exception": "System.InvalidOperationException",
                "operations": ["create", "listExpandedNodes", "validateVCenter", "delete"],
            },
        },
        {
            "name": "precheck-code-absent",
            "params": params(VcenterFqdn="vc01.corp.example.com"),
            "scenario": {"nodes": nodes(), "validate": validate_missing_code},
            "expect": {
                "exception": "System.InvalidOperationException",
                "operations": ["create", "listExpandedNodes", "validateVCenter", "delete"],
            },
        },
        {
            "name": "success-minimal",
            "params": params(VcenterFqdn="vc01.corp.example.com"),
            "scenario": {"nodes": nodes(), "validate": validate_ok},
            "expect": {
                "exception": None,
                "operations": [
                    "create",
                    "listExpandedNodes",
                    "validateVCenter",
                    "addVcenterDatasource",
                    "delete",
                ],
                "authBody": {"username": API_USER, "password": API_PASSWORD},
                "validateBody": {
                    "fqdn": "vc01.corp.example.com",
                    "proxy_id": proxy_ok_id,
                    "credentials": {"username": VC_USER, "password": VC_PASSWORD},
                },
                "addBody": {
                    "fqdn": "vc01.corp.example.com",
                    "proxy_id": proxy_ok_id,
                    "nickname": "vc-dc1",
                    "credentials": {"username": VC_USER, "password": VC_PASSWORD},
                },
            },
        },
        {
            "name": "success-full",
            "params": params(
                VcenterIp="10.20.30.40",
                AuthDomain="corp.example.com",
                Notes="Located in DC1",
                Nickname="vc-dc1-standby",
                Disabled=True,
            ),
            "scenario": {"nodes": nodes(), "validate": validate_ok},
            "expect": {
                "exception": None,
                "operations": [
                    "create",
                    "listExpandedNodes",
                    "validateVCenter",
                    "addVcenterDatasource",
                    "delete",
                ],
                "authBody": {
                    "username": API_USER,
                    "password": API_PASSWORD,
                    "domain": {"domain_type": "LDAP", "value": "corp.example.com"},
                },
                "validateBody": {
                    "ip": "10.20.30.40",
                    "proxy_id": proxy_ok_id,
                    "credentials": {"username": VC_USER, "password": VC_PASSWORD},
                },
                "addBody": {
                    "ip": "10.20.30.40",
                    "proxy_id": proxy_ok_id,
                    "nickname": "vc-dc1-standby",
                    "enabled": False,
                    "notes": "Located in DC1",
                    "credentials": {"username": VC_USER, "password": VC_PASSWORD},
                },
            },
        },
    ]

    for case in cases:
        token = rand("tok")
        entity_id = rand("18230:902:ds")
        case["token"] = token
        case["entityId"] = entity_id
        scenario = case["scenario"]
        scenario["token"] = token
        scenario["expiry"] = 1774000000000
        scenario.setdefault(
            "add",
            {
                "status": 201,
                "body": {
                    "entity_id": entity_id,
                    "entity_type": "VCenterDataSource",
                    "proxy_id": proxy_ok_id,
                    "nickname": case["params"]["Nickname"],
                    "enabled": True,
                },
            },
        )
    return cases


# ---------------------------------------------------------------------------
# mock lifecycle
# ---------------------------------------------------------------------------
def start_mock(workdir, case):
    scenario_path = workdir / ("scenario-%s.json" % case["name"])
    log_path = workdir / ("requests-%s.jsonl" % case["name"])
    ready_path = workdir / ("ready-%s.json" % case["name"])
    scenario_path.write_text(json.dumps(case["scenario"]), encoding="utf-8")

    proc = subprocess.Popen(
        [
            sys.executable,
            "-B",
            str(PROTECTED / "mock_server.py"),
            "--contract",
            str(CONTRACT_PATH),
            "--scenario",
            str(scenario_path),
            "--log",
            str(log_path),
            "--ready",
            str(ready_path),
            "--port",
            "0",
        ],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    deadline = time.time() + 25
    while time.time() < deadline:
        if ready_path.is_file():
            try:
                info = json.loads(ready_path.read_text(encoding="utf-8"))
            except ValueError:
                time.sleep(0.05)
                continue
            case["baseUrl"] = info["base_url"]
            case["logPath"] = log_path
            case["proc"] = proc
            return True
        if proc.poll() is not None:
            stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
            FAILURES.append("mock for case %s exited early: %s" % (case["name"], stderr.strip()))
            return False
        time.sleep(0.05)
    proc.kill()
    FAILURES.append("mock for case %s did not become ready" % case["name"])
    return False


def read_log(path):
    entries = []
    if not path.is_file():
        return entries
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return entries


# ---------------------------------------------------------------------------
# assertions
# ---------------------------------------------------------------------------
def content_type_is_json(entry):
    value = entry["headers"].get("content-type") or ""
    return value.lower().split(";")[0].strip() == "application/json"


def assert_case(case, record):
    name = case["name"]
    expect = case["expect"]
    entries = read_log(case["logPath"])
    operations = [entry["operation_id"] for entry in entries]

    def fail(message):
        FAILURES.append("[%s] %s" % (name, message))

    CHECKS[0] += 1
    if operations != expect["operations"]:
        fail("request sequence was %s, expected %s" % (operations, expect["operations"]))

    CHECKS[0] += 1
    off_contract = [entry["raw_target"] for entry in entries if not entry["on_contract"]]
    if off_contract:
        fail("off-contract requests were issued: %s" % off_contract)

    CHECKS[0] += 1
    if expect["exception"] is None:
        if record["threw"]:
            fail("expected success but the function threw %s: %s" % (record["exceptionType"], record["message"]))
    else:
        if not record["threw"]:
            fail("expected %s but the function returned normally" % expect["exception"])
        elif record["exceptionType"] != expect["exception"]:
            fail("threw %s, expected %s" % (record["exceptionType"], expect["exception"]))

    # The mutating operation must never appear on a gated path.
    CHECKS[0] += 1
    if "addVcenterDatasource" not in expect["operations"] and "addVcenterDatasource" in operations:
        fail("the mutating addVcenterDatasource operation ran even though the run must fail closed")

    by_op = {}
    for entry in entries:
        by_op.setdefault(entry["operation_id"], []).append(entry)

    authorization = "NetworkInsight " + case["token"]

    for entry in entries:
        CHECKS[0] += 1
        if entry["query"]:
            fail("%s carried an unexpected query string %r" % (entry["operation_id"], entry["query"]))
        if entry["operation_id"] == "create":
            CHECKS[0] += 1
            if "authorization" in entry["headers"]:
                fail("the create operation must not send an Authorization header")
        else:
            CHECKS[0] += 1
            if entry["headers"].get("authorization") != authorization:
                fail(
                    "%s sent Authorization %r, expected %r"
                    % (entry["operation_id"], entry["headers"].get("authorization"), authorization)
                )

    for op in ("create", "validateVCenter", "addVcenterDatasource"):
        for entry in by_op.get(op, []):
            CHECKS[0] += 1
            if not content_type_is_json(entry):
                fail("%s sent Content-Type %r, expected application/json" % (op, entry["headers"].get("content-type")))
            CHECKS[0] += 1
            if entry["body_parse_error"] is not None:
                fail("%s sent a body that is not valid JSON: %s" % (op, entry["body_parse_error"]))

    for op in ("listExpandedNodes", "delete"):
        for entry in by_op.get(op, []):
            CHECKS[0] += 1
            if entry["body_raw"] != "":
                fail("%s must not send a request body, got %r" % (op, entry["body_raw"]))

    def assert_body(op, expected):
        entries_for_op = by_op.get(op, [])
        CHECKS[0] += 1
        if len(entries_for_op) != 1:
            fail("expected exactly one %s request, saw %d" % (op, len(entries_for_op)))
            return
        actual = entries_for_op[0]["body_json"]
        CHECKS[0] += 1
        if not isinstance(actual, dict):
            fail("%s body is not a JSON object: %r" % (op, actual))
            return
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        CHECKS[0] += 1
        if missing:
            fail("%s body is missing required fields %s" % (op, missing))
        CHECKS[0] += 1
        if extra:
            fail(
                "%s body carries fields that must be omitted when unset: %s (body was %s)"
                % (op, extra, json.dumps(actual, sort_keys=True))
            )
        for key in sorted(set(expected) & set(actual)):
            CHECKS[0] += 1
            if actual[key] != expected[key]:
                fail(
                    "%s body field %r was %r, expected %r"
                    % (op, key, actual[key], expected[key])
                )
            elif isinstance(expected[key], bool) and not isinstance(actual[key], bool):
                fail("%s body field %r must be a JSON boolean" % (op, key))

    if "authBody" in expect:
        assert_body("create", expect["authBody"])
    if "validateBody" in expect:
        assert_body("validateVCenter", expect["validateBody"])
    if "addBody" in expect:
        assert_body("addVcenterDatasource", expect["addBody"])

    if expect["exception"] is None:
        CHECKS[0] += 1
        if record["outputCount"] != 1:
            fail(
                "expected exactly one pipeline output (the created data source), got %d: %s"
                % (record["outputCount"], json.dumps(record["outputs"]))
            )
        else:
            output = record["outputs"][0]
            CHECKS[0] += 1
            if not isinstance(output, dict) or output.get("entity_id") != case["entityId"]:
                fail(
                    "the returned object is not the data source from the 201 response (entity_id %r)"
                    % case["entityId"]
                )


# ---------------------------------------------------------------------------
def main():
    if shutil.which("pwsh") is None:
        print("FAIL: pwsh is not available on PATH")
        return 1

    static_checks()
    if FAILURES:
        report()
        return 1

    workdir = Path(tempfile.mkdtemp(prefix="vcfon-verify-"))
    cases = build_cases()
    started = []
    try:
        for case in cases:
            if not start_mock(workdir, case):
                break
            started.append(case)

        if len(started) != len(cases):
            report()
            return 1

        for case in cases:
            case["params"]["Server"] = case["baseUrl"]

        cases_path = workdir / "cases.json"
        cases_path.write_text(
            json.dumps([{"name": c["name"], "params": c["params"]} for c in cases]),
            encoding="utf-8",
        )
        out_path = workdir / "results.json"

        env = dict(os.environ)
        env["POWERSHELL_TELEMETRY_OPTOUT"] = "1"
        proc = subprocess.run(
            [
                "pwsh",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(PROTECTED / "invoke_cases.ps1"),
                "-ModulePath",
                str(MODULE_PSM1),
                "-CasesPath",
                str(cases_path),
                "-OutPath",
                str(out_path),
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )

        if not out_path.is_file():
            FAILURES.append(
                "the PowerShell harness produced no results.\nstdout:\n%s\nstderr:\n%s"
                % (proc.stdout.strip(), proc.stderr.strip())
            )
            report()
            return 1

        document = json.loads(out_path.read_text(encoding="utf-8"))
        if document.get("importError"):
            FAILURES.append("the module could not be imported: %s" % document["importError"])
            report()
            return 1
        if not check(document.get("commandFound"), "%s is not exported by the module" % COMMAND_NAME):
            report()
            return 1

        records = {record["name"]: record for record in document.get("cases", [])}
        for case in cases:
            record = records.get(case["name"])
            if record is None:
                FAILURES.append("[%s] the harness recorded no result for this case" % case["name"])
                continue
            assert_case(case, record)
    finally:
        for case in started:
            proc_handle = case.get("proc")
            if proc_handle is not None and proc_handle.poll() is None:
                proc_handle.terminate()
                try:
                    proc_handle.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc_handle.kill()
        shutil.rmtree(workdir, ignore_errors=True)

    return report()


def report():
    if FAILURES:
        print("FAIL (%d checks, %d failures)" % (CHECKS[0], len(FAILURES)))
        for failure in FAILURES:
            print("  - %s" % failure)
        return 1
    print("PASS (%d checks)" % CHECKS[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
