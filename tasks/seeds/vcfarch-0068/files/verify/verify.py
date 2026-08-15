#!/usr/bin/env python3
"""Deterministic verifier for the brownfield VCF architecture seed.

The verifier inspects the submitted artifact, protected fixture/snapshot/
specification, module implementation, and the deterministic structure of the
required research record. It never depends on live network content.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import deque
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "architecture" / "migration-plan.json"
INVENTORY_PATH = ROOT / "fixtures" / "estate-inventory.json"
COMPATIBILITY_PATH = ROOT / "fixtures" / "compatibility-snapshot.json"
PLAN_SCHEMA_PATH = ROOT / "schemas" / "migration-plan.schema.json"
OPENAPI_PATH = ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
MODULE_PATH = ROOT / "VcfBrownfieldArchitecture" / "VcfBrownfieldArchitecture.psm1"
MANIFEST_PATH = ROOT / "VcfBrownfieldArchitecture" / "VcfBrownfieldArchitecture.psd1"
RESEARCH_PATH = ROOT / "research-sources.md"
OPENAPI_SHA256 = "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d"


class VerificationError(Exception):
    pass


def fail(message: str) -> None:
    raise VerificationError(message)


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        fail(f"missing required file: {path.relative_to(ROOT)}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {path.relative_to(ROOT)}: {exc}")


def pointer_get(document: Any, pointer: str) -> Any:
    if not pointer.startswith("#/"):
        fail(f"unsupported schema reference: {pointer}")
    value = document
    for raw_part in pointer[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        try:
            value = value[int(part)] if isinstance(value, list) else value[part]
        except (KeyError, IndexError, ValueError, TypeError):
            fail(f"unresolved schema reference: {pointer}")
    return value


def type_matches(instance: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(instance, dict)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "string":
        return isinstance(instance, str)
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "null":
        return instance is None
    fail(f"unsupported JSON Schema type: {expected}")


def validate(instance: Any, schema: dict[str, Any], root_schema: dict[str, Any], path: str = "$") -> None:
    """Validate the JSON Schema keywords used by the pinned OpenAPI and plan."""
    if "$ref" in schema:
        validate(instance, pointer_get(root_schema, schema["$ref"]), root_schema, path)
        return

    if "allOf" in schema:
        for subschema in schema["allOf"]:
            validate(instance, subschema, root_schema, path)
    if "anyOf" in schema:
        successes = 0
        for subschema in schema["anyOf"]:
            try:
                validate(instance, subschema, root_schema, path)
                successes += 1
            except VerificationError:
                pass
        if successes == 0:
            fail(f"{path}: value does not satisfy any allowed schema")
    if "oneOf" in schema:
        successes = 0
        for subschema in schema["oneOf"]:
            try:
                validate(instance, subschema, root_schema, path)
                successes += 1
            except VerificationError:
                pass
        if successes != 1:
            fail(f"{path}: value must satisfy exactly one schema")

    if "const" in schema and instance != schema["const"]:
        fail(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        fail(f"{path}: {instance!r} is not in the allowed values")

    expected_type = schema.get("type")
    if expected_type is not None:
        allowed = [expected_type] if isinstance(expected_type, str) else expected_type
        if not any(type_matches(instance, item) for item in allowed):
            fail(f"{path}: expected type {expected_type!r}")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        missing = [key for key in required if key not in instance]
        if missing:
            fail(f"{path}: missing required properties {missing}")
        properties = schema.get("properties", {})
        for key, value in instance.items():
            if key in properties:
                validate(value, properties[key], root_schema, f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                fail(f"{path}: unexpected property {key!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate(value, schema["additionalProperties"], root_schema, f"{path}.{key}")

    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            fail(f"{path}: too few items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            fail(f"{path}: too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(encoded) != len(set(encoded)):
                fail(f"{path}: array items must be unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, value in enumerate(instance):
                validate(value, item_schema, root_schema, f"{path}[{index}]")

    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            fail(f"{path}: string is too short")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            fail(f"{path}: string is too long")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            fail(f"{path}: string does not match {schema['pattern']!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            fail(f"{path}: number is below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            fail(f"{path}: number is above maximum")


def supported_path(snapshot: dict[str, Any], source: str, target: str) -> list[str]:
    queue: deque[list[str]] = deque([[source]])
    visited = {source}
    while queue:
        path = queue.popleft()
        if path[-1] == target:
            return path
        destinations = sorted(
            edge["to"] for edge in snapshot["supportedReleaseHops"] if edge["from"] == path[-1]
        )
        for destination in destinations:
            if destination not in visited:
                visited.add(destination)
                queue.append(path + [destination])
    fail(f"snapshot contains no supported release path from {source} to {target}")


def expected_gate_ids(release_path: list[str], snapshot: dict[str, Any]) -> list[str]:
    gate_ids = list(snapshot["requiredBaseGates"]) + [snapshot["hardwareGate"]]
    for release in release_path[1:]:
        gate_ids.extend(
            [
                f"bundles-staged-{release}",
                f"upgrade-precheck-{release}",
                f"nsx-complete-{release}",
                f"vcenter-complete-{release}",
                f"esxi-complete-{release}",
            ]
        )
    return gate_ids


def component_gates(
    component_type: str, role: str, release_path: list[str], snapshot: dict[str, Any]
) -> list[str]:
    if role == "management":
        return ["management-domain-unchanged"]
    gates = list(snapshot["requiredBaseGates"])
    if component_type == "ESXI_HOST":
        gates.append(snapshot["hardwareGate"])
    for hop_index, release in enumerate(release_path[1:]):
        if hop_index:
            gates.append(f"esxi-complete-{release_path[hop_index]}")
        gates.extend([f"bundles-staged-{release}", f"upgrade-precheck-{release}"])
        if component_type == "VCENTER":
            gates.append(f"nsx-complete-{release}")
        elif component_type == "ESXI_HOST":
            gates.append(f"vcenter-complete-{release}")
    return list(dict.fromkeys(gates))


def step_gates(
    component_type: str, hop_index: int, release_path: list[str], snapshot: dict[str, Any]
) -> list[str]:
    release = release_path[hop_index + 1]
    gates = list(snapshot["requiredBaseGates"])
    if component_type == "ESXI_HOST":
        gates.append(snapshot["hardwareGate"])
    if hop_index:
        gates.append(f"esxi-complete-{release_path[hop_index]}")
    gates.extend([f"bundles-staged-{release}", f"upgrade-precheck-{release}"])
    if component_type == "VCENTER":
        gates.append(f"nsx-complete-{release}")
    elif component_type == "ESXI_HOST":
        gates.append(f"vcenter-complete-{release}")
    return list(dict.fromkeys(gates))


def verify_semantics(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    domains = {domain["role"]: domain for domain in inventory["domains"]}
    if set(domains) != {"management", "workload"}:
        fail("fixture must contain exactly one management and one workload domain")
    management = domains["management"]
    workload = domains["workload"]
    release_path = supported_path(snapshot, workload["release"], inventory["targetRelease"])

    envelope = inventory["integrationEnvelope"]
    workload_vcenter = next(c for c in workload["components"] if c["type"] == "VCENTER")
    expected_envelope = {
        "sddcId": envelope["sddcId"],
        "workflowType": envelope["workflowType"],
        "version": inventory["targetRelease"],
        "vcenterSpec": {
            "vcenterHostname": workload_vcenter["fqdn"],
            "rootVcenterPassword": "REDACTED!",
            "version": snapshot["boms"][inventory["targetRelease"]]["VCENTER"],
            "useExistingDeployment": True,
        },
        "networkSpecs": envelope["networkSpecs"],
        "dnsSpec": envelope["dnsSpec"],
    }
    for key, value in expected_envelope.items():
        if plan[key] != value:
            fail(f"integration envelope mismatch for {key}")

    scalar_expected = {
        "schemaVersion": 1,
        "estateId": inventory["estateId"],
        "workloadDomainId": workload["id"],
        "sourceRelease": workload["release"],
        "targetRelease": inventory["targetRelease"],
        "managementDomainPolicy": "PRESERVE",
        "releasePath": release_path,
    }
    for key, value in scalar_expected.items():
        if plan[key] != value:
            fail(f"plan {key} does not match the fixture/snapshot")

    expected_components: list[dict[str, Any]] = []
    for domain in inventory["domains"]:
        for component in domain["components"]:
            if domain["role"] == "management":
                target_version = component["version"]
                disposition = "PRESERVE"
            else:
                target_version = snapshot["boms"][inventory["targetRelease"]][component["type"]]
                disposition = "UPGRADE"
            expected_components.append(
                {
                    "id": component["id"],
                    "domainId": domain["id"],
                    "role": domain["role"],
                    "type": component["type"],
                    "fqdn": component["fqdn"],
                    "currentVersion": component["version"],
                    "targetVersion": target_version,
                    "disposition": disposition,
                    "gates": component_gates(
                        component["type"], domain["role"], release_path, snapshot
                    ),
                }
            )
    if plan["components"] != expected_components:
        fail("components must name every fixture component with exact versions, target, disposition, and gates")

    management_ids = [component["id"] for component in management["components"]]
    workload_ids_by_type = {
        component_type: [
            component["id"]
            for component in workload["components"]
            if component["type"] == component_type
        ]
        for component_type in snapshot["componentOrder"]
    }
    expected_gates: list[dict[str, Any]] = [
        {
            "id": "management-domain-unchanged",
            "type": "INVARIANT",
            "subjectIds": management_ids,
            "expectedStatus": "UNCHANGED",
        },
        {
            "id": "source-health-green",
            "type": "HEALTH",
            "subjectIds": [workload["id"]],
            "expectedStatus": "GREEN",
        },
        {
            "id": snapshot["hardwareGate"],
            "type": "COMPATIBILITY",
            "subjectIds": workload_ids_by_type["ESXI_HOST"],
            "expectedStatus": "SUPPORTED",
        },
    ]
    for release in release_path[1:]:
        expected_gates.extend(
            [
                {
                    "id": f"bundles-staged-{release}",
                    "type": "BUNDLE",
                    "subjectIds": [workload["id"]],
                    "expectedStatus": "AVAILABLE",
                    "bundleIds": [
                        snapshot["bundleIds"][release][component_type]
                        for component_type in snapshot["componentOrder"]
                    ],
                },
                {
                    "id": f"upgrade-precheck-{release}",
                    "type": "PRECHECK",
                    "subjectIds": [workload["id"]],
                    "expectedStatus": "SUCCEEDED",
                },
                {
                    "id": f"nsx-complete-{release}",
                    "type": "DEPENDENCY",
                    "subjectIds": workload_ids_by_type["NSX_MANAGER"],
                    "expectedStatus": "COMPLETED",
                },
                {
                    "id": f"vcenter-complete-{release}",
                    "type": "DEPENDENCY",
                    "subjectIds": workload_ids_by_type["VCENTER"],
                    "expectedStatus": "COMPLETED",
                },
                {
                    "id": f"esxi-complete-{release}",
                    "type": "DEPENDENCY",
                    "subjectIds": workload_ids_by_type["ESXI_HOST"],
                    "expectedStatus": "COMPLETED",
                },
            ]
        )
    if plan["gates"] != expected_gates:
        fail("gates do not match the pinned compatibility authority")

    expected_steps: list[dict[str, Any]] = []
    sequence = 1
    for hop_index, (release_from, release_to) in enumerate(zip(release_path, release_path[1:])):
        for component_type in snapshot["componentOrder"]:
            expected_steps.append(
                {
                    "sequence": sequence,
                    "releaseFrom": release_from,
                    "releaseTo": release_to,
                    "componentType": component_type,
                    "componentIds": workload_ids_by_type[component_type],
                    "fromVersion": snapshot["boms"][release_from][component_type],
                    "toVersion": snapshot["boms"][release_to][component_type],
                    "gates": step_gates(component_type, hop_index, release_path, snapshot),
                    "completionGate": f"{component_type.lower().replace('_manager', '').replace('_host', '')}-complete-{release_to}",
                }
            )
            sequence += 1
    if plan["steps"] != expected_steps:
        fail("ordered steps must follow every supported hop and the pinned NSX/vCenter/ESXi order")

    referenced_gates = set()
    for component in plan["components"]:
        referenced_gates.update(component["gates"])
    for step in plan["steps"]:
        referenced_gates.update(step["gates"])
        referenced_gates.add(step["completionGate"])
        if set(step["componentIds"]) & set(management_ids):
            fail("an upgrade step references a management-domain component")
    actual_gate_ids = [gate["id"] for gate in plan["gates"]]
    if actual_gate_ids != expected_gate_ids(release_path, snapshot):
        fail("gate IDs are missing, duplicated, or out of deterministic order")
    if not referenced_gates.issubset(set(actual_gate_ids)):
        fail("a component or step references an undefined gate")


def verify_module(plan: dict[str, Any]) -> None:
    if not MODULE_PATH.is_file() or not MANIFEST_PATH.is_file():
        fail("PowerShell module and manifest are required")
    module_text = MODULE_PATH.read_text(encoding="utf-8")
    manifest_text = MANIFEST_PATH.read_text(encoding="utf-8")
    required_module_tokens = [
        "VMware.Sdk.Vcf.SddcManager",
        "Get-VcfWorkloadDomainInventory",
        "New-VcfWorkloadDomainMigrationPlan",
    ]
    for token in required_module_tokens:
        if token not in module_text and token not in manifest_text:
            fail(f"PowerShell module is missing {token}")
    inventory_ast_script = r"""
Import-Module VMware.Sdk.Vcf.SddcManager -ErrorAction Stop
$errors = $null
$tokens = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:VCF_MODULE_FILE,
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count) { exit 1 }
$inventoryFunction = @(
    $ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq 'Get-VcfWorkloadDomainInventory'
    }, $true)
)
if ($inventoryFunction.Count -ne 1) { exit 2 }
$shadowedSdkCommands = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -match '^(Connect|Disconnect|Invoke)-Vcf'
}, $true))
if ($shadowedSdkCommands.Count) { exit 3 }
$commands = @($inventoryFunction[0].FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.CommandAst]
}, $true))
$sdkCommands = @($commands | Where-Object {
    $_.GetCommandName() -match '^(Connect|Disconnect|Invoke)-Vcf'
})
if (-not @($sdkCommands | Where-Object {
    $_.GetCommandName() -match '^Invoke-VcfGet'
}).Count) { exit 4 }
foreach ($command in $sdkCommands) {
    $name = $command.GetCommandName()
    $metadata = @(Get-Command -Name $name -Module VMware.Sdk.Vcf.SddcManager `
        -ErrorAction SilentlyContinue)
    if ($metadata.Count -ne 1) { exit 5 }
    $argumentNames = @($command.CommandElements | Where-Object {
        $_ -is [System.Management.Automation.Language.CommandParameterAst]
    } | ForEach-Object { $_.ParameterName })
    foreach ($argumentName in $argumentNames) {
        if (-not $metadata[0].Parameters.ContainsKey($argumentName)) { exit 6 }
    }
}
$forbiddenCommands = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.CommandAst] -and
        $node.GetCommandName() -in @('Install-Module', 'Save-Module')
}, $true))
if ($forbiddenCommands.Count) { exit 7 }
"""
    env = os.environ.copy()
    env["VCF_MODULE_FILE"] = str(MODULE_PATH)
    inventory_ast = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-Command", inventory_ast_script],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=20,
        check=False,
    )
    if inventory_ast.returncode != 0:
        fail("SDK-backed inventory function is missing, shadows the SDK, or uses invalid SDK commands")

    parse_script = (
        "$e=$null;$t=$null;"
        "[System.Management.Automation.Language.Parser]::ParseFile($env:VCF_MODULE_FILE,[ref]$t,[ref]$e)>$null;"
        "if($e.Count){$e|ForEach-Object{$_.Message};exit 1}"
    )
    parsed = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-Command", parse_script],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=20,
        check=False,
    )
    if parsed.returncode != 0:
        fail(f"PowerShell module has syntax errors: {parsed.stdout.strip()}")

    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temp_directory:
        generated_path = Path(temp_directory) / "migration-plan.json"
        env.update(
            {
                "VCF_INVENTORY_FILE": str(INVENTORY_PATH),
                "VCF_COMPATIBILITY_FILE": str(COMPATIBILITY_PATH),
                "VCF_OUTPUT_FILE": str(generated_path),
            }
        )
        generate_script = (
            "Import-Module $env:VCF_MODULE_FILE -Force;"
            "New-VcfWorkloadDomainMigrationPlan "
            "-InventoryPath $env:VCF_INVENTORY_FILE "
            "-CompatibilityPath $env:VCF_COMPATIBILITY_FILE "
            "-OutputPath $env:VCF_OUTPUT_FILE | Out-Null"
        )
        generated = subprocess.run(
            ["pwsh", "-NoLogo", "-NoProfile", "-Command", generate_script],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
        )
        if generated.returncode != 0:
            fail(f"planning command failed: {generated.stdout.strip()}")
        generated_plan = load_json(generated_path)
        if generated_plan != plan:
            fail("checked-in architecture is not reproduced by the planning command")


def verify_research() -> None:
    try:
        text = RESEARCH_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        fail("research-sources.md is required")

    access_dates = re.findall(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)", text)
    if not access_dates:
        fail("research-sources.md must record an ISO access date")
    if len(access_dates) != 1:
        fail("research-sources.md must record one shared access date")
    for access_date in access_dates:
        try:
            date.fromisoformat(access_date)
        except ValueError:
            fail("research-sources.md contains an invalid access date")

    entries = re.findall(
        r"(?m)^\s*-\s+\[([^\]\r\n]+)\]\((https://[^)\s]+)\)\s+(?:—|-)\s+(.+?)\s*$",
        text,
    )
    if len(entries) < 2:
        fail("research-sources.md must contain at least two titled HTTPS sources and decisions")

    urls: set[str] = set()
    research_topics: list[str] = []
    for title, url, decision in entries:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (
            hostname == "broadcom.com" or hostname.endswith(".broadcom.com")
        ):
            fail("research sources must use exact HTTPS URLs on official Broadcom sites")
        if url in urls:
            fail("research source URLs must be unique")
        urls.add(url)
        if not title.strip() or not decision.strip():
            fail("each research source needs a title and the decision it informed")
        research_topics.append(f"{title} {decision}".lower())

    combined_topics = " ".join(research_topics)
    if not re.search(r"compatib|interop|hardware", combined_topics):
        fail("research must cover compatibility or interoperability material")
    if not re.search(r"upgrad|bundle", combined_topics):
        fail("research must cover official upgrade or bundle material")


def main() -> int:
    try:
        # Required first validation stage: the artifact is checked directly
        # against the VCF Installer's own SddcSpec before custom checks run.
        plan = load_json(PLAN_PATH)
        openapi = load_json(OPENAPI_PATH)
        sddc_schema = openapi["components"]["schemas"]["SddcSpec"]
        validate(plan, sddc_schema, openapi)
        print("PASS: artifact validates against VCF Installer SddcSpec")

        plan_schema = load_json(PLAN_SCHEMA_PATH)
        validate(plan, plan_schema, plan_schema)
        print("PASS: artifact validates against migration plan schema")

        digest = hashlib.sha256(OPENAPI_PATH.read_bytes()).hexdigest()
        if digest != OPENAPI_SHA256:
            fail("pinned VCF Installer specification hash changed")
        if openapi.get("info", {}).get("version") != "9.1.0.0":
            fail("pinned VCF Installer specification version changed")

        inventory = load_json(INVENTORY_PATH)
        snapshot = load_json(COMPATIBILITY_PATH)
        verify_semantics(plan, inventory, snapshot)
        print("PASS: architecture matches inventory and pinned compatibility snapshot")

        verify_module(plan)
        print("PASS: PowerShell module reproduces the architecture and uses VMware.Sdk.Vcf")

        verify_research()
        print("PASS: research record contains dated official Broadcom sources and decisions")
        return 0
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    except (KeyError, StopIteration, TypeError, ValueError) as exc:
        print(f"FAIL: malformed fixture or artifact structure: {exc}", file=sys.stderr)
        return 1
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL: verifier runtime error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
