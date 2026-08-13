#!/usr/bin/env python3
"""Protected verification for the VCF Operations for Networks onboarding module.

Starts the loopback appliance stand-in on an ephemeral port, drives the module
against it once, then asserts the exact wire shape of every request the module
produced by reading the stand-in's request log.  Nothing here contacts a live
VMware endpoint; the only URL handed to the module is 127.0.0.1.

    python3 tests/verify.py            # from the repository root

Exits 0 when every check passes, 1 otherwise.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MANIFEST = os.path.join("src", "VcfOpsNetworksOnboarding", "VcfOpsNetworksOnboarding.psd1")
CMDLET = "Invoke-VcfOpsNetworksApplicationOnboarding"
REQUIRED_SDK_MODULE = "VMware.Sdk.Vcf.Ops"
DEFINITIONS = os.path.join("onboarding", "applications.json")
CONTRACT = os.path.join("docs", "contract.json")
STATE = os.path.join("mock", "fixtures", "appliance-state.json")
MOCK = os.path.join("mock", "vcfops_networks_mock.py")

PAGE_SIZE = 100
AUTH_HEADER = "authorization"
AUTH_PREFIX = "NetworkInsight "

EXPECTED_CREATED = [
    "payments-core",
    "identity-broker",
    "checkout-frontend",
    "risk-scoring",
    "telemetry-pipeline",
    "fraud-detection",
]
EXPECTED_SKIPPED = ["ledger-archive", "partner-gateway-legacy"]

DRIVER = r"""
param(
    [Parameter(Mandatory)][string] $Manifest,
    [Parameter(Mandatory)][string] $Server,
    [Parameter(Mandatory)][string] $DefinitionPath,
    [Parameter(Mandatory)][string] $OutFile,
    [Parameter(Mandatory)][string] $Username,
    [Parameter(Mandatory)][string] $Password,
    [string] $DomainType = 'LOCAL',
    [AllowEmptyString()][string] $DomainValue,
    [int] $PageSize = 100,
    [switch] $ExplicitOptions
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$report = [ordered]@{
    ok = $false; error = $null; result = $null
    manifest = $null; exported = @(); parameters = @()
    requiredModules = @()
}

try {
    $manifestData = Import-PowerShellDataFile -LiteralPath $Manifest
    $report['manifest'] = @{
        RootModule    = [string]$manifestData['RootModule']
        ModuleVersion = [string]$manifestData['ModuleVersion']
    }
    $report['requiredModules'] = @(
        foreach ($entry in @($manifestData['RequiredModules'])) {
            if ($entry -is [System.Collections.IDictionary]) { [string]$entry['ModuleName'] } else { [string]$entry }
        }
    )

    # Import the root module directly. The SDK is a genuine deployment
    # prerequisite declared by the manifest, but verification stays offline and
    # does not install, vendor, or fake that external package.
    $rootModulePath = Join-Path (Split-Path -Parent $Manifest) ([string]$manifestData['RootModule'])
    Import-Module -Name $rootModulePath -Force -ErrorAction Stop
    $moduleName = [System.IO.Path]::GetFileNameWithoutExtension($rootModulePath)
    $report['exported'] = @((Get-Module -Name $moduleName).ExportedCommands.Keys | Sort-Object)
    $report['parameters'] = @((Get-Command -Name '__CMDLET__').Parameters.Keys | Sort-Object)

    $credential = [pscredential]::new($Username, (ConvertTo-SecureString $Password -AsPlainText -Force))
    if ($ExplicitOptions) {
        $result = & '__CMDLET__' -Server $Server -Credential $credential -DefinitionPath $DefinitionPath `
            -DomainType $DomainType -DomainValue $DomainValue -PageSize $PageSize
    }
    else {
        # Omit every optional parameter so the documented LOCAL and 100 defaults
        # are exercised on the wire, not merely accepted by parameter binding.
        $result = & '__CMDLET__' -Server $Server -Credential $credential -DefinitionPath $DefinitionPath
    }

    $report['result'] = [ordered]@{
        Created           = @($result.Created)
        Skipped           = @($result.Skipped)
        TokenRefreshCount = [int]$result.TokenRefreshCount
    }
    $report['ok'] = $true
}
catch {
    $report['error'] = ($_ | Out-String)
}

$report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $OutFile -Encoding utf8
"""


class Failure(Exception):
    pass


CHECKS = []


def check(label, condition, detail=""):
    CHECKS.append((label, bool(condition), detail))
    if not condition:
        raise Failure("%s%s" % (label, (": " + detail) if detail else ""))


def soft_check(label, condition, detail=""):
    CHECKS.append((label, bool(condition), detail))
    return bool(condition)


# --------------------------------------------------------------------------
# Expected request bodies, derived from the CMDB export by the same rules the
# module is required to follow.
# --------------------------------------------------------------------------
def expected_member(source):
    return {"key": {"entity_id": source["entity_id"], "entity_type": source["entity_type"]},
            "name": source["name"]}


def expected_tier_body(tier):
    body = {"name": tier["name"]}

    criteria = []
    if tier.get("search_membership"):
        criteria.append({
            "membership_type": "SearchMembershipCriteria",
            "search_membership_criteria": {
                "entity_type": tier["search_membership"]["entity_type"],
                "filter": tier["search_membership"]["filter"],
            },
        })
    if tier.get("ip_membership") and tier["ip_membership"].get("ip_addresses"):
        criteria.append({
            "membership_type": "IPAddressMembershipCriteria",
            "ip_address_membership_criteria": {
                "ip_addresses": list(tier["ip_membership"]["ip_addresses"]),
            },
        })
    if criteria:
        body["group_membership_criteria"] = criteria

    members = {}
    for source_key, target_key in (
        ("member_vms", "vms"),
        ("member_physical_ips", "physical_ips"),
        ("member_kubernetes_services", "kubernetes_services"),
    ):
        items = tier.get(source_key) or []
        if items:
            members[target_key] = [expected_member(m) for m in items]
    if members:
        body["member_list"] = members
    return body


def expected_application_body(app):
    body = {"name": app["name"]}
    groups = app.get("source_group_entity_id") or []
    if groups:
        body["source_group_entity_id"] = list(groups)
    if app.get("enable_intent") is not None:
        body["enable_intent"] = bool(app["enable_intent"])
    body["tiers"] = [expected_tier_body(t) for t in app["tiers"]]
    return body


def find_empties(node, path="body"):
    """Every place an optional field was sent empty instead of being omitted."""
    problems = []
    if node is None:
        problems.append("%s is null" % path)
    elif isinstance(node, str):
        if node == "":
            problems.append("%s is an empty string" % path)
    elif isinstance(node, dict):
        if not node:
            problems.append("%s is an empty object" % path)
        for key, value in node.items():
            problems.extend(find_empties(value, "%s.%s" % (path, key)))
    elif isinstance(node, list):
        if not node:
            problems.append("%s is an empty array" % path)
        for i, value in enumerate(node):
            problems.extend(find_empties(value, "%s[%d]" % (path, i)))
    return problems


def as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def wait_for_port(path, proc, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise Failure("the appliance stand-in exited early (rc=%s)" % proc.returncode)
        if os.path.exists(path):
            text = open(path, encoding="utf-8").read().strip()
            if text.isdigit():
                port = int(text)
                with socket.socket() as probe:
                    probe.settimeout(1.0)
                    if probe.connect_ex(("127.0.0.1", port)) == 0:
                        return port
        time.sleep(0.05)
    raise Failure("the appliance stand-in never started listening")


# --------------------------------------------------------------------------
def run(workdir):
    pwsh = shutil.which("pwsh")
    check("PowerShell 7 (pwsh) is on PATH", pwsh is not None,
          "install PowerShell 7 or run scripts/setup.sh")

    for relative in (MANIFEST, DEFINITIONS, CONTRACT, STATE, MOCK):
        check("%s exists" % relative, os.path.isfile(os.path.join(ROOT, relative)))

    with open(os.path.join(ROOT, STATE), encoding="utf-8") as fh:
        state = json.load(fh)
    with open(os.path.join(ROOT, DEFINITIONS), encoding="utf-8") as fh:
        definitions = json.load(fh)["applications"]
    by_name = {a["name"]: a for a in definitions}

    log_path = os.path.join(workdir, "requests.jsonl")
    port_path = os.path.join(workdir, "port")
    report_path = os.path.join(workdir, "report.json")
    driver_path = os.path.join(workdir, "driver.ps1")

    with open(driver_path, "w", encoding="utf-8") as fh:
        fh.write(DRIVER.replace("__CMDLET__", CMDLET))

    mock = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, MOCK),
         "--contract", os.path.join(ROOT, CONTRACT),
         "--state", os.path.join(ROOT, STATE),
         "--log", log_path, "--host", "127.0.0.1", "--port", "0",
         "--port-file", port_path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    try:
        port = wait_for_port(port_path, mock)
        server = "http://127.0.0.1:%d" % port
        print("appliance stand-in listening on %s" % server)

        completed = subprocess.run(
            [pwsh, "-NoProfile", "-NonInteractive", "-File", driver_path,
             "-Manifest", os.path.join(ROOT, MANIFEST),
             "-Server", server,
             "-DefinitionPath", os.path.join(ROOT, DEFINITIONS),
             "-OutFile", report_path,
             "-Username", state["credentials"]["username"],
             "-Password", state["credentials"]["password"]],
            cwd=ROOT, capture_output=True, text=True, timeout=900)
    finally:
        mock.terminate()
        try:
            mock.wait(timeout=10)
        except subprocess.TimeoutExpired:
            mock.kill()

    check("the module run produced a report", os.path.isfile(report_path),
          "pwsh stdout:\n%s\npwsh stderr:\n%s" % (completed.stdout[-4000:], completed.stderr[-4000:]))
    with open(report_path, encoding="utf-8") as fh:
        report = json.load(fh)
    check("the module ran without raising", report.get("ok") is True,
          (report.get("error") or "")[:4000])

    # -- packaging ---------------------------------------------------------
    exported = as_list(report.get("exported"))
    check("the module exports exactly %s" % CMDLET, exported == [CMDLET], repr(exported))
    parameters = set(as_list(report.get("parameters")))
    expected_parameters = {
        "Server", "Credential", "DefinitionPath", "DomainType", "DomainValue", "PageSize",
    }
    check("the exported function exposes every documented parameter",
          expected_parameters <= parameters,
          "missing %s" % repr(sorted(expected_parameters - parameters)))
    required = as_list(report.get("requiredModules"))
    check("the manifest declares %s in RequiredModules" % REQUIRED_SDK_MODULE,
          REQUIRED_SDK_MODULE in required, repr(required))

    vendored = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in dirnames + filenames:
            lowered = name.lower()
            if lowered.startswith(("vmware.sdk.", "vmware.openapi", "vmware.bindings")) or lowered.endswith(".dll"):
                vendored.append(os.path.relpath(os.path.join(dirpath, name), ROOT))
    check("no VMware SDK content is vendored into the repository", not vendored, repr(vendored[:10]))

    # -- returned summary --------------------------------------------------
    result = report.get("result") or {}
    check("Created lists every application that had to be created, in batch order",
          as_list(result.get("Created")) == EXPECTED_CREATED, repr(result.get("Created")))
    check("Skipped lists the applications the appliance already held",
          as_list(result.get("Skipped")) == EXPECTED_SKIPPED, repr(result.get("Skipped")))
    check("the token was re-acquired exactly once", result.get("TokenRefreshCount") == 1,
          repr(result.get("TokenRefreshCount")))

    # -- request log -------------------------------------------------------
    with open(log_path, encoding="utf-8") as fh:
        log = [json.loads(line) for line in fh if line.strip()]
    check("the appliance stand-in recorded requests", log)

    unknown = [r for r in log if r["operationId"] is None]
    check("every request addressed an operation the contract names", not unknown,
          repr([(r["method"], r["path"]) for r in unknown[:5]]))
    server_errors = [r for r in log if r["status"] >= 500]
    check("the appliance never returned a server error", not server_errors,
          repr([(r["operationId"], r["status"]) for r in server_errors[:5]]))
    rejected = [r for r in log if r["status"] == 400]
    check("no request was rejected as malformed", not rejected,
          repr([(r["operationId"], r["bodyRaw"][:200]) for r in rejected[:3]]))

    for entry in log:
        problems = find_empties(entry["body"]) if entry["body"] is not None else []
        check("%s #%d omits optional fields it has no value for"
              % (entry["operationId"], entry["seq"]), not problems, "; ".join(problems))

    creates = [r for r in log if r["operationId"] == "create"]
    lists = [r for r in log if r["operationId"] == "listApplications"]
    adds = [r for r in log if r["operationId"] == "addApplicationWithTiers"]
    deletes = [r for r in log if r["operationId"] == "delete"]

    # -- token acquisition -------------------------------------------------
    check("the token was acquired exactly twice: once up front, once after the session ended",
          len(creates) == 2, "saw %d create calls" % len(creates))
    for entry in creates:
        seq = entry["seq"]
        check("token request #%d carries no Authorization header" % seq,
              AUTH_HEADER not in entry["headers"], entry["headers"].get(AUTH_HEADER, ""))
        check("token request #%d is sent as JSON" % seq,
              entry["headers"].get("content-type", "").startswith("application/json"),
              entry["headers"].get("content-type", "<absent>"))
        body = entry["body"]
        check("token request #%d sends exactly username, password and domain" % seq,
              isinstance(body, dict) and set(body) == {"username", "password", "domain"},
              repr(sorted(body) if isinstance(body, dict) else body))
        check("token request #%d authenticates as the service account" % seq,
              body["username"] == state["credentials"]["username"], body["username"])
        domain = body["domain"]
        check("token request #%d sends domain_type only, with no domain value" % seq,
              isinstance(domain, dict) and set(domain) == {"domain_type"}, repr(domain))
        check("token request #%d declares the LOCAL domain" % seq,
              domain["domain_type"] == "LOCAL", repr(domain["domain_type"]))
        check("token request #%d succeeded" % seq, entry["status"] == 200, repr(entry["status"]))

    issued = state["tokens"]["issue_order"]
    first_token, second_token = issued[0], issued[1]

    authenticated = [r for r in log if r["operationId"] != "create"]
    for entry in authenticated:
        header = entry["headers"].get(AUTH_HEADER)
        check("%s #%d authenticates with the contract's ApiKeyAuth scheme"
              % (entry["operationId"], entry["seq"]),
              isinstance(header, str) and header.startswith(AUTH_PREFIX),
              repr(header))

    unauthorized = [r for r in log if r["status"] == 401]
    check("the session ended exactly once mid-run", len(unauthorized) == 1,
          repr([(r["operationId"], r["seq"]) for r in unauthorized]))
    refused = unauthorized[0]
    check("the rejected request was an application create",
          refused["operationId"] == "addApplicationWithTiers", refused["operationId"])
    check("the rejected request was the one carrying the first token",
          refused["headers"].get(AUTH_HEADER) == AUTH_PREFIX + first_token)

    after = [r for r in log if r["seq"] > refused["seq"]]
    check("a new token was acquired straight after the refusal",
          after and after[0]["operationId"] == "create",
          repr(after[0]["operationId"]) if after else "nothing followed")
    check("the refused request was replayed next",
          len(after) > 1 and after[1]["operationId"] == "addApplicationWithTiers",
          repr(after[1]["operationId"]) if len(after) > 1 else "nothing followed")
    replay = after[1]
    check("the replay carries the newly acquired token",
          replay["headers"].get(AUTH_HEADER) == AUTH_PREFIX + second_token,
          repr(replay["headers"].get(AUTH_HEADER)))
    check("the replay sends byte-for-byte the same body as the refused request",
          replay["bodyRaw"] == refused["bodyRaw"])
    check("the replay succeeded", replay["status"] == 201, repr(replay["status"]))

    check("no request after the refusal reuses the dead token",
          not [r for r in after if r["headers"].get(AUTH_HEADER) == AUTH_PREFIX + first_token])

    # -- listApplications --------------------------------------------------
    check("the existing applications were enumerated across both pages",
          len(lists) == 2, "saw %d listApplications calls" % len(lists))
    check("the first page is requested with the page size only and no cursor",
          lists[0]["query"] == {"size": [str(PAGE_SIZE)]}, repr(lists[0]["query"]))
    check("the second page is requested with the cursor the first page returned",
          lists[1]["query"] == {"size": [str(PAGE_SIZE)], "cursor": ["Mg=="]}, repr(lists[1]["query"]))
    for entry in lists:
        check("listApplications #%d sends no request body" % entry["seq"],
              entry["bodyRaw"] == "", repr(entry["bodyRaw"][:200]))
        check("listApplications #%d succeeded" % entry["seq"], entry["status"] == 200,
              repr(entry["status"]))
    check("the enumeration happens before anything is created",
          all(l["seq"] < adds[0]["seq"] for l in lists) if adds else False)

    # -- addApplicationWithTiers -------------------------------------------
    check("one create call per application, plus the single replay",
          len(adds) == len(EXPECTED_CREATED) + 1, "saw %d create calls" % len(adds))

    succeeded = [r for r in adds if r["status"] == 201]
    names = [r["body"]["name"] for r in succeeded]
    check("each application was created exactly once, in batch order",
          names == EXPECTED_CREATED, repr(names))
    check("no application the appliance already held was re-sent",
          not (set(names) & set(EXPECTED_SKIPPED)), repr(sorted(set(names) & set(EXPECTED_SKIPPED))))

    for entry in adds:
        seq = entry["seq"]
        check("application create #%d sends no If-Match header" % seq,
              "if-match" not in entry["headers"], entry["headers"].get("if-match", ""))
        check("application create #%d is sent as JSON" % seq,
              entry["headers"].get("content-type", "").startswith("application/json"),
              entry["headers"].get("content-type", "<absent>"))
        check("application create #%d sends no query parameters" % seq,
              entry["query"] == {}, repr(entry["query"]))
        expected = expected_application_body(by_name[entry["body"]["name"]])
        check("application create #%d ('%s') matches the contract body exactly"
              % (seq, entry["body"]["name"]), entry["body"] == expected,
              "sent:     %s\n   expected: %s"
              % (json.dumps(entry["body"], sort_keys=True), json.dumps(expected, sort_keys=True)))

    # -- token release -----------------------------------------------------
    check("the token is released once the batch is done", len(deletes) == 1,
          "saw %d delete calls" % len(deletes))
    check("the token release is the last request", deletes[0]["seq"] == log[-1]["seq"])
    check("the token release presents the token currently held",
          deletes[0]["headers"].get(AUTH_HEADER) == AUTH_PREFIX + second_token,
          repr(deletes[0]["headers"].get(AUTH_HEADER)))
    check("the token release succeeded", deletes[0]["status"] == 204, repr(deletes[0]["status"]))

    # -- explicit LDAP/domain/page-size parameters -------------------------
    # The main run deliberately omits all optional parameters to exercise the
    # defaults. A short, skip-only batch against a fresh appliance verifies the
    # non-default parameter values without repeating the mutation-heavy batch.
    ldap_state = json.loads(json.dumps(state))
    ldap_state["credentials"]["domain_type"] = "LDAP"
    ldap_state["credentials"]["domain_value"] = "corp.example"
    ldap_state["tokens"]["revoke_after_successful_creates"] = 99

    ldap_state_path = os.path.join(workdir, "ldap-state.json")
    ldap_definitions_path = os.path.join(workdir, "existing-applications.json")
    ldap_log_path = os.path.join(workdir, "ldap-requests.jsonl")
    ldap_port_path = os.path.join(workdir, "ldap-port")
    ldap_report_path = os.path.join(workdir, "ldap-report.json")
    with open(ldap_state_path, "w", encoding="utf-8") as fh:
        json.dump(ldap_state, fh)
    with open(ldap_definitions_path, "w", encoding="utf-8") as fh:
        json.dump({"applications": [by_name[name] for name in EXPECTED_SKIPPED]}, fh)

    ldap_mock = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, MOCK),
         "--contract", os.path.join(ROOT, CONTRACT),
         "--state", ldap_state_path,
         "--log", ldap_log_path, "--host", "127.0.0.1", "--port", "0",
         "--port-file", ldap_port_path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        ldap_port = wait_for_port(ldap_port_path, ldap_mock)
        completed_ldap = subprocess.run(
            [pwsh, "-NoProfile", "-NonInteractive", "-File", driver_path,
             "-Manifest", os.path.join(ROOT, MANIFEST),
             "-Server", "http://127.0.0.1:%d" % ldap_port,
             "-DefinitionPath", ldap_definitions_path,
             "-OutFile", ldap_report_path,
             "-Username", ldap_state["credentials"]["username"],
             "-Password", ldap_state["credentials"]["password"],
             "-DomainType", "LDAP", "-DomainValue", "corp.example",
             "-PageSize", "1", "-ExplicitOptions"],
            cwd=ROOT, capture_output=True, text=True, timeout=300)
    finally:
        ldap_mock.terminate()
        try:
            ldap_mock.wait(timeout=10)
        except subprocess.TimeoutExpired:
            ldap_mock.kill()

    check("the explicit-parameter run produced a report", os.path.isfile(ldap_report_path),
          "pwsh stdout:\n%s\npwsh stderr:\n%s"
          % (completed_ldap.stdout[-4000:], completed_ldap.stderr[-4000:]))
    with open(ldap_report_path, encoding="utf-8") as fh:
        ldap_report = json.load(fh)
    check("the explicit LDAP/domain/page-size run completed",
          ldap_report.get("ok") is True, (ldap_report.get("error") or "")[:4000])
    ldap_result = ldap_report.get("result") or {}
    check("the explicit-parameter batch skips the existing applications in file order",
          as_list(ldap_result.get("Created")) == []
          and as_list(ldap_result.get("Skipped")) == EXPECTED_SKIPPED,
          repr(ldap_result))
    check("a run with no 401 reports no token refresh",
          ldap_result.get("TokenRefreshCount") == 0,
          repr(ldap_result.get("TokenRefreshCount")))

    with open(ldap_log_path, encoding="utf-8") as fh:
        ldap_log = [json.loads(line) for line in fh if line.strip()]
    check("the explicit-parameter run uses only the four permitted operations",
          [r["operationId"] for r in ldap_log]
          == ["create", "listApplications", "listApplications", "listApplications", "delete"],
          repr([r["operationId"] for r in ldap_log]))
    ldap_create = ldap_log[0]
    check("the LDAP credential body includes exactly the supplied domain value",
          ldap_create["body"].get("domain")
          == {"domain_type": "LDAP", "value": "corp.example"},
          repr(ldap_create["body"].get("domain")))
    check("the LDAP token request remains unauthenticated",
          AUTH_HEADER not in ldap_create["headers"],
          repr(ldap_create["headers"].get(AUTH_HEADER)))
    ldap_lists = [r for r in ldap_log if r["operationId"] == "listApplications"]
    check("the explicit page size is retained on every cursor page",
          [r["query"] for r in ldap_lists] == [
              {"size": ["1"]},
              {"size": ["1"], "cursor": ["MQ=="]},
              {"size": ["1"], "cursor": ["Mg=="]},
          ], repr([r["query"] for r in ldap_lists]))
    check("the explicit-parameter run releases its token last",
          ldap_log[-1]["operationId"] == "delete" and ldap_log[-1]["status"] == 204)


def main():
    workdir = tempfile.mkdtemp(prefix="vcfops-verify-")
    failure = None
    try:
        run(workdir)
    except Failure as exc:
        failure = str(exc)
    except Exception as exc:  # noqa: BLE001 - report anything as a verification failure
        failure = "%s: %s" % (type(exc).__name__, exc)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    for label, ok, detail in CHECKS:
        if not ok:
            print("FAIL  %s" % label)
            if detail:
                print("      %s" % detail)
    print("\n%d/%d checks passed" % (passed, len(CHECKS)))
    if failure:
        print("\nVERIFICATION FAILED: %s" % failure)
        return 1
    print("VERIFICATION PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
