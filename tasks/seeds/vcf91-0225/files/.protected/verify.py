#!/usr/bin/env python3
"""Protected verifier for the VCF 9.1 SDDC LCM multi-step upgrade module.

Starts the contract-pinned loopback mock on an ephemeral 127.0.0.1 port, runs
the module under test twice through a genuine caller-owned PowerCLI session,
and asserts the reported run outcome together with the exact request wire shape
recorded by the mock. No live VMware endpoint is contacted.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

ROOT = Path(__file__).resolve().parent.parent
PROTECTED = ROOT / ".protected"
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MODULE_DIR = ROOT / "VcfSddcLcmUpgrade"
MANIFEST = MODULE_DIR / "VcfSddcLcmUpgrade.psd1"
IMPLEMENTATION = MODULE_DIR / "VcfSddcLcmUpgrade.psm1"

SDK_MODULE = "VMware.Sdk.Vcf.Installer"
SDK_VERSION = "13.5.0.25380678"
EXPECTED_OPERATION_IDS = [
    "setDepot",
    "resolveDepotComponents",
    "performComponentAction",
    "getTask",
]

FAILURES: list[str] = []


def check(condition, message):
    if not condition:
        FAILURES.append(message)
    return bool(condition)


def fatal(message):
    print("FAIL: " + message)
    print("\nVerification failed.")
    sys.exit(1)


def load_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


# --------------------------------------------------------------------------
# scenario construction (runtime-only identifiers)
# --------------------------------------------------------------------------


def build_scenario():
    run = uuid.uuid4().hex[:8]
    components = []
    # Ordered plan. The apply of the second component fails, so the third is
    # never attempted.
    for name, version in (
        ("vcenter", "9.1.0.0000.2410771-HF1"),
        ("nsx", "9.1.0.0000.2410814"),
        ("esx", "9.1.0.0000.2410902"),
    ):
        components.append(
            {
                "name": name,
                "id": str(uuid.uuid4()),
                "targetVersion": version,
                "binaryUrl": f"https://depot-{run}.vcf.test/{name}/{version}/manifest",
            }
        )

    depot_fqdn = f"depot-{run}.vcf.test"

    # The depot answers out of the requested order, carries a component that was
    # never requested, and carries a stale version of a requested component.
    resolved = [
        {
            "component": "VCENTER",
            "version": components[0]["targetVersion"],
            "binaryUrl": f"https://depot-{run}.vcf.test/vcenter/wrong-case-component/manifest",
        },
        {
            "component": components[0]["name"],
            "version": components[0]["targetVersion"].lower(),
            "binaryUrl": f"https://depot-{run}.vcf.test/vcenter/wrong-case-version/manifest",
        },
        {
            "component": "nsx",
            "version": "9.1.0.0000.2309001",
            "binaryUrl": f"https://depot-{run}.vcf.test/nsx/stale/manifest",
        },
        {
            "component": components[2]["name"],
            "version": components[2]["targetVersion"],
            "binaryUrl": components[2]["binaryUrl"],
        },
        {
            "component": "vcf-operations",
            "version": "9.1.0.0000.2410999",
            "binaryUrl": f"https://depot-{run}.vcf.test/vcf-operations/manifest",
        },
        {
            "component": components[1]["name"],
            "version": components[1]["targetVersion"],
            "binaryUrl": components[1]["binaryUrl"],
        },
        {
            "component": components[0]["name"],
            "version": components[0]["targetVersion"],
            "binaryUrl": components[0]["binaryUrl"],
        },
    ]

    task_keys = [
        "depot",
        "precheck:vcenter",
        "apply:vcenter",
        "precheck:nsx",
        "apply:nsx",
    ]
    tasks = {key: str(uuid.uuid4()) for key in task_keys}

    def shape(name, task_type, stages, poll, error_id="", error=""):
        return {
            "name": name,
            "type": task_type,
            "stages": stages,
            "poll": poll,
            "errorMessageId": error_id,
            "errorMessage": error,
        }

    failure_message = (
        "NSX Manager node nsx-a.vcf.test rejected the upgrade bundle: "
        "the appliance is in a degraded cluster state."
    )
    task_shapes = {
        "depot": shape(
            "fleet_depot_registration",
            "depot",
            ["depot-reachability", "depot-metadata-sync"],
            ["  running  ", "  succeeded  "],
        ),
        "precheck:vcenter": shape(
            "vcenter_precheck", "precheck",
            ["precheck-inventory", "precheck-capacity"],
            ["  running  ", "  succeeded  "],
        ),
        "apply:vcenter": shape(
            "vcenter_apply", "apply",
            ["package-deploy", "service-restart"],
            ["  running  ", "  succeeded  "],
        ),
        "precheck:nsx": shape(
            "nsx_precheck", "precheck",
            ["precheck-inventory", "precheck-capacity"],
            ["  running  ", "  succeeded  "],
        ),
        "apply:nsx": shape(
            "nsx_apply", "apply",
            ["package-deploy", "service-restart"],
            ["  running  ", "  failed  "],
            "com.broadcom.lcm.ops.nsx.upgrade.failed",
            failure_message,
        ),
    }

    return {
        "accessToken": "lcm-" + uuid.uuid4().hex,
        "refreshTokenId": str(uuid.uuid4()),
        "applianceId": str(uuid.uuid4()),
        "referencePrefix": "ref-" + run,
        "user": f"lcm-{run}@vcf.test",
        "password": "Pw-" + uuid.uuid4().hex[:12] + "!aZ",
        "depot": {
            "fqdn": depot_fqdn,
            "certificate": "-----BEGIN CERTIFICATE-----\n"
            + base64.b64encode(run.encode()).decode()
            + "\n-----END CERTIFICATE-----",
        },
        "components": components,
        "resolved": resolved,
        "tasks": tasks,
        "taskShapes": task_shapes,
        # extras exercised only by the "full" case
        "bundleVersion": "9.1.0.0-24107710",
        "manifestCertificates": [
            "LS0tLS1CRUdJTk-" + uuid.uuid4().hex[:10],
            "MIIDMjCCAheg-" + uuid.uuid4().hex[:10],
        ],
        "correlationId": str(uuid.uuid4()),
    }


# --------------------------------------------------------------------------
# request-log helpers
# --------------------------------------------------------------------------


class Request:
    def __init__(self, entry):
        self.sequence = entry["sequence"]
        self.method = entry["method"]
        self.target = entry["target"]
        self.status = entry["status"]
        self.headers = [(name, value) for name, value in entry["headers"]]
        self.body = base64.b64decode(entry["bodyBase64"])
        split = urlsplit(self.target)
        self.path = split.path
        self.query = parse_qsl(split.query, keep_blank_values=True)

    def header_values(self, name):
        lowered = name.lower()
        return [value for key, value in self.headers if key.lower() == lowered]

    def json_body(self):
        if not self.body:
            return None
        return json.loads(self.body.decode("utf-8"))

    def describe(self):
        return f"#{self.sequence} {self.method} {self.target}"


def read_log(path):
    entries = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                entries.append(Request(json.loads(line)))
    entries.sort(key=lambda item: item.sequence)
    return entries


# --------------------------------------------------------------------------
# assertions
# --------------------------------------------------------------------------


def assert_no_unexpected_members(label, body, allowed, path=""):
    """Every member present must be contracted, and none may be empty-valued."""
    for key in body:
        where = f"{path}.{key}" if path else key
        check(key in allowed, f"{label}: member '{where}' is not contracted.")


def assert_absent(label, body, names, path=""):
    for name in names:
        where = f"{path}.{name}" if path else name
        check(
            name not in body,
            f"{label}: optional member '{where}' was not supplied by the caller "
            f"and must be absent from the request body, but the wire carried "
            f"{json.dumps(body.get(name))}.",
        )


def assert_upgrade_body(label, body, component, scenario, case):
    """Assert one ComponentUpgradeSpec exactly, including omissions."""
    if not isinstance(body, dict):
        check(False, f"{label}: request body is not a JSON object.")
        return

    assert_no_unexpected_members(
        label, body, {"componentSpec", "lcmPlatformSpec", "correlationId"}
    )
    if not check("componentSpec" in body, f"{label}: componentSpec is required."):
        return

    spec = body["componentSpec"]
    if not isinstance(spec, dict):
        check(False, f"{label}: componentSpec is not a JSON object.")
        return
    assert_no_unexpected_members(
        label,
        spec,
        {"software", "depot", "policy", "userInput", "additionalInput"},
        "componentSpec",
    )
    # policy / userInput / additionalInput are optional and never supplied.
    assert_absent(
        label, spec, ["policy", "userInput", "additionalInput"], "componentSpec"
    )

    software = spec.get("software")
    if check(isinstance(software, dict), f"{label}: componentSpec.software is required."):
        check(
            software.get("version") == component["targetVersion"],
            f"{label}: componentSpec.software.version must be "
            f"{component['targetVersion']!r}, got {software.get('version')!r}.",
        )
        assert_no_unexpected_members(label, software, {"version"}, "componentSpec.software")

    depot = spec.get("depot")
    if check(isinstance(depot, dict), f"{label}: componentSpec.depot is required."):
        check(
            depot.get("url") == component["binaryUrl"],
            f"{label}: componentSpec.depot.url must be the resolved binary url "
            f"{component['binaryUrl']!r}, got {depot.get('url')!r}.",
        )
        assert_no_unexpected_members(
            label, depot, {"url", "certificate"}, "componentSpec.depot"
        )
        if case["manifestCertificates"] is None:
            assert_absent(label, depot, ["certificate"], "componentSpec.depot")
        else:
            check(
                depot.get("certificate") == case["manifestCertificates"],
                f"{label}: componentSpec.depot.certificate must be "
                f"{case['manifestCertificates']!r}, got {depot.get('certificate')!r}.",
            )

    if case["performBackup"] is None:
        assert_absent(label, body, ["lcmPlatformSpec"])
    else:
        platform = body.get("lcmPlatformSpec")
        if check(
            isinstance(platform, dict),
            f"{label}: lcmPlatformSpec was supplied by the caller and must be "
            f"present on the wire.",
        ):
            check(
                platform.get("performBackup") is case["performBackup"],
                f"{label}: lcmPlatformSpec.performBackup must be the supplied "
                f"boolean {case['performBackup']!r}, got "
                f"{platform.get('performBackup')!r}. A supplied false is a "
                f"supplied value.",
            )
            assert_no_unexpected_members(
                label, platform, {"performBackup"}, "lcmPlatformSpec"
            )

    if case["correlationId"] is None:
        assert_absent(label, body, ["correlationId"])
    else:
        check(
            body.get("correlationId") == case["correlationId"],
            f"{label}: correlationId must be {case['correlationId']!r}, got "
            f"{body.get('correlationId')!r}.",
        )


def assert_common_headers(label, request, scenario, case):
    auth = request.header_values("Authorization")
    check(
        auth == ["Bearer " + scenario["accessToken"]],
        f"{label}: Authorization must appear exactly once carrying the "
        f"caller-owned session bearer token, got {auth!r}.",
    )
    accept = request.header_values("Accept")
    check(
        len(accept) == 1 and "application/json" in accept[0],
        f"{label}: Accept must be sent exactly once as application/json, got {accept!r}.",
    )
    correlation = request.header_values("X-Correlation-Id")
    if case["correlationId"] is None:
        check(
            correlation == [],
            f"{label}: X-Correlation-Id must be absent when the caller supplied "
            f"no correlation id, got {correlation!r}.",
        )
    else:
        check(
            correlation == [case["correlationId"]],
            f"{label}: X-Correlation-Id must be sent exactly once as "
            f"{case['correlationId']!r}, got {correlation!r}.",
        )


def assert_json_content_type(label, request):
    values = request.header_values("Content-Type")
    if not check(
        len(values) == 1,
        f"{label}: Content-Type must be sent exactly once, got {values!r}.",
    ):
        return
    parts = [part.strip() for part in values[0].split(";")]
    check(
        parts[0].lower() == "application/json",
        f"{label}: Content-Type media type must be application/json, got {values[0]!r}.",
    )
    for extra in parts[1:]:
        if extra.lower().startswith("charset="):
            check(
                extra.split("=", 1)[1].strip().strip('"').lower() == "utf-8",
                f"{label}: Content-Type charset must be utf-8, got {values[0]!r}.",
            )


# --------------------------------------------------------------------------
# case execution
# --------------------------------------------------------------------------


def run_case(case, scenario, workdir, contract):
    log_path = workdir / f"requests-{case['name']}.jsonl"
    scenario_path = workdir / f"scenario-{case['name']}.json"
    plan_path = workdir / f"plan-{case['name']}.json"
    options_path = workdir / f"options-{case['name']}.json"
    report_path = workdir / f"report-{case['name']}.json"

    scenario_path.write_text(json.dumps(scenario), encoding="utf-8")
    plan_path.write_text(
        json.dumps(
            [
                {
                    "Name": component["name"],
                    "Id": component["id"],
                    "TargetVersion": component["targetVersion"],
                }
                for component in scenario["components"]
            ]
        ),
        encoding="utf-8",
    )
    # Only the optional inputs this case exercises appear as keys, so the
    # runner binds exactly those parameters and leaves the rest unbound.
    options = {}
    for key, value in (
        ("bundleVersion", case["bundleVersion"]),
        ("depotManifestCertificate", case["manifestCertificates"]),
        ("performBackup", case["performBackup"]),
        ("correlationId", case["correlationId"]),
    ):
        if value is not None:
            options[key] = value
    options_path.write_text(json.dumps(options), encoding="utf-8")
    log_path.touch()

    mock = subprocess.Popen(
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
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        port_line = mock.stdout.readline().strip()
        if not port_line.isdigit():
            fatal(
                "The contract-pinned mock did not start: "
                + (mock.stderr.read() or port_line)
            )
        port = int(port_line)

        command = [
            "pwsh",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(PROTECTED / "invoke_case.ps1"),
            "-ModuleManifest", str(MANIFEST),
            "-MockHost", "127.0.0.1",
            "-MockPort", str(port),
            "-User", scenario["user"],
            "-Password", scenario["password"],
            "-DepotFqdn", scenario["depot"]["fqdn"],
            "-DepotCertificate", scenario["depot"]["certificate"],
            "-PlanPath", str(plan_path),
            "-OptionsPath", str(options_path),
            "-OutputPath", str(report_path),
        ]

        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=300
        )
    finally:
        mock.terminate()
        try:
            mock.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover
            mock.kill()

    requests = read_log(log_path) if log_path.exists() else []
    if completed.returncode != 0 or not report_path.exists():
        detail = (completed.stdout or "") + (completed.stderr or "")
        rejected = [
            f"    {item.describe()} -> HTTP {item.status}"
            for item in requests
            if item.status >= 400
        ]
        if rejected:
            detail += "\n  The mock rejected these requests:\n" + "\n".join(rejected)
        fatal(f"case '{case['name']}' did not produce a report.\n{detail.strip()}")

    return json.loads(report_path.read_text(encoding="utf-8")), requests


def run_negative_case(kind, base_scenario, workdir):
    """Run one contracted error-path scenario and return its caught exception."""
    scenario = json.loads(json.dumps(base_scenario))
    if kind == "duplicate-resolution":
        component = scenario["components"][0]
        scenario["resolved"].append(
            {
                "component": component["name"],
                "version": component["targetVersion"],
                "binaryUrl": component["binaryUrl"] + "?duplicate=1",
            }
        )
        mode = "resolution"
    elif kind == "blank-resolution":
        component = scenario["components"][0]
        matching = next(
            entry
            for entry in scenario["resolved"]
            if entry["component"] == component["name"]
            and entry["version"] == component["targetVersion"]
        )
        matching["binaryUrl"] = "   "
        mode = "resolution"
    elif kind == "timeout":
        scenario["taskShapes"]["depot"]["poll"] = ["  running  "]
        mode = "timeout"
    else:  # pragma: no cover - verifier programming error
        raise ValueError(kind)

    log_path = workdir / f"requests-{kind}.jsonl"
    scenario_path = workdir / f"scenario-{kind}.json"
    plan_path = workdir / f"plan-{kind}.json"
    report_path = workdir / f"report-{kind}.json"
    scenario_path.write_text(json.dumps(scenario), encoding="utf-8")
    plan_path.write_text(
        json.dumps(
            [
                {
                    "Name": component["name"],
                    "Id": component["id"],
                    "TargetVersion": component["targetVersion"],
                }
                for component in scenario["components"]
            ]
        ),
        encoding="utf-8",
    )
    log_path.touch()

    mock = subprocess.Popen(
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
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        port_line = mock.stdout.readline().strip()
        if not port_line.isdigit():
            fatal(
                "The contract-pinned mock did not start: "
                + (mock.stderr.read() or port_line)
            )
        command = [
            "pwsh",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(PROTECTED / "invoke_negative_case.ps1"),
            "-ModuleManifest", str(MANIFEST),
            "-MockHost", "127.0.0.1",
            "-MockPort", port_line,
            "-User", scenario["user"],
            "-Password", scenario["password"],
            "-DepotFqdn", scenario["depot"]["fqdn"],
            "-DepotCertificate", scenario["depot"]["certificate"],
            "-PlanPath", str(plan_path),
            "-Mode", mode,
            "-OutputPath", str(report_path),
        ]
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=20
            )
        except subprocess.TimeoutExpired as exc:
            fatal(f"negative case '{kind}' exceeded 20 seconds: {exc}")
    finally:
        mock.terminate()
        try:
            mock.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover
            mock.kill()

    requests = read_log(log_path) if log_path.exists() else []
    if completed.returncode != 0 or not report_path.exists():
        detail = (completed.stdout or "") + (completed.stderr or "")
        fatal(f"negative case '{kind}' did not produce a report.\n{detail.strip()}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    return scenario, report, requests


def assert_negative_case(kind, scenario, report, requests, contract):
    check(report["threw"] is True, f"{kind}: the contracted defect was not rejected.")
    check(
        report["sessionStillOpen"] is True
        and report["tokenUnchanged"] is True
        and report["serviceUriUnchanged"] is True,
        f"{kind}: the caller-owned session was mutated while handling an error: "
        f"{report!r}.",
    )
    if kind == "timeout":
        check(
            report["exceptionType"] == "System.TimeoutException",
            f"{kind}: timeout must throw System.TimeoutException, got "
            f"{report['exceptionType']!r} ({report['exceptionMessage']!r}).",
        )

    bootstrap_paths = {
        route["path"] for route in contract["sessionBootstrap"]["routes"]
    }
    lcm = [item for item in requests if item.path.startswith("/sddc-lcm/")]
    other = [
        item
        for item in requests
        if item.path not in bootstrap_paths and item not in lcm
    ]
    check(not other, f"{kind}: unexpected requests: {[item.describe() for item in other]}.")
    task_path = f"/sddc-lcm/v1/tasks/{scenario['tasks']['depot']}"
    if kind == "timeout":
        check(
            len(lcm) >= 1
            and lcm[0].method == "POST"
            and lcm[0].path == "/sddc-lcm/v1/depot"
            and all(item.method == "GET" and item.path == task_path for item in lcm[1:]),
            f"{kind}: only setDepot and polling its exact task id are allowed; got "
            f"{[item.describe() for item in lcm]}.",
        )
    else:
        expected = [
            ("POST", "/sddc-lcm/v1/depot"),
            ("GET", task_path),
            ("GET", task_path),
            ("POST", "/sddc-lcm/v1/depot/components"),
        ]
        check(
            [(item.method, item.path) for item in lcm] == expected,
            f"{kind}: resolution defects must stop before any component action; got "
            f"{[item.describe() for item in lcm]}.",
        )


def assert_case(case, scenario, report, requests, contract):
    name = case["name"]
    components = scenario["components"]
    tasks = scenario["tasks"]
    bootstrap_paths = {
        route["path"] for route in contract["sessionBootstrap"]["routes"]
    }

    # ---- reported run outcome ----
    check(
        report["propertyOrder"]
        == ",".join(
            [
                "overallStatus",
                "depotFqdn",
                "depotTaskId",
                "resolvedComponents",
                "steps",
                "failedStep",
                "failedOperationId",
                "failedAction",
                "failedComponent",
                "failedTaskId",
                "failedStage",
                "errorMessage",
                "notAttempted",
            ]
        ),
        f"{name}: report property order is {report['propertyOrder']!r}.",
    )
    check(
        report["overallStatus"] == "FAILED",
        f"{name}: overallStatus must be FAILED, got {report['overallStatus']!r}.",
    )
    check(
        report["depotTaskId"] == tasks["depot"],
        f"{name}: depotTaskId must be {tasks['depot']!r}, got {report['depotTaskId']!r}.",
    )
    check(
        report["depotFqdn"] == scenario["depot"]["fqdn"],
        f"{name}: depotFqdn must be {scenario['depot']['fqdn']!r}, got "
        f"{report['depotFqdn']!r}.",
    )
    check(
        report["impostorRejected"] is True,
        f"{name}: the function must consume a genuine PowerCLI session; a "
        f"look-alike object carrying ServiceUri and SessionSecret was accepted.",
    )
    check(
        report["sessionStillOpen"] is True
        and report["tokenUnchanged"] is True
        and report["serviceUriUnchanged"] is True,
        f"{name}: the caller-owned PowerCLI session must be left connected and "
        f"untouched (open={report['sessionStillOpen']}, "
        f"tokenUnchanged={report['tokenUnchanged']}, "
        f"serviceUriUnchanged={report['serviceUriUnchanged']}).",
    )
    expected_validation_checks = {
        "blankDepotFqdn",
        "blankDepotCertificate",
        "emptyComponentList",
        "blankComponentName",
        "blankComponentId",
        "blankComponentTargetVersion",
        "duplicateComponentName",
        "duplicateComponentId",
        "pollBelowRange",
        "pollAboveRange",
        "timeoutBelowRange",
        "timeoutAboveRange",
    }
    validation_results = report["validationResults"]
    check(
        set(validation_results) == expected_validation_checks
        and all(value is True for value in validation_results.values()),
        f"{name}: every invalid input must be rejected before a request; got "
        f"{validation_results!r}.",
    )
    check(
        report["pollIntervalDefault"] == 2 and report["timeoutDefault"] == 300,
        f"{name}: defaults must be PollIntervalSeconds=2 and TimeoutSeconds=300; "
        f"got {report['pollIntervalDefault']!r} and {report['timeoutDefault']!r}.",
    )
    check(
        report["exportedFunctions"] == ["Invoke-VcfSddcLcmComponentUpgrade"],
        f"{name}: the module must export exactly Invoke-VcfSddcLcmComponentUpgrade; "
        f"got {report['exportedFunctions']!r}.",
    )

    expected_resolved = [
        {
            "component": component["name"],
            "version": component["targetVersion"],
            "binaryUrl": component["binaryUrl"],
        }
        for component in components
    ]
    check(
        report["resolvedComponents"] == expected_resolved,
        f"{name}: resolvedComponents must match the plan order with the correct "
        f"binary urls.\n    expected {expected_resolved}\n    got      "
        f"{report['resolvedComponents']}",
    )

    # The heart of the scenario: earlier steps keep their true outcome, the
    # failing step is reported as failed, and nothing after it runs.
    expected_steps = [
        ("setDepot", "", "", tasks["depot"], "SUCCEEDED"),
        ("resolveDepotComponents", "", "", "", "SUCCEEDED"),
        ("performComponentAction", "precheck", "vcenter", tasks["precheck:vcenter"], "SUCCEEDED"),
        ("performComponentAction", "apply", "vcenter", tasks["apply:vcenter"], "SUCCEEDED"),
        ("performComponentAction", "precheck", "nsx", tasks["precheck:nsx"], "SUCCEEDED"),
        ("performComponentAction", "apply", "nsx", tasks["apply:nsx"], "FAILED"),
    ]
    got_steps = [
        (
            step["operationId"],
            step["action"],
            step["component"],
            step["taskId"],
            step["status"],
        )
        for step in report["steps"]
    ]
    if check(
        got_steps == expected_steps,
        f"{name}: the reported steps are wrong.\n    expected {expected_steps}\n"
        f"    got      {got_steps}",
    ):
        for index, step in enumerate(report["steps"], start=1):
            check(
                step["stepNumber"] == index,
                f"{name}: step {index} carries stepNumber {step['stepNumber']}.",
            )
            check(
                step["propertyOrder"]
                == "stepNumber,operationId,action,component,taskId,status",
                f"{name}: step property order is {step['propertyOrder']!r}.",
            )

    check(
        report["failedStep"] == 6,
        f"{name}: failedStep must be 6, got {report['failedStep']}.",
    )
    check(
        report["failedOperationId"] == "performComponentAction",
        f"{name}: failedOperationId is {report['failedOperationId']!r}.",
    )
    check(
        report["failedAction"] == "apply",
        f"{name}: failedAction must be 'apply', got {report['failedAction']!r}.",
    )
    check(
        report["failedComponent"] == "nsx",
        f"{name}: failedComponent must be 'nsx', got {report['failedComponent']!r}.",
    )
    check(
        report["failedTaskId"] == tasks["apply:nsx"],
        f"{name}: failedTaskId must be {tasks['apply:nsx']!r}, got "
        f"{report['failedTaskId']!r}.",
    )
    check(
        report["failedStage"] == "service-restart",
        f"{name}: failedStage must be the stage that actually failed "
        f"('service-restart'), got {report['failedStage']!r}.",
    )
    check(
        report["errorMessage"] == scenario["taskShapes"]["apply:nsx"]["errorMessage"],
        f"{name}: errorMessage must be the failed stage's error message, got "
        f"{report['errorMessage']!r}.",
    )
    check(
        report["notAttempted"] == ["esx"],
        f"{name}: components after the failure must be reported as not "
        f"attempted; expected ['esx'], got {report['notAttempted']!r}.",
    )

    # ---- wire shape ----
    bootstrap = [item for item in requests if item.path in bootstrap_paths]
    lcm = [item for item in requests if item.path.startswith("/sddc-lcm/")]
    other = [
        item
        for item in requests
        if item not in bootstrap and item not in lcm
    ]
    check(
        not other,
        f"{name}: the module issued requests outside the contracted SDDC LCM "
        f"surface: {[item.describe() for item in other]}",
    )
    # The session belongs to the caller: connect happens before the run and
    # disconnect after it, so no bootstrap route may appear in between.
    if lcm and bootstrap:
        first, last = lcm[0].sequence, lcm[-1].sequence
        interleaved = [
            item for item in bootstrap if first < item.sequence < last
        ]
        check(
            not interleaved,
            f"{name}: the module must consume the caller-owned session, not "
            f"authenticate. These session routes were called during the run: "
            f"{[item.describe() for item in interleaved]}",
        )
    check(
        len([item for item in bootstrap if item.path == "/v1/tokens"]) == 1,
        f"{name}: POST /v1/tokens must be issued exactly once, by the verifier's "
        f"Connect-VcfInstallerServer.",
    )
    check(
        all(item.status < 400 for item in lcm),
        f"{name}: the mock rejected "
        f"{[(item.describe(), item.status) for item in lcm if item.status >= 400]}.",
    )

    base = "/sddc-lcm"
    expected_wire = [
        ("POST", f"{base}/v1/depot", None),
        ("GET", f"{base}/v1/tasks/{tasks['depot']}", None),
        ("GET", f"{base}/v1/tasks/{tasks['depot']}", None),
        ("POST", f"{base}/v1/depot/components", None),
        ("POST", f"{base}/v1/components/{components[0]['id']}", "precheck"),
        ("GET", f"{base}/v1/tasks/{tasks['precheck:vcenter']}", None),
        ("GET", f"{base}/v1/tasks/{tasks['precheck:vcenter']}", None),
        ("POST", f"{base}/v1/components/{components[0]['id']}", "apply"),
        ("GET", f"{base}/v1/tasks/{tasks['apply:vcenter']}", None),
        ("GET", f"{base}/v1/tasks/{tasks['apply:vcenter']}", None),
        ("POST", f"{base}/v1/components/{components[1]['id']}", "precheck"),
        ("GET", f"{base}/v1/tasks/{tasks['precheck:nsx']}", None),
        ("GET", f"{base}/v1/tasks/{tasks['precheck:nsx']}", None),
        ("POST", f"{base}/v1/components/{components[1]['id']}", "apply"),
        ("GET", f"{base}/v1/tasks/{tasks['apply:nsx']}", None),
        ("GET", f"{base}/v1/tasks/{tasks['apply:nsx']}", None),
    ]
    got_wire = [
        (item.method, item.path, dict(item.query).get("action")) for item in lcm
    ]
    if not check(
        got_wire == expected_wire,
        f"{name}: the ordered SDDC LCM request sequence is wrong.\n"
        f"    expected {expected_wire}\n    got      {got_wire}",
    ):
        return

    # No request may touch the component that follows the failure.
    third_id = components[2]["id"]
    check(
        not any(third_id in item.path for item in lcm),
        f"{name}: component '{components[2]['name']}' follows the failed apply "
        f"and must never be contacted.",
    )

    for item in lcm:
        assert_common_headers(f"{name} {item.describe()}", item, scenario, case)

    # setDepot body
    set_depot = lcm[0]
    assert_json_content_type(f"{name} setDepot", set_depot)
    body = set_depot.json_body()
    label = f"{name} setDepot"
    if check(isinstance(body, dict), f"{label}: body is not a JSON object."):
        check(
            body.get("fqdn") == scenario["depot"]["fqdn"],
            f"{label}: fqdn must be {scenario['depot']['fqdn']!r}, got "
            f"{body.get('fqdn')!r}.",
        )
        check(
            body.get("certificate") == scenario["depot"]["certificate"],
            f"{label}: certificate must be the supplied PEM chain.",
        )
        assert_no_unexpected_members(label, body, {"fqdn", "certificate"})

    # getTask carries no body at all.
    for item in lcm:
        if item.method == "GET":
            check(
                item.body == b"",
                f"{name} {item.describe()}: getTask must not carry a request body.",
            )

    # resolveDepotComponents body
    resolve = lcm[3]
    assert_json_content_type(f"{name} resolveDepotComponents", resolve)
    body = resolve.json_body()
    label = f"{name} resolveDepotComponents"
    if check(isinstance(body, dict), f"{label}: body is not a JSON object."):
        assert_no_unexpected_members(
            label, body, {"fleetDepotSpec", "componentVersions", "version"}
        )
        fleet = body.get("fleetDepotSpec")
        if check(isinstance(fleet, dict), f"{label}: fleetDepotSpec is required."):
            check(
                fleet.get("fqdn") == scenario["depot"]["fqdn"]
                and fleet.get("certificate") == scenario["depot"]["certificate"],
                f"{label}: fleetDepotSpec must repeat the supplied depot endpoint.",
            )
            assert_no_unexpected_members(
                label, fleet, {"fqdn", "certificate"}, "fleetDepotSpec"
            )
        expected_versions = [
            {"component": component["name"], "version": component["targetVersion"]}
            for component in scenario["components"]
        ]
        check(
            body.get("componentVersions") == expected_versions,
            f"{label}: componentVersions must list every planned component in "
            f"order.\n    expected {expected_versions}\n    got      "
            f"{body.get('componentVersions')}",
        )
        if case["bundleVersion"] is None:
            assert_absent(label, body, ["version"])
        else:
            check(
                body.get("version") == case["bundleVersion"],
                f"{label}: version must be {case['bundleVersion']!r}, got "
                f"{body.get('version')!r}.",
            )

    # performComponentAction bodies
    for index, component_index, action in (
        (4, 0, "precheck"),
        (7, 0, "apply"),
        (10, 1, "precheck"),
        (13, 1, "apply"),
    ):
        item = lcm[index]
        component = scenario["components"][component_index]
        label = f"{name} performComponentAction {action} {component['name']}"
        assert_json_content_type(label, item)
        check(
            item.query == [("action", action)],
            f"{label}: the query must be exactly action={action}, got {item.query!r}.",
        )
        assert_upgrade_body(label, item.json_body(), component, scenario, case)


# --------------------------------------------------------------------------
# static checks
# --------------------------------------------------------------------------


def static_checks(contract, sources):
    check(
        contract["operationIds"] == EXPECTED_OPERATION_IDS,
        f"docs/contract.json must name exactly {EXPECTED_OPERATION_IDS}, got "
        f"{contract['operationIds']}.",
    )
    check(
        sources["operationIds"] == EXPECTED_OPERATION_IDS,
        f"docs/official_sources.json must name exactly {EXPECTED_OPERATION_IDS}.",
    )
    check(
        contract["source"]["repositoryCommitSha"]
        == sources["repositoryCommitSha"]
        == "3949fc33339fc5ea1b77eadb258f1cf49aa88e26",
        "The pinned vmware/vcf-api-specs commit sha changed.",
    )
    check(
        sources["specPath"] == "specifications/sddc-lcm/sddc-lcm-openapi.yaml",
        "The spec path must point at the SDDC LCM OpenAPI document.",
    )
    check(
        contract["service"]["basePath"] == "/sddc-lcm",
        "The contracted service base path must be /sddc-lcm.",
    )

    if not IMPLEMENTATION.exists():
        fatal(
            f"{IMPLEMENTATION.relative_to(ROOT)} does not exist. Create the module "
            f"implementation."
        )
    text = IMPLEMENTATION.read_text(encoding="utf-8", errors="replace")
    banned = {
        "Install-Module": "the module must not install anything",
        "Save-Module": "the module must not download anything",
        "Find-Module": "the module must not reach the gallery",
        "Connect-VcfInstallerServer": "the caller owns the session",
        "Disconnect-VcfInstallerServer": "the caller owns the session",
    }
    for needle, why in banned.items():
        check(
            needle not in text,
            f"{IMPLEMENTATION.name} must not reference {needle}: {why}.",
        )


def preflight():
    if not shutil.which("pwsh"):
        fatal("pwsh is required but was not found on PATH.")
    probe = subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$m = Get-Module -ListAvailable -Name '%s' | "
            "Where-Object { $_.Version.ToString() -eq '%s' }; "
            "if ($m) { 'present' } else { 'missing' }" % (SDK_MODULE, SDK_VERSION),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if "present" not in (probe.stdout or ""):
        fatal(
            f"{SDK_MODULE} {SDK_VERSION} is a prerequisite that the environment "
            f"must install; it was not found. {(probe.stdout or '').strip()} "
            f"{(probe.stderr or '').strip()}"
        )


def main():
    for path in (CONTRACT_PATH, SOURCES_PATH, MANIFEST):
        if not path.exists():
            fatal(f"missing protected artefact {path.relative_to(ROOT)}")

    contract = load_json(CONTRACT_PATH)
    sources = load_json(SOURCES_PATH)

    static_checks(contract, sources)
    if FAILURES:
        for failure in FAILURES:
            print("FAIL: " + failure)
        print("\nVerification failed.")
        sys.exit(1)

    preflight()

    scenario = build_scenario()
    cases = [
        {
            "name": "minimal",
            "bundleVersion": None,
            "manifestCertificates": None,
            "performBackup": None,
            "correlationId": None,
        },
        {
            "name": "full",
            "bundleVersion": scenario["bundleVersion"],
            "manifestCertificates": scenario["manifestCertificates"],
            # A supplied false must still reach the wire.
            "performBackup": False,
            "correlationId": scenario["correlationId"],
        },
    ]

    with tempfile.TemporaryDirectory(prefix="vcf-sddc-lcm-") as raw:
        workdir = Path(raw)
        for case in cases:
            report, requests = run_case(case, scenario, workdir, contract)
            assert_case(case, scenario, report, requests, contract)
        for kind in ("duplicate-resolution", "blank-resolution", "timeout"):
            negative_scenario, report, requests = run_negative_case(
                kind, scenario, workdir
            )
            assert_negative_case(
                kind, negative_scenario, report, requests, contract
            )

    if FAILURES:
        for failure in FAILURES:
            print("FAIL: " + failure)
        print(f"\nVerification failed with {len(FAILURES)} problem(s).")
        sys.exit(1)

    print("All SDDC LCM upgrade-run checks passed.")


if __name__ == "__main__":
    main()
