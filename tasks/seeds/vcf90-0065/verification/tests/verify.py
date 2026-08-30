#!/usr/bin/env python3
"""Protected end-to-end verifier for the VCF Operations outbound notification module."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from support.vcf_ops_mock import (
    EXPECTED_OPERATIONS,
    KNOWN_TEMPLATE_ID,
    OFF_CONTRACT_PROBE,
    OPS_TOKEN,
    PLUGIN_IDS,
    RULE_IDS,
    SEED_PASSWORD,
    SEED_USER,
    ContractPinnedVcfOps,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "contract.json"
SOURCES = ROOT / "docs" / "official_sources.json"
MANIFEST = ROOT / "src" / "VcfOps.OutboundNotification" / "VcfOps.OutboundNotification.psd1"
MODULE = ROOT / "src" / "VcfOps.OutboundNotification" / "VcfOps.OutboundNotification.psm1"
RUNNER = ROOT / "tests" / "invoke_submission.ps1"

COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
SPEC_PATH = "specifications/vcf-operations/vcf-operations-openapi.json"
SPEC_TAG = "9.0.0.0"
BASE = "/suite-api"
# Added by the 9.1.0.0 revision of the same specification file.
NINE_ONE_ONLY_OPERATION = "exchangeOpsTokenWithJwtToken"

TEMPLATE_REASON = "notification rule requires an existing notification template"


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


# --------------------------------------------------------------------------
# contract provenance
# --------------------------------------------------------------------------


def verify_contract() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    source = contract["source"]

    require(source["commit"] == COMMIT, "contract commit was changed")
    require(source["path"] == SPEC_PATH, "contract spec path was changed")
    require(source["tag"] == SPEC_TAG, "contract is not pinned to tag 9.0.0.0")
    require(source["apiVersion"] == SPEC_TAG, "contract apiVersion is not 9.0.0.0")
    require(source["basePath"] == BASE, "contract base path is not /suite-api")
    # The 9.0.0.0 revision leaves info.version empty; 9.1.0.0 sets it to 9.1.0.0.
    require(
        source["specInfoVersion"] == "",
        "contract was derived from a revision whose info.version is set, i.e. not 9.0.0.0",
    )

    operations = {
        operation_id: (value["method"], value["path"])
        for operation_id, value in contract["operations"].items()
    }
    require(operations == EXPECTED_OPERATIONS, "contract operation set was changed")
    require(
        NINE_ONE_ONLY_OPERATION not in contract["operations"],
        f"{NINE_ONE_ONLY_OPERATION} only exists from 9.1.0.0; the contract must be 9.0.0.0",
    )

    source_operations = {
        item["operationId"]: (item["method"], item["path"])
        for item in sources["operationIds"]
    }
    require(source_operations == EXPECTED_OPERATIONS, "official source operations do not match")
    require(sources["commit"] == COMMIT, "official source commit was changed")
    require(sources["specPath"] == SPEC_PATH, "official source path was changed")
    require(sources["tag"] == SPEC_TAG, "official source tag was changed")
    require(sources["repositoryLicense"] == "Apache-2.0", "source license is not recorded")
    require(
        sources["repository"] == "https://github.com/vmware/vcf-api-specs",
        "official repository was changed",
    )
    require(
        sources["specUrl"]
        == f"https://raw.githubusercontent.com/vmware/vcf-api-specs/{COMMIT}/{SPEC_PATH}",
        "official source URL is not pinned to the recorded commit",
    )
    require(
        {item["jsonPointer"] for item in sources["operationIds"]}
        == {
            "#/paths/~1api~1auth~1token~1acquire/post",
            "#/paths/~1api~1auth~1token~1release/post",
            "#/paths/~1api~1versions~1current/get",
            "#/paths/~1api~1alertplugins/post",
            "#/paths/~1api~1alertplugins/put",
            "#/paths/~1api~1notifications~1rules/post",
        },
        "official source JSON pointers were changed",
    )

    plugin = contract["schemas"]["notification-plugin"]
    require(
        plugin["required"] == ["name", "pluginTypeId"],
        "notification-plugin required set no longer matches the pinned specification",
    )
    require(
        set(plugin["optional"])
        == {"pluginId", "description", "enabled", "configValues", "certificates"},
        "notification-plugin optional set no longer matches the pinned specification",
    )
    rule = contract["schemas"]["notification-rule"]
    require(
        rule["required"] == ["name", "pluginId"],
        "notification-rule required set no longer matches the pinned specification",
    )
    for field in ("templateId", "criticalities", "alertStatuses", "resourceFilters"):
        require(field in rule["optional"], f"notification-rule.{field} must be optional")


# --------------------------------------------------------------------------
# submission shape
# --------------------------------------------------------------------------


def verify_submission_shape() -> None:
    manifest_text = MANIFEST.read_text(encoding="utf-8")
    require(
        re.search(r"RequiredModules[\s\S]*VMware\.Sdk\.Vcf\.Ops", manifest_text, re.I) is not None,
        "manifest must require VMware.Sdk.Vcf.Ops",
    )

    environment = os.environ.copy()
    environment.update(
        {"MOONSHINER_SOURCE_PATH": str(MODULE), "POWERSHELL_TELEMETRY_OPTOUT": "1"}
    )
    inspection_script = r"""
$parseErrors = $null
$tokens = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    $env:MOONSHINER_SOURCE_PATH,
    [ref] $tokens,
    [ref] $parseErrors
)
$commands = @(
    $ast.FindAll(
        { param($node) $node -is [Management.Automation.Language.CommandAst] },
        $true
    ) | ForEach-Object { $_.GetCommandName() } | Where-Object { $_ }
)
$types = @(
    $ast.FindAll(
        { param($node) $node -is [Management.Automation.Language.TypeExpressionAst] },
        $true
    ) | ForEach-Object { $_.TypeName.FullName }
)
$strings = @(
    $ast.FindAll(
        { param($node) $node -is [Management.Automation.Language.StringConstantExpressionAst] },
        $true
    ) | ForEach-Object { $_.Value }
)
$functionNames = @(
    $ast.FindAll(
        { param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] },
        $true
    ) | ForEach-Object { $_.Name }
)
$result = [ordered]@{
    commands = $commands
    types = $types
    strings = $strings
    functionNames = $functionNames
    errorCount = @($parseErrors).Count
}
ConvertTo-Json -InputObject $result -Compress
"""
    inspection = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", inspection_script],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    require(
        inspection.returncode == 0,
        f"could not inspect PowerShell syntax: {inspection.stderr[-500:]}",
    )
    lines = [line for line in inspection.stdout.splitlines() if line.strip()]
    require(lines, "PowerShell syntax inspection returned no result")
    shape = json.loads(lines[-1])
    require(shape["errorCount"] == 0, "submission module has PowerShell parse errors")

    def values(name: str) -> list[str]:
        item = shape[name]
        return [item] if isinstance(item, str) else item

    commands = {name.casefold() for name in values("commands")}
    required_commands = (
        "invoke-vcfopscreatealertplugin",
        "invoke-vcfopsupdatealertplugin",
        "invoke-vcfopscreatenotificationpluginrule",
    )
    for required_command in required_commands:
        require(
            required_command in commands,
            f"implementation must drive the SDK cmdlet {required_command}",
        )
    function_names = {name.casefold() for name in values("functionNames")}
    require(
        not (function_names & set(required_commands)),
        "implementation must call the SDK cmdlets, not define replacements for them",
    )

    banned_commands = {
        "invoke-restmethod", "invoke-webrequest", "curl", "curl.exe", "wget", "wget.exe",
    }
    found = sorted(commands & banned_commands)
    types = {name.casefold() for name in values("types")}
    banned_type_suffixes = (
        "webclient", "httpclient", "httpmessageinvoker", "httpmessagehandler",
        "socketshttphandler", "webrequesthandler", "httpwebrequest", "webrequest",
        "tcpclient", "udpclient", "socket",
    )
    found += sorted(name for name in types if name.endswith(banned_type_suffixes))
    strings = {value.casefold() for value in values("strings")}
    found += sorted(value for value in strings if value.endswith(banned_type_suffixes))
    found += sorted(strings & {"curl", "curl.exe", "wget", "wget.exe"})
    require(not found, f"direct HTTP client is not allowed: {', '.join(found)}")


# --------------------------------------------------------------------------
# execution
# --------------------------------------------------------------------------


def create_certificate(directory: Path) -> tuple[Path, Path]:
    cert = directory / "loopback-cert.pem"
    key = directory / "loopback-key.pem"
    command = [
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes",
        "-days", "1", "-subj", "/CN=127.0.0.1", "-addext",
        "subjectAltName=IP:127.0.0.1", "-addext",
        "basicConstraints=critical,CA:TRUE", "-keyout", str(key), "-out", str(cert),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    require(
        result.returncode == 0,
        f"could not create loopback TLS certificate: {result.stderr[-400:]}",
    )
    return cert, key


def run_submission(port: int, output: Path) -> None:
    environment = os.environ.copy()
    environment.update({"POWERSHELL_TELEMETRY_OPTOUT": "1", "VMWARE_CEIP_ENABLED": "0"})
    command = [
        "pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(RUNNER),
        "-ManifestPath", str(MANIFEST), "-Port", str(port), "-OutputPath", str(output),
    ]
    result = subprocess.run(
        command, cwd=ROOT, env=environment, capture_output=True, text=True, timeout=300
    )
    detail = (result.stdout + "\n" + result.stderr).strip()
    require(result.returncode == 0, f"PowerShell integration failed:\n{detail[-2500:]}")
    require(output.exists(), "PowerShell command did not emit its result")


def step(report: dict, index: int) -> dict:
    steps = report["steps"]
    require(len(steps) == 3, "the result must report exactly three steps")
    require(
        [item["index"] for item in steps] == [1, 2, 3],
        "the three step reports must be returned in Index order",
    )
    return steps[index - 1]


def verify_reporting(output: Path) -> None:
    report = json.loads(output.read_text(encoding="utf-8"))

    # -- the change that fails on its last step ------------------------------
    partial = report["partial"]
    require(partial["succeeded"] is False, "the partially applied change must not report success")
    require(
        partial["pluginId"] == PLUGIN_IDS[0],
        "the failing run must report the plugin id the server actually assigned",
    )
    require(partial["ruleId"] == "", "no notification rule was created, so ruleId must be empty")
    require(
        partial["requiresCleanup"] is True,
        "a change that applied two steps before failing must be flagged for cleanup",
    )

    first = step(partial, 1)
    require(
        first["operationId"] == "createAlertPlugin" and first["status"] == "Succeeded",
        "step 1 actually succeeded and must be reported as Succeeded",
    )
    require(first["statusCode"] is None, "a successful step must have a null statusCode")
    second = step(partial, 2)
    require(
        second["operationId"] == "updateAlertPlugin" and second["status"] == "Succeeded",
        "step 2 actually succeeded and must be reported as Succeeded",
    )
    require(second["statusCode"] is None, "a successful step must have a null statusCode")
    third = step(partial, 3)
    require(
        third["operationId"] == "createNotificationPluginRule",
        "step 3 must be the notification rule creation",
    )
    require(third["status"] == "Failed", "step 3 failed and must be reported as Failed")
    require(
        third["statusCode"] == 422,
        f"step 3 must report the 422 the server returned, got {third['statusCode']!r}",
    )
    require(
        TEMPLATE_REASON in third["message"],
        "step 3 must report the reason the server gave, not a generic message",
    )

    # -- the change that fully succeeds --------------------------------------
    complete = report["complete"]
    require(complete["succeeded"] is True, "the fully applied change must report success")
    require(
        complete["pluginId"] == PLUGIN_IDS[1],
        "the successful run must report the second plugin id",
    )
    require(
        complete["ruleId"] == RULE_IDS[0],
        "the successful run must report the notification rule id",
    )
    require(
        complete["requiresCleanup"] is False,
        "a change that applied cleanly must not be flagged for cleanup",
    )
    for index, operation_id in enumerate(
        ("createAlertPlugin", "updateAlertPlugin", "createNotificationPluginRule"), start=1
    ):
        entry = step(complete, index)
        require(
            entry["operationId"] == operation_id and entry["status"] == "Succeeded",
            f"step {index} of the successful change must be reported as Succeeded",
        )
        require(
            entry["statusCode"] is None,
            f"successful step {index} must have a null statusCode",
        )


def verify_wire(requests: list[dict]) -> None:
    require(requests, "loopback service received no requests")
    off_contract = [item for item in requests if item["offContract"]]
    require(
        not off_contract,
        "client called an operation the pinned contract does not name: "
        + ", ".join(f"{item['method']} {item['path']}" for item in off_contract),
    )

    sequence = [item["operationId"] for item in requests]
    require(
        sequence
        == [
            "acquireToken",
            "getCurrentVersionOfServer",
            "createAlertPlugin",
            "updateAlertPlugin",
            "createNotificationPluginRule",
            "createAlertPlugin",
            "updateAlertPlugin",
            "createNotificationPluginRule",
            "releaseToken",
        ],
        f"unexpected request sequence: {sequence}",
    )

    token = requests[0]
    require(
        token["path"] == BASE + "/api/auth/token/acquire" and token["method"] == "POST",
        "acquireToken wire target is wrong",
    )
    body = token["json"]
    require(
        body.get("username") == SEED_USER and body.get("password") == SEED_PASSWORD,
        "acquireToken must carry the supplied credentials",
    )

    module_requests = requests[2:8]
    for item in module_requests:
        require(
            item["headers"].get("authorization") == f"OpsToken {OPS_TOKEN}",
            "every module request must carry the acquired OpsToken",
        )
        require(item["query"] == {}, "none of these operations take a query string")

    create_a, update_a, rule_a, create_b, update_b, rule_b = module_requests

    # Scenario A - no optional parameter was bound, so none may appear on the
    # wire. An unset optional must be omitted, not sent as "" or [].
    require(
        create_a["path"] == BASE + "/api/alertplugins" and create_a["method"] == "POST",
        "createAlertPlugin wire target is wrong",
    )
    require(
        create_a["json"] == {"name": "seed-webhook", "pluginTypeId": "RestPlugin"},
        "createAlertPlugin must send only the bound fields; unset optionals must be "
        f"omitted rather than sent empty, got {create_a['json']!r}",
    )
    require(
        update_a["path"] == BASE + "/api/alertplugins" and update_a["method"] == "PUT",
        "updateAlertPlugin wire target is wrong",
    )
    require(
        update_a["json"]
        == {
            "name": "seed-webhook",
            "pluginTypeId": "RestPlugin",
            "pluginId": PLUGIN_IDS[0],
            "enabled": True,
        },
        f"updateAlertPlugin must enable the created instance, got {update_a['json']!r}",
    )
    require(
        rule_a["path"] == BASE + "/api/notifications/rules" and rule_a["method"] == "POST",
        "createNotificationPluginRule wire target is wrong",
    )
    require(
        rule_a["json"] == {"name": "seed-critical-rule", "pluginId": PLUGIN_IDS[0]},
        "the notification rule must send only the bound fields; unset optionals must be "
        f"omitted rather than sent empty, got {rule_a['json']!r}",
    )

    # Scenario B - every optional was bound and must reach the wire verbatim.
    expected_config = [
        {"name": "METHOD", "value": "POST"},
        {"name": "URL", "value": "https://hooks.example.com/vcf"},
        {"name": "ZED", "value": "uppercase-key"},
        {"name": "alpha", "value": "lowercase-key"},
    ]
    require(
        create_b["json"]
        == {
            "name": "seed-webhook-2",
            "pluginTypeId": "RestPlugin",
            "description": "Critical alert relay",
            "configValues": expected_config,
        },
        f"bound plugin optionals were not forwarded exactly, got {create_b['json']!r}",
    )
    require(
        update_b["json"]
        == {
            "name": "seed-webhook-2",
            "pluginTypeId": "RestPlugin",
            "description": "Critical alert relay",
            "configValues": expected_config,
            "pluginId": PLUGIN_IDS[1],
            "enabled": True,
        },
        f"the enable step must preserve the bound optionals, got {update_b['json']!r}",
    )
    require(
        rule_b["json"]
        == {
            "name": "seed-rule-2",
            "pluginId": PLUGIN_IDS[1],
            "templateId": KNOWN_TEMPLATE_ID,
            "criticalities": ["CRITICAL", "IMMEDIATE"],
        },
        f"bound rule optionals were not forwarded exactly, got {rule_b['json']!r}",
    )

    require(
        requests[-1]["path"] == BASE + "/api/auth/token/release",
        "the session must be released on teardown",
    )


def verify_mock_is_contract_pinned(mock: ContractPinnedVcfOps) -> None:
    """The service must refuse an operation the 9.0.0.0 contract does not name."""
    import http.client
    import ssl as ssl_module

    context = ssl_module.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl_module.CERT_NONE
    connection = http.client.HTTPSConnection("127.0.0.1", mock.port, context=context, timeout=30)
    try:
        method, path = OFF_CONTRACT_PROBE
        connection.request(method, BASE + path, body="{}", headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        response.read()
        require(
            response.status == 404,
            f"{NINE_ONE_ONLY_OPERATION} is not in the 9.0.0.0 contract and must be refused, "
            f"got {response.status}",
        )
    finally:
        connection.close()


def main() -> int:
    try:
        verify_contract()
        verify_submission_shape()
        with tempfile.TemporaryDirectory(prefix="vcf90-0065-") as temporary:
            directory = Path(temporary)
            cert, key = create_certificate(directory)
            request_log = directory / "requests.jsonl"
            output = directory / "result.json"
            with ContractPinnedVcfOps(CONTRACT, request_log, cert, key) as mock:
                verify_mock_is_contract_pinned(mock)
                probe_count = len(mock.requests())
                run_submission(mock.port, output)
                verify_wire(mock.requests()[probe_count:])
                verify_reporting(output)
        print(
            "PASS: 9.0.0.0 contract provenance, exact request wire shape, and accurate "
            "multi-step reporting verified"
        )
        return 0
    except (VerificationError, KeyError, ValueError, json.JSONDecodeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
