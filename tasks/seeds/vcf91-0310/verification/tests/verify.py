#!/usr/bin/env python3
"""Acceptance test for src/VcfAutomationCatalog.

Everything runs against 127.0.0.1. No live VMware endpoint is contacted: the
only HTTP server involved is mock/vcfa_mock.py, started here on an ephemeral
port, and the only assertions are made against the request log it writes.

    python3 tests/verify.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOCK = os.path.join(ROOT, "mock", "vcfa_mock.py")
CONTRACT = os.path.join(ROOT, "docs", "contract.json")
SRC = os.path.join(ROOT, "src")
MODULE_NAME = "VcfAutomationCatalog"
MODULE_DIR = os.path.join(SRC, MODULE_NAME)
MANIFEST = os.path.join(MODULE_DIR, MODULE_NAME + ".psd1")
ROOT_MODULE = os.path.join(MODULE_DIR, MODULE_NAME + ".psm1")
FUNCTION = "New-VcfAutomationCatalogDeployment"
SDK_MODULE = "VMware.Sdk.Vcf.SddcManager"

REFRESH_TOKEN = "Qw8sT4nP1kJd6yUb3XeMhVzR7fLaC0gO"
ACCESS_TOKEN = "eyJhbGciOiJSUzI1NiJ9.dmVyaWZpZXItYWNjZXNzLXRva2Vu.9Zt2"
TOKEN_TYPE = "Bearer"
AUTHORIZATION = TOKEN_TYPE + " " + ACCESS_TOKEN

PROJECT_ID = "6f2b1c84-0d9e-4a51-9c33-8b7e5a2d1f60"
OTHER_PROJECT_ID = "b18d4e77-3a52-4c0f-8e91-2d6c4b09a7f3"
ITEM_ID = "a4c19e3b-7f28-4d16-9a05-c3e81b6d4207"
ITEM_NAME = "Ubuntu Web Tier"

INPUTS = {
    "hostname": "web-01",
    "cpuCount": 2,
    "memoryGb": 8,
    "tags": {"tier": "web", "env": "prod"},
    "disks": [{"label": "data", "capacityGb": 40}],
}
REASON = "CHG0041288 - capacity uplift for the Q3 campaign"
ITEM_VERSION = "v3.1"

FAILURES = []


def fail(scenario, message):
    FAILURES.append("[%s] %s" % (scenario, message))


def check(scenario, condition, message):
    if not condition:
        fail(scenario, message)
    return bool(condition)


def base_state(faults=None, deployments=None):
    return {
        "session": {
            "refreshToken": REFRESH_TOKEN,
            "accessToken": ACCESS_TOKEN,
            "tokenType": TOKEN_TYPE,
            "user": "svc-automation@vsphere.local",
        },
        "projects": [
            {"id": PROJECT_ID, "name": "Platform Engineering"},
            {"id": OTHER_PROJECT_ID, "name": "Payments"},
        ],
        "catalogItems": [
            {"id": ITEM_ID, "name": ITEM_NAME, "version": ITEM_VERSION},
            {"id": "d92f5a10-6c47-4b83-a1de-58f027c9b4e6", "name": "Postgres Cluster",
             "version": "v2.0"},
        ],
        "deployments": deployments or [],
        "faults": faults or [],
    }


# ---------------------------------------------------------------- stand-in


class Standin:
    """Runs mock/vcfa_mock.py on an ephemeral loopback port."""

    def __init__(self, workdir, tag, state):
        self.dir = os.path.join(workdir, tag)
        os.makedirs(self.dir, exist_ok=True)
        self.state_in = os.path.join(self.dir, "state-in.json")
        self.state_out = os.path.join(self.dir, "state-out.json")
        self.log_path = os.path.join(self.dir, "requests.jsonl")
        with open(self.state_in, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
        self.proc = None
        self.base_url = None

    def __enter__(self):
        self.proc = subprocess.Popen(
            [sys.executable, MOCK,
             "--port", "0",
             "--contract", CONTRACT,
             "--state", self.state_in,
             "--state-out", self.state_out,
             "--log", self.log_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        line = self.proc.stdout.readline().strip()
        if not line.startswith("LISTENING "):
            err = self.proc.stderr.read()
            raise RuntimeError("stand-in did not start: %r %s" % (line, err))
        self.base_url = "http://127.0.0.1:%s" % line.split()[1]
        return self

    def __exit__(self, *exc):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        return False

    def log(self):
        if not os.path.exists(self.log_path):
            return []
        with open(self.log_path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def state(self):
        with open(self.state_out, encoding="utf-8") as fh:
            return json.load(fh)


# ------------------------------------------------------------- powershell


DRIVER = r"""
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $Manifest,
    [Parameter(Mandatory = $true)] [string] $ScenarioPath,
    [Parameter(Mandatory = $true)] [string] $ResultPath
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$calls = [System.Collections.Generic.List[object]]::new()
$importError = $null

try {
    Import-Module -Name $Manifest -Force -ErrorAction Stop -WarningAction SilentlyContinue
} catch {
    $importError = $_.Exception.Message
}

if ($null -eq $importError) {
    $scenario = Get-Content -Raw -LiteralPath $ScenarioPath | ConvertFrom-Json -AsHashtable
    foreach ($call in $scenario.calls) {
        $splat = @{
            ApiUri         = $scenario.apiUri
            RefreshToken   = $scenario.refreshToken
            CatalogItemId  = $call.catalogItemId
            DeploymentName = $call.deploymentName
            ProjectId      = $call.projectId
        }
        foreach ($optional in 'Inputs', 'Reason', 'CatalogItemVersion') {
            $key = $optional.Substring(0, 1).ToLowerInvariant() + $optional.Substring(1)
            if ($call.ContainsKey($key) -and $null -ne $call[$key]) {
                $splat[$optional] = $call[$key]
            }
        }

        $entry = [ordered]@{ ok = $false; error = $null; result = $null; outputCount = 0 }
        try {
            $returned = @(& $scenario.functionName @splat)
            $entry.outputCount = $returned.Count
            if ($returned.Count -eq 1) {
                $r = $returned[0]
                $propertyNames = @($r.PSObject.Properties.Name)
                $expectedNames = @(
                    'DeploymentId', 'DeploymentName', 'Created', 'LookupCount'
                )
                $unknownNames = @($propertyNames | Where-Object { $_ -notin $expectedNames })
                $missingNames = @($expectedNames | Where-Object { $_ -notin $propertyNames })
                if ($unknownNames.Count -eq 0 -and $missingNames.Count -eq 0 -and
                    $propertyNames.Count -eq $expectedNames.Count) {
                    $entry.ok = $true
                    $entry.result = [ordered]@{
                        DeploymentId   = [string]$r.DeploymentId
                        DeploymentName = [string]$r.DeploymentName
                        Created        = [bool]$r.Created
                        LookupCount    = [int]$r.LookupCount
                    }
                } else {
                    $entry.error = "result properties were [$($propertyNames -join ', ')]"
                }
            } else {
                $entry.error = "expected exactly one output object, got $($returned.Count)"
            }
        } catch {
            $entry.error = $_.Exception.Message
        }
        $calls.Add($entry)
    }
}

$payload = [ordered]@{ importError = $importError; calls = $calls }
$payload | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $ResultPath -Encoding utf8
"""

PROBE = r"""
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $Manifest,
    [Parameter(Mandatory = $true)] [string] $ResultPath
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$o = [ordered]@{}

try {
    $mi = Test-ModuleManifest -Path $Manifest -ErrorAction Stop
    $o.manifestOk = $true
    $o.requiredModules = @($mi.RequiredModules | ForEach-Object { $_.Name })
    $o.rootModule = [string]$mi.RootModule
} catch {
    $o.manifestOk = $false
    $o.manifestError = $_.Exception.Message
}

try {
    Import-Module -Name $Manifest -Force -ErrorAction Stop -WarningAction SilentlyContinue
    $o.importOk = $true
    $name = [System.IO.Path]::GetFileNameWithoutExtension($Manifest)
    $o.exported = @(Get-Command -Module $name -CommandType Function, Cmdlet |
                        ForEach-Object { $_.Name })
    $o.loadedSdkModules = @(Get-Module -Name 'VMware.Sdk.Vcf*' | ForEach-Object { $_.Name })
} catch {
    $o.importOk = $false
    $o.importError = $_.Exception.Message
}

$o | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ResultPath -Encoding utf8
"""


def run_pwsh(script_body, args, workdir, tag):
    script = os.path.join(workdir, tag + ".ps1")
    result = os.path.join(workdir, tag + ".result.json")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(script_body)
    completed = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-File", script] + args + [result],
        capture_output=True, text=True, timeout=600,
    )
    if not os.path.exists(result):
        raise RuntimeError(
            "pwsh produced no result for %s\nstdout:\n%s\nstderr:\n%s"
            % (tag, completed.stdout, completed.stderr)
        )
    with open(result, encoding="utf-8") as fh:
        return json.load(fh)


def run_scenario(workdir, tag, state, calls):
    with Standin(workdir, tag, state) as standin:
        scenario = {
            "apiUri": standin.base_url,
            "refreshToken": REFRESH_TOKEN,
            "functionName": FUNCTION,
            "calls": calls,
        }
        scenario_path = os.path.join(standin.dir, "scenario.json")
        with open(scenario_path, "w", encoding="utf-8") as fh:
            json.dump(scenario, fh, indent=2)
        outcome = run_pwsh(DRIVER, [MANIFEST, scenario_path], workdir, tag)
        log = standin.log()
        state_out = standin.state()
        base_url = standin.base_url
    return outcome, log, state_out, base_url


# ------------------------------------------------------------- assertions


def empty_values(node, path="body"):
    """Paths of every null, empty string, empty array and empty object."""
    found = []
    if node is None:
        found.append(path)
    elif isinstance(node, str):
        if node == "":
            found.append(path)
    elif isinstance(node, list):
        if not node:
            found.append(path)
        for index, item in enumerate(node):
            found.extend(empty_values(item, "%s[%d]" % (path, index)))
    elif isinstance(node, dict):
        if not node:
            found.append(path)
        for key, value in node.items():
            found.extend(empty_values(value, "%s.%s" % (path, key)))
    return found


def ops(log):
    return [e["operation"] for e in log]


def entries(log, operation):
    return [e for e in log if e["operation"] == operation]


def single_values(query_params):
    out = {}
    for key, values in query_params.items():
        out[key] = values[0] if len(values) == 1 else values
    return out


def assert_common_wire(scenario, log, allowed_statuses=(200, 503)):
    """Checks that hold for every run of the module."""
    for entry in log:
        if entry["operation"] is None:
            fail(scenario, "module called an operation the contract does not name: %s %s"
                 % (entry["method"], entry["path"]))
        if entry["status"] not in allowed_statuses:
            fail(scenario, "stand-in refused %s %s with %s: %s"
                 % (entry["method"], entry["path"], entry["status"],
                    (entry.get("bodyJson") or {})))
        host = entry["headers"].get("Host", "")
        if not host.startswith("127.0.0.1"):
            fail(scenario, "request went to %r, which is not loopback" % host)

    for entry in entries(log, "retrieveAuthToken"):
        if "Authorization" in entry["headers"]:
            fail(scenario, "the token-minting call carried an Authorization header")
        if entry["bodyJson"] != {"refreshToken": REFRESH_TOKEN}:
            fail(scenario, "retrieveAuthToken body was %r, expected exactly "
                           "{'refreshToken': ...}" % (entry["bodyJson"],))

    for name in ("getDeployments", "requestCatalogItemInstances"):
        for entry in entries(log, name):
            got = entry["headers"].get("Authorization")
            if got != AUTHORIZATION:
                fail(scenario, "%s sent Authorization %r, expected %r"
                     % (name, got, AUTHORIZATION))

    for entry in entries(log, "requestCatalogItemInstances"):
        ctype = entry["headers"].get("Content-Type", "")
        if not ctype.startswith("application/json"):
            fail(scenario, "catalog request Content-Type was %r" % ctype)


def assert_lookup(scenario, entry, deployment_name):
    got = single_values(entry["queryParams"])
    expected = {"name": deployment_name, "projects": PROJECT_ID}
    if got != expected:
        fail(scenario, "deployment lookup query was %r, expected exactly %r" % (got, expected))


# -------------------------------------------------------------- scenarios


def scenario_create(workdir):
    name = "create"
    deployment_name = "web-tier-prod-01"
    calls = [{
        "catalogItemId": ITEM_ID,
        "deploymentName": deployment_name,
        "projectId": PROJECT_ID,
        "inputs": INPUTS,
        "reason": REASON,
        "catalogItemVersion": ITEM_VERSION,
    }]
    outcome, log, state_out, _ = run_scenario(workdir, name, base_state(), calls)

    if outcome["importError"]:
        fail(name, "Import-Module failed: %s" % outcome["importError"])
        return
    call = outcome["calls"][0]
    if not check(name, call["ok"], "call failed: %s" % call["error"]):
        return

    assert_common_wire(name, log)
    check(name, ops(log) == ["retrieveAuthToken", "getDeployments",
                             "requestCatalogItemInstances"],
          "request sequence was %r" % (ops(log),))

    lookups = entries(log, "getDeployments")
    if check(name, len(lookups) == 1, "expected 1 deployment lookup, saw %d" % len(lookups)):
        assert_lookup(name, lookups[0], deployment_name)

    posts = entries(log, "requestCatalogItemInstances")
    if check(name, len(posts) == 1, "expected 1 catalog request, saw %d" % len(posts)):
        post = posts[0]
        check(name, post["path"] == "/catalog/api/items/%s/request" % ITEM_ID,
              "catalog request path was %r" % post["path"])
        check(name, post["queryParams"] == {},
              "catalog request carried query parameters %r" % post["queryParams"])
        body = post["bodyJson"]
        expected = {
            "deploymentName": deployment_name,
            "projectId": PROJECT_ID,
            "inputs": INPUTS,
            "reason": REASON,
            "version": ITEM_VERSION,
        }
        check(name, sorted(body or {}) == sorted(expected),
              "catalog request body keys were %r, expected %r"
              % (sorted(body or {}), sorted(expected)))
        check(name, body == expected, "catalog request body was %r, expected %r"
              % (body, expected))

    result = call["result"]
    check(name, result["Created"] is True, "Created was %r, expected True" % result["Created"])
    check(name, result["LookupCount"] == 1,
          "LookupCount was %r, expected 1" % result["LookupCount"])
    check(name, result["DeploymentName"] == deployment_name,
          "DeploymentName was %r" % result["DeploymentName"])

    stored = state_out["deployments"]
    if check(name, len(stored) == 1, "stand-in holds %d deployments, expected 1" % len(stored)):
        check(name, result["DeploymentId"] == stored[0]["id"],
              "DeploymentId %r does not match the deployment the appliance created (%r)"
              % (result["DeploymentId"], stored[0]["id"]))


def scenario_rerun(workdir):
    name = "rerun"
    deployment_name = "web-tier-prod-02"
    call = {
        "catalogItemId": ITEM_ID,
        "deploymentName": deployment_name,
        "projectId": PROJECT_ID,
        "inputs": INPUTS,
        "reason": REASON,
        "catalogItemVersion": ITEM_VERSION,
    }
    outcome, log, state_out, _ = run_scenario(
        workdir, name, base_state(), [dict(call), dict(call)])

    if outcome["importError"]:
        fail(name, "Import-Module failed: %s" % outcome["importError"])
        return
    for index, entry in enumerate(outcome["calls"]):
        if not check(name, entry["ok"], "call %d failed: %s" % (index + 1, entry["error"])):
            return

    assert_common_wire(name, log)
    posts = entries(log, "requestCatalogItemInstances")
    check(name, len(posts) == 1,
          "two identical runs produced %d catalog requests; the second run must not "
          "submit one" % len(posts))
    check(name, len(entries(log, "getDeployments")) == 2,
          "expected one lookup per run, saw %d" % len(entries(log, "getDeployments")))

    first, second = outcome["calls"][0]["result"], outcome["calls"][1]["result"]
    check(name, first["Created"] is True, "first run reported Created=%r" % first["Created"])
    check(name, second["Created"] is False,
          "second run reported Created=%r, expected False" % second["Created"])
    check(name, second["LookupCount"] == 1,
          "second run LookupCount was %r, expected 1" % second["LookupCount"])
    check(name, first["DeploymentId"] == second["DeploymentId"] != "",
          "second run returned deployment %r, expected the existing %r"
          % (second["DeploymentId"], first["DeploymentId"]))

    stored = state_out["deployments"]
    check(name, len(stored) == 1,
          "stand-in holds %d deployments after two identical runs, expected 1" % len(stored))


def scenario_minimal(workdir):
    name = "minimal"
    deployment_name = "web-tier-lab-01"
    calls = [{
        "catalogItemId": ITEM_ID,
        "deploymentName": deployment_name,
        "projectId": PROJECT_ID,
    }]
    outcome, log, state_out, _ = run_scenario(workdir, name, base_state(), calls)

    if outcome["importError"]:
        fail(name, "Import-Module failed: %s" % outcome["importError"])
        return
    call = outcome["calls"][0]
    if not check(name, call["ok"], "call failed: %s" % call["error"]):
        return

    assert_common_wire(name, log)
    posts = entries(log, "requestCatalogItemInstances")
    if not check(name, len(posts) == 1, "expected 1 catalog request, saw %d" % len(posts)):
        return

    body = posts[0]["bodyJson"]
    expected = {"deploymentName": deployment_name, "projectId": PROJECT_ID}
    check(name, body == expected,
          "with no optional arguments the catalog request body was %r, expected exactly %r. "
          "An optional field with no value is left out of the JSON, never sent as null, "
          "\"\", [] or {}." % (body, expected))
    stray = empty_values(body)
    check(name, not stray, "catalog request body carried empty value(s) at %r" % stray)

    for entry in entries(log, "getDeployments"):
        assert_lookup(name, entry, deployment_name)

    check(name, call["result"]["Created"] is True,
          "Created was %r, expected True" % call["result"]["Created"])
    check(name, len(state_out["deployments"]) == 1,
          "stand-in holds %d deployments, expected 1" % len(state_out["deployments"]))


def scenario_lost_response(workdir):
    """The appliance creates the deployment and then fails to answer."""
    name = "lost-response"
    deployment_name = "web-tier-prod-03"
    state = base_state(faults=[{
        "operation": "requestCatalogItemInstances",
        "catalogItemId": ITEM_ID,
        "mode": "commit-then-503",
        "times": 1,
    }])
    calls = [{
        "catalogItemId": ITEM_ID,
        "deploymentName": deployment_name,
        "projectId": PROJECT_ID,
        "inputs": INPUTS,
    }]
    outcome, log, state_out, _ = run_scenario(workdir, name, state, calls)

    if outcome["importError"]:
        fail(name, "Import-Module failed: %s" % outcome["importError"])
        return
    call = outcome["calls"][0]
    if not check(name, call["ok"],
                 "the run must recover from the failed catalog request, but it threw: %s"
                 % call["error"]):
        return

    assert_common_wire(name, log)
    posts = entries(log, "requestCatalogItemInstances")
    check(name, len(posts) == 1,
          "the catalog request was sent %d times; the deployment already existed, so "
          "resending it duplicates the deployment" % len(posts))
    check(name, len(entries(log, "getDeployments")) == 2,
          "expected the lookup to run twice (once before the request, once after it "
          "failed), saw %d" % len(entries(log, "getDeployments")))

    result = call["result"]
    check(name, result["LookupCount"] == 2,
          "LookupCount was %r, expected 2" % result["LookupCount"])
    check(name, result["Created"] is True,
          "Created was %r; the first lookup found nothing, so this run's request is what "
          "created the deployment" % result["Created"])

    stored = state_out["deployments"]
    if check(name, len(stored) == 1,
             "stand-in holds %d deployments, expected exactly 1" % len(stored)):
        check(name, stored[0]["name"] == deployment_name,
              "stored deployment is named %r" % stored[0]["name"])
        check(name, result["DeploymentId"] == stored[0]["id"],
              "DeploymentId %r does not match the deployment that survived (%r)"
              % (result["DeploymentId"], stored[0]["id"]))


def scenario_clean_failure(workdir):
    """The appliance refuses the request without creating anything."""
    name = "clean-failure"
    deployment_name = "web-tier-prod-04"
    state = base_state(faults=[{
        "operation": "requestCatalogItemInstances",
        "catalogItemId": ITEM_ID,
        "mode": "fail-before-commit",
        "times": 1,
    }])
    calls = [{
        "catalogItemId": ITEM_ID,
        "deploymentName": deployment_name,
        "projectId": PROJECT_ID,
        "reason": REASON,
    }]
    outcome, log, state_out, _ = run_scenario(workdir, name, state, calls)

    if outcome["importError"]:
        fail(name, "Import-Module failed: %s" % outcome["importError"])
        return
    call = outcome["calls"][0]
    if not check(name, call["ok"],
                 "nothing was created, so the run must resend and succeed, but it threw: %s"
                 % call["error"]):
        return

    assert_common_wire(name, log)
    posts = entries(log, "requestCatalogItemInstances")
    check(name, len(posts) == 2,
          "expected the catalog request to be sent twice (the first attempt left nothing "
          "behind), saw %d" % len(posts))
    if len(posts) == 2:
        check(name, posts[0]["bodyJson"] == posts[1]["bodyJson"],
              "the resent catalog request body differed from the first: %r vs %r"
              % (posts[0]["bodyJson"], posts[1]["bodyJson"]))
        check(name, posts[0]["bodyRaw"] == posts[1]["bodyRaw"],
              "the resent catalog request was not byte-for-byte identical to the first")
    check(name, len(entries(log, "getDeployments")) == 2,
          "expected 2 lookups, saw %d" % len(entries(log, "getDeployments")))
    check(name, len(entries(log, "retrieveAuthToken")) == 1,
          "expected the access token to be minted once per run, saw %d"
          % len(entries(log, "retrieveAuthToken")))

    result = call["result"]
    check(name, result["Created"] is True, "Created was %r, expected True" % result["Created"])
    check(name, result["LookupCount"] == 2,
          "LookupCount was %r, expected 2" % result["LookupCount"])
    check(name, len(state_out["deployments"]) == 1,
          "stand-in holds %d deployments, expected exactly 1"
          % len(state_out["deployments"]))


def scenario_gives_up(workdir):
    """Two failures in a row: one resend is the limit."""
    name = "gives-up"
    deployment_name = "web-tier-prod-05"
    state = base_state(faults=[{
        "operation": "requestCatalogItemInstances",
        "catalogItemId": ITEM_ID,
        "mode": "fail-before-commit",
        "times": 2,
    }])
    calls = [{
        "catalogItemId": ITEM_ID,
        "deploymentName": deployment_name,
        "projectId": PROJECT_ID,
    }]
    outcome, log, state_out, _ = run_scenario(workdir, name, state, calls)

    if outcome["importError"]:
        fail(name, "Import-Module failed: %s" % outcome["importError"])
        return

    assert_common_wire(name, log)
    call = outcome["calls"][0]
    check(name, not call["ok"] and call["error"],
          "the appliance refused the request twice, so the run must fail loudly; it "
          "returned %r instead" % (call["result"],))
    posts = entries(log, "requestCatalogItemInstances")
    check(name, len(posts) == 2,
          "expected the catalog request to be sent at most twice, saw %d" % len(posts))
    check(name, len(entries(log, "getDeployments")) == 2,
          "the failed resend must be thrown, not followed by another lookup; saw %d lookups"
          % len(entries(log, "getDeployments")))
    check(name, len(state_out["deployments"]) == 0,
          "stand-in holds %d deployments, expected none"
          % len(state_out["deployments"]))


def scenario_no_response(workdir):
    """A transport failure has no HTTP response, so its outcome is unknown."""
    name = "no-response"
    deployment_name = "web-tier-prod-06"
    state = base_state(faults=[{
        "operation": "requestCatalogItemInstances",
        "catalogItemId": ITEM_ID,
        "mode": "disconnect-before-commit",
        "times": 1,
    }])
    calls = [{
        "catalogItemId": ITEM_ID,
        "deploymentName": deployment_name,
        "projectId": PROJECT_ID,
        "inputs": INPUTS,
    }]
    outcome, log, state_out, _ = run_scenario(workdir, name, state, calls)

    if outcome["importError"]:
        fail(name, "Import-Module failed: %s" % outcome["importError"])
        return
    call = outcome["calls"][0]
    if not check(name, call["ok"],
                 "the response-less failure left nothing behind, so the run must "
                 "look up and resend once: %s" % call["error"]):
        return

    assert_common_wire(name, log, allowed_statuses=(200, None))
    check(name, ops(log) == ["retrieveAuthToken", "getDeployments",
                             "requestCatalogItemInstances", "getDeployments",
                             "requestCatalogItemInstances"],
          "request sequence was %r" % (ops(log),))
    posts = entries(log, "requestCatalogItemInstances")
    if check(name, len(posts) == 2, "expected one resend, saw %d posts" % len(posts)):
        check(name, posts[0]["bodyRaw"] == posts[1]["bodyRaw"],
              "the response-less request was not resent byte for byte")
    result = call["result"]
    check(name, result["Created"] is True, "Created was %r" % result["Created"])
    check(name, result["LookupCount"] == 2,
          "LookupCount was %r, expected 2" % result["LookupCount"])
    check(name, len(state_out["deployments"]) == 1,
          "stand-in holds %d deployments, expected 1" % len(state_out["deployments"]))


def scenario_4xx(workdir):
    """An answered 4xx is definitive and must never be retried."""
    name = "answered-4xx"
    deployment_name = "web-tier-prod-07"
    missing_item_id = "00000000-0000-4000-8000-000000000404"
    calls = [{
        "catalogItemId": missing_item_id,
        "deploymentName": deployment_name,
        "projectId": PROJECT_ID,
    }]
    outcome, log, state_out, _ = run_scenario(workdir, name, base_state(), calls)

    if outcome["importError"]:
        fail(name, "Import-Module failed: %s" % outcome["importError"])
        return
    call = outcome["calls"][0]
    check(name, not call["ok"] and call["error"],
          "the missing catalog item returned 404, but the call did not throw")
    assert_common_wire(name, log, allowed_statuses=(200, 404))
    check(name, ops(log) == ["retrieveAuthToken", "getDeployments",
                             "requestCatalogItemInstances"],
          "a 4xx must not trigger a lookup or resend; sequence was %r" % (ops(log),))
    check(name, len(state_out["deployments"]) == 0,
          "stand-in holds %d deployments, expected none" % len(state_out["deployments"]))


def scenario_bound_empty_optionals(workdir):
    """Explicitly bound optional values with no content are still omitted."""
    name = "bound-empty-optionals"
    deployment_name = "web-tier-lab-02"
    calls = [{
        "catalogItemId": ITEM_ID,
        "deploymentName": deployment_name,
        "projectId": PROJECT_ID,
        "inputs": {},
        "reason": "",
        "catalogItemVersion": "",
    }]
    outcome, log, _, _ = run_scenario(workdir, name, base_state(), calls)

    if outcome["importError"]:
        fail(name, "Import-Module failed: %s" % outcome["importError"])
        return
    call = outcome["calls"][0]
    if not check(name, call["ok"], "call failed: %s" % call["error"]):
        return
    assert_common_wire(name, log)
    posts = entries(log, "requestCatalogItemInstances")
    if check(name, len(posts) == 1, "expected 1 catalog request, saw %d" % len(posts)):
        expected = {"deploymentName": deployment_name, "projectId": PROJECT_ID}
        check(name, posts[0]["bodyJson"] == expected,
              "bound empty optional values were serialized: %r" % posts[0]["bodyJson"])


def scenario_contract_pinning(workdir):
    """The stand-in answers only what the contract names."""
    name = "contract-pinning"
    with open(CONTRACT, encoding="utf-8") as fh:
        contract = json.load(fh)

    with Standin(workdir, name, base_state()) as standin:
        def call(method, path, body=None, headers=None):
            request = urllib.request.Request(
                standin.base_url + path, method=method,
                data=json.dumps(body).encode() if body is not None else None,
                headers=headers or {},
            )
            try:
                with urllib.request.urlopen(request, timeout=15) as response:
                    return response.status
            except urllib.error.HTTPError as exc:
                return exc.code
            except urllib.error.URLError as exc:
                return "URLError: %s" % exc

        for excluded in contract["excludedOperations"]:
            path = excluded["path"].replace(
                "{deploymentId}", "8a1f6d20-4b7c-4e35-9f18-0c2a7d5e3b91")
            status = call(excluded["method"], path,
                          body={} if excluded["method"] == "POST" else None,
                          headers={"Authorization": AUTHORIZATION,
                                   "Content-Type": "application/json"})
            check(name, status == 404,
                  "%s %s is not on the contract but the stand-in answered %s"
                  % (excluded["method"], path, status))

        status = call("GET", "/deployment/api/deployments?name=x&expandResources=true"
                             "&notAContractParameter=1",
                      headers={"Authorization": AUTHORIZATION})
        check(name, status == 400,
              "a query parameter the contract does not declare was answered with %s, "
              "expected 400" % status)

        status = call("POST", "/catalog/api/items/%s/request" % ITEM_ID,
                      body={"deploymentName": "x", "projectId": PROJECT_ID,
                            "notAContractProperty": True},
                      headers={"Authorization": AUTHORIZATION,
                               "Content-Type": "application/json"})
        check(name, status == 400,
              "a body property the contract does not declare was answered with %s, "
              "expected 400" % status)

        status = call("POST", "/iaas/api/login", body={"refreshToken": REFRESH_TOKEN})
        check(name, status == 200, "retrieveAuthToken answered %s" % status)


def scenario_packaging(workdir):
    name = "packaging"
    if not check(name, os.path.isfile(MANIFEST), "missing %s" % MANIFEST):
        return
    if not check(name, os.path.isfile(ROOT_MODULE), "missing %s" % ROOT_MODULE):
        return

    vendored = []
    for dirpath, dirnames, filenames in os.walk(SRC):
        for entry in list(dirnames) + filenames:
            lowered = entry.lower()
            if lowered.startswith("vmware.") or lowered.startswith("vmware_"):
                vendored.append(os.path.relpath(os.path.join(dirpath, entry), ROOT))
            elif lowered.endswith((".dll", ".nupkg", ".zip")):
                vendored.append(os.path.relpath(os.path.join(dirpath, entry), ROOT))
    check(name, not vendored,
          "the VCF PowerCLI SDK is a prerequisite, not something to vendor; found %r"
          % vendored)

    probe = run_pwsh(PROBE, [MANIFEST], workdir, "packaging-probe")
    if not check(name, probe.get("manifestOk"),
                 "Test-ModuleManifest failed: %s" % probe.get("manifestError")):
        return
    required = probe.get("requiredModules") or []
    check(name, SDK_MODULE in required,
          "the manifest's RequiredModules is %r; it must declare %r"
          % (required, SDK_MODULE))
    if not check(name, probe.get("importOk"),
                 "Import-Module failed: %s" % probe.get("importError")):
        return
    exported = probe.get("exported") or []
    check(name, exported == [FUNCTION],
          "the module exports %r; it must export exactly [%r]" % (exported, FUNCTION))
    check(name, SDK_MODULE in (probe.get("loadedSdkModules") or []),
          "importing the module did not pull in %s, so the declared dependency is not "
          "load-bearing (loaded: %r)" % (SDK_MODULE, probe.get("loadedSdkModules")))


# ------------------------------------------------------------------- main


def main():
    if shutil.which("pwsh") is None:
        print("FAIL: pwsh is not on PATH; run scripts/setup.sh first", file=sys.stderr)
        return 1

    workdir = tempfile.mkdtemp(prefix="vcfa-verify-")
    try:
        scenario_packaging(workdir)
        if any(f.startswith("[packaging]") for f in FAILURES):
            print_report()
            return 1

        scenario_create(workdir)
        scenario_rerun(workdir)
        scenario_minimal(workdir)
        scenario_lost_response(workdir)
        scenario_clean_failure(workdir)
        scenario_gives_up(workdir)
        scenario_no_response(workdir)
        scenario_4xx(workdir)
        scenario_bound_empty_optionals(workdir)
        scenario_contract_pinning(workdir)
    finally:
        if not FAILURES:
            shutil.rmtree(workdir, ignore_errors=True)
        else:
            print("artefacts kept in %s\n" % workdir, file=sys.stderr)

    print_report()
    return 1 if FAILURES else 0


def print_report():
    if FAILURES:
        print("FAIL (%d)" % len(FAILURES))
        for failure in FAILURES:
            print("  - %s" % failure)
    else:
        print("PASS")


if __name__ == "__main__":
    sys.exit(main())
