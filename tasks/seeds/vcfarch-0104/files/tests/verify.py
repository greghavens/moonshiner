#!/usr/bin/env python3
"""Offline acceptance verifier for the VCF fleet architecture artifact."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "output" / "vcf-fleet-architecture.json"
OPENAPI = ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
PLAN_SCHEMA = ROOT / "grading" / "migration-plan.schema.json"
INVENTORY = ROOT / "fixtures" / "estate-inventory.json"
SNAPSHOT = ROOT / "grading" / "compatibility-snapshot.json"
MODULE_LOCATIONS = (
    (
        ROOT / "VcfFleetArchitecture" / "VcfFleetArchitecture.psd1",
        ROOT / "VcfFleetArchitecture" / "VcfFleetArchitecture.psm1",
    ),
    (ROOT / "VcfFleetArchitecture.psd1", ROOT / "VcfFleetArchitecture.psm1"),
)
RESEARCH = ROOT / "research" / "consulted-sources.json"


class ValidationError(AssertionError):
    pass


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValidationError(f"missing required file: {path.relative_to(ROOT)}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid JSON in {path.relative_to(ROOT)}: {exc}") from exc


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def verify_no_vendored_vmware_module() -> None:
    module_name = "vmware.sdk.vcf.installer"
    module_file_suffixes = {".cat", ".cdxml", ".dll", ".nupkg", ".ps1xml", ".psd1", ".psm1", ".zip"}
    for path in ROOT.rglob("*"):
        lowered = path.name.lower()
        is_module_directory = path.is_dir() and lowered == module_name
        is_module_file = (
            path.is_file()
            and lowered.startswith(module_name + ".")
            and path.suffix.lower() in module_file_suffixes
        )
        if is_module_directory or is_module_file:
            raise ValidationError(
                f"vendored VMware.Sdk.Vcf.Installer content is not allowed: {path.relative_to(ROOT)}"
            )


def inspect_powershell_module() -> dict[str, Any]:
    module_manifest: Path | None = None
    module_source: Path | None = None
    for manifest, source in MODULE_LOCATIONS:
        if source.is_file():
            module_manifest = manifest if manifest.is_file() else None
            module_source = source
            break
    if module_source is None:
        expected = MODULE_LOCATIONS[0][1].relative_to(ROOT)
        raise ValidationError(f"missing required PowerShell module: {expected}")

    # Parse the module without importing it: verification remains offline while the
    # authored artifact is still checked using PowerShell's real parser.
    script = r"""
$ErrorActionPreference = 'Stop'
$manifest = $null
if ($env:VCF_VERIFY_MANIFEST) {
    $manifest = Import-PowerShellDataFile -LiteralPath $env:VCF_VERIFY_MANIFEST
}
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:VCF_VERIFY_MODULE,
    [ref] $tokens,
    [ref] $parseErrors
)
if ($parseErrors.Count -gt 0) {
    throw (($parseErrors | ForEach-Object Message) -join '; ')
}
$functions = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -ieq 'New-VcfFleetArchitecture'
}, $true))
$sdkInitializerDefinitions = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -ilike 'Initialize-VcfInstaller*'
}, $true) | ForEach-Object Name)
$parameters = @()
if ($functions.Count -eq 1 -and $null -ne $functions[0].Body.ParamBlock) {
    $parameters = @($functions[0].Body.ParamBlock.Parameters | ForEach-Object {
        $_.Name.VariablePath.UserPath
    })
}
$commands = @()
if ($functions.Count -eq 1) {
    $commands = @($functions[0].FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.CommandAst]
    }, $true) | ForEach-Object { $_.GetCommandName() } | Where-Object { $null -ne $_ })
}
$manifestRequiredModules = @($manifest.RequiredModules | ForEach-Object {
    if ($_ -is [string]) { $_ }
    elseif ($null -ne $_.ModuleName) { $_.ModuleName }
    else { $_.Name }
})
$scriptRequiredModules = @($ast.ScriptRequirements.RequiredModules | ForEach-Object { $_.Name })
[pscustomobject]@{
    manifestPresent = $null -ne $manifest
    rootModule = $manifest.RootModule
    requiredModules = @($manifestRequiredModules + $scriptRequiredModules)
    functionsToExport = @($manifest.FunctionsToExport)
    matchingFunctionCount = $functions.Count
    sdkInitializerDefinitions = $sdkInitializerDefinitions
    functionParameters = $parameters
    commands = $commands
} | ConvertTo-Json -Compress -Depth 8
"""
    environment = os.environ.copy()
    environment["VCF_VERIFY_MANIFEST"] = str(module_manifest) if module_manifest else ""
    environment["VCF_VERIFY_MODULE"] = str(module_source)
    try:
        completed = subprocess.run(
            ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
            env=environment,
        )
    except FileNotFoundError as exc:
        raise ValidationError("PowerShell (pwsh) is unavailable for module validation") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValidationError("PowerShell module validation timed out") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown parser failure"
        raise ValidationError(f"invalid PowerShell module: {detail}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValidationError("PowerShell module inspection returned invalid JSON") from exc


def verify_module() -> None:
    module = inspect_powershell_module()
    if (
        module.get("manifestPresent")
        and str(module.get("rootModule", "")).lower() != "vcffleetarchitecture.psm1"
    ):
        raise ValidationError("module manifest RootModule must be VcfFleetArchitecture.psm1")

    required_modules = {str(item).lower() for item in as_list(module.get("requiredModules"))}
    if "vmware.sdk.vcf.installer" not in required_modules:
        raise ValidationError("module manifest must declare VMware.Sdk.Vcf.Installer")

    exports = {str(item).lower() for item in as_list(module.get("functionsToExport"))}
    if (
        module.get("manifestPresent")
        and "new-vcffleetarchitecture" not in exports
        and "*" not in exports
    ):
        raise ValidationError("module manifest must export New-VcfFleetArchitecture")
    if module.get("matchingFunctionCount") != 1:
        raise ValidationError("module must define New-VcfFleetArchitecture exactly once")
    if as_list(module.get("sdkInitializerDefinitions")):
        raise ValidationError("module must not replace VMware.Sdk.Vcf.Installer initializer functions")

    expected_parameters = {"inventorypath", "compatibilitysnapshotpath", "outputpath"}
    actual_parameters = {str(item).lower() for item in as_list(module.get("functionParameters"))}
    missing_parameters = expected_parameters - actual_parameters
    if missing_parameters:
        raise ValidationError(
            "New-VcfFleetArchitecture is missing required parameters; "
            f"missing={sorted(missing_parameters)}"
        )

    commands = {str(item).lower() for item in as_list(module.get("commands"))}
    if "initialize-vcfinstallersddcspec" not in commands:
        raise ValidationError(
            "New-VcfFleetArchitecture module must construct the top-level installer SddcSpec model"
        )


def verify_research() -> None:
    document = load_json(RESEARCH)
    if isinstance(document, dict):
        records = document.get("consulted", document.get("sources"))
    else:
        records = document
    if not isinstance(records, list) or not records:
        raise ValidationError("research/consulted-sources.json must contain consulted source records")

    for index, record in enumerate(records):
        path = f"research/consulted-sources.json[{index}]"
        if not isinstance(record, dict):
            raise ValidationError(f"{path}: expected an object")
        for field in ("title", "url", "accessedAt", "claim"):
            value = record.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValidationError(f"{path}.{field}: expected a non-empty string")

        parsed_url = urlparse(record["url"])
        hostname = (parsed_url.hostname or "").lower().rstrip(".")
        if parsed_url.scheme != "https" or not (
            hostname == "broadcom.com" or hostname.endswith(".broadcom.com")
        ):
            raise ValidationError(f"{path}.url: expected an official Broadcom HTTPS source")

        timestamp = record["accessedAt"]
        if re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})",
            timestamp,
        ) is None:
            raise ValidationError(f"{path}.accessedAt: expected an RFC 3339 timestamp")
        try:
            accessed_at = datetime.fromisoformat(timestamp[:-1] + "+00:00" if timestamp.endswith("Z") else timestamp)
        except ValueError as exc:
            raise ValidationError(f"{path}.accessedAt: expected an RFC 3339 timestamp") from exc
        if accessed_at.tzinfo is None or accessed_at.utcoffset() is None:
            raise ValidationError(f"{path}.accessedAt: timestamp must include a UTC offset")


def resolve_pointer(document: Any, pointer: str) -> Any:
    if not pointer.startswith("#/"):
        raise ValidationError(f"only local schema references are supported: {pointer}")
    current = document
    for token in pointer[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        current = current[token]
    return current


def json_type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    raise ValidationError(f"unsupported JSON Schema type: {expected}")


def validate(instance: Any, schema: dict[str, Any], document: dict[str, Any], path: str = "$") -> None:
    if "$ref" in schema:
        validate(instance, resolve_pointer(document, schema["$ref"]), document, path)
        return

    for branch in schema.get("allOf", []):
        validate(instance, branch, document, path)

    if "anyOf" in schema:
        successes = 0
        for branch in schema["anyOf"]:
            try:
                validate(instance, branch, document, path)
                successes += 1
            except ValidationError:
                pass
        if successes == 0:
            raise ValidationError(f"{path}: does not match any allowed schema")

    if "oneOf" in schema:
        successes = 0
        for branch in schema["oneOf"]:
            try:
                validate(instance, branch, document, path)
                successes += 1
            except ValidationError:
                pass
        if successes != 1:
            raise ValidationError(f"{path}: must match exactly one schema")

    if "const" in schema and instance != schema["const"]:
        raise ValidationError(f"{path}: value does not match const")
    if "enum" in schema and instance not in schema["enum"]:
        raise ValidationError(f"{path}: value {instance!r} is not in enum")

    expected_type = schema.get("type")
    if expected_type is not None:
        allowed_types = [expected_type] if isinstance(expected_type, str) else expected_type
        if not any(json_type_matches(instance, item) for item in allowed_types):
            raise ValidationError(f"{path}: expected {allowed_types}, got {type(instance).__name__}")

    if isinstance(instance, dict):
        for required in schema.get("required", []):
            if required not in instance:
                raise ValidationError(f"{path}: missing required property {required!r}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extras = sorted(set(instance) - set(properties))
            if extras:
                raise ValidationError(f"{path}: unexpected properties {extras}")
        for key, subschema in properties.items():
            if key in instance:
                validate(instance[key], subschema, document, f"{path}.{key}")

    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            raise ValidationError(f"{path}: too few items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            raise ValidationError(f"{path}: too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(encoded) != len(set(encoded)):
                raise ValidationError(f"{path}: duplicate array items")
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(instance):
                validate(item, schema["items"], document, f"{path}[{index}]")

    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            raise ValidationError(f"{path}: string is too short")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            raise ValidationError(f"{path}: string is too long")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            raise ValidationError(f"{path}: string does not match {schema['pattern']!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            raise ValidationError(f"{path}: number is below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            raise ValidationError(f"{path}: number is above maximum")


def assert_subset(actual: Any, expected: Any, path: str = "$") -> None:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            raise ValidationError(f"{path}: expected an object")
        for key, value in expected.items():
            if key not in actual:
                raise ValidationError(f"{path}: missing inventory-defined property {key!r}")
            assert_subset(actual[key], value, f"{path}.{key}")
    elif isinstance(expected, list):
        if actual != expected:
            raise ValidationError(f"{path}: does not match the inventory target design")
    elif actual != expected:
        raise ValidationError(f"{path}: expected {expected!r}, got {actual!r}")


def verify() -> None:
    verify_no_vendored_vmware_module()
    verify_module()
    verify_research()

    # The artifact is validated against the pinned installer's own SddcSpec first.
    artifact = load_json(ARTIFACT)
    openapi = load_json(OPENAPI)
    try:
        sddc_schema = openapi["components"]["schemas"]["SddcSpec"]
    except KeyError as exc:
        raise ValidationError("pinned installer specification has no SddcSpec schema") from exc
    validate(artifact, sddc_schema, openapi)

    # Only after installer-schema validation may brownfield grading begin.
    plan_schema = load_json(PLAN_SCHEMA)
    inventory = load_json(INVENTORY)
    snapshot = load_json(SNAPSHOT)
    if "migrationPlan" not in artifact:
        raise ValidationError("$.migrationPlan: missing ordered brownfield plan")
    validate(artifact["migrationPlan"], plan_schema, plan_schema, "$.migrationPlan")

    if openapi.get("info", {}).get("version") != "9.1.0.0":
        raise ValidationError("installer specification is not pinned to 9.1.0.0")
    assert_subset(artifact, inventory["targetSddcSpec"])

    plan = artifact["migrationPlan"]
    if plan["estateId"] != inventory["estateId"]:
        raise ValidationError("migration plan estateId does not match inventory")
    if plan["targetFleetVersion"] != snapshot["targetFleetVersion"]:
        raise ValidationError("migration plan targetFleetVersion does not match snapshot")
    if inventory["targetFleetVersion"] != snapshot["targetFleetVersion"]:
        raise ValidationError("fixture and compatibility snapshot target different fleets")

    inventory_by_id = {item["id"]: item for item in inventory["components"]}
    steps_by_id = {item["componentId"]: item for item in plan["steps"]}
    if len(steps_by_id) != len(plan["steps"]):
        raise ValidationError("migration plan repeats a component")
    if set(steps_by_id) != set(inventory_by_id):
        missing = sorted(set(inventory_by_id) - set(steps_by_id))
        extra = sorted(set(steps_by_id) - set(inventory_by_id))
        raise ValidationError(f"migration plan inventory coverage differs; missing={missing}, extra={extra}")

    ordered = sorted(
        inventory["components"],
        key=lambda item: (snapshot["componentRules"][item["componentType"]]["planRank"], item["id"]),
    )
    expected_component_order = [item["id"] for item in ordered]
    actual_component_order = [item["componentId"] for item in plan["steps"]]
    if actual_component_order != expected_component_order:
        raise ValidationError(
            "migration plan steps are not in frozen-authority order; "
            f"expected={expected_component_order}, actual={actual_component_order}"
        )
    expected_order = {item["id"]: index for index, item in enumerate(ordered, start=1)}

    for component_id, component in inventory_by_id.items():
        step = steps_by_id[component_id]
        rule = snapshot["componentRules"][component["componentType"]]
        expected = {
            "order": expected_order[component_id],
            "componentId": component_id,
            "componentType": component["componentType"],
            "currentVersion": component["version"],
            "targetComponent": rule["targetComponent"],
            "targetVersion": rule["targetVersion"],
            "disposition": rule["disposition"],
            "gates": rule["gates"],
        }
        step_without_gates = {key: value for key, value in step.items() if key != "gates"}
        expected_without_gates = {key: value for key, value in expected.items() if key != "gates"}
        actual_gates = {json.dumps(gate, sort_keys=True) for gate in step["gates"]}
        expected_gates = {json.dumps(gate, sort_keys=True) for gate in expected["gates"]}
        if step_without_gates != expected_without_gates or actual_gates != expected_gates:
            raise ValidationError(f"migration step for {component_id} differs from inventory/snapshot authority")

    positions_by_type: dict[str, list[int]] = {}
    for step in plan["steps"]:
        positions_by_type.setdefault(step["componentType"], []).append(step["order"])
    for constraint in snapshot["sequenceConstraints"]:
        before = positions_by_type[constraint["beforeType"]]
        after = positions_by_type[constraint["afterType"]]
        if max(before) >= min(after):
            raise ValidationError(
                f"sequence constraint violated: {constraint['beforeType']} before {constraint['afterType']}"
            )

    print("PASS: PowerShell module, research provenance, SddcSpec, and frozen-authority migration plan")


if __name__ == "__main__":
    try:
        verify()
    except (ValidationError, KeyError, TypeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
