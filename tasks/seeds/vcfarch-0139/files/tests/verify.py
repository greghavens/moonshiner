#!/usr/bin/env python3
"""Protected, deterministic verifier for vcfarch-0139."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "out" / "architecture.json"
SPEC = ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
INVENTORY = ROOT / "fixtures" / "estate-inventory.json"
SNAPSHOT = ROOT / "authority" / "compatibility-snapshot.json"
ARCH_SCHEMA = ROOT / "schemas" / "existing-estate-architecture.schema.json"
MANIFEST = ROOT / "VcfMixedEstate" / "VcfMixedEstate.psd1"
MODULE = ROOT / "VcfMixedEstate" / "VcfMixedEstate.psm1"
RESEARCH = ROOT / "docs" / "research-sources.md"

PROTECTED_HASHES = {
    SPEC: "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d",
    INVENTORY: "8af23ed74b878e04f726dee10a27be26878f96d568fc8ddcbc14294095fba016",
    SNAPSHOT: "8155db94538a8dc4fc63ae2971dd58694315c21cdb609b7a688b69df8454be9b",
    ARCH_SCHEMA: "36160aedbd7d3f3e4701f81326bde7b048800e286bf94b674883db1cbfa1a93c",
}


class VerificationError(Exception):
    pass


class SchemaValidationError(VerificationError):
    pass


def fail(message: str) -> None:
    raise VerificationError(message)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VerificationError(f"missing required file: {path.relative_to(ROOT)}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(
            f"invalid JSON in {path.relative_to(ROOT)}: {exc.msg} at line {exc.lineno}"
        ) from exc


def check_protected(path: Path) -> None:
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError as exc:
        raise VerificationError(f"missing protected file: {path.relative_to(ROOT)}") from exc
    if digest != PROTECTED_HASHES[path]:
        fail(f"protected file was modified: {path.relative_to(ROOT)}")


def read_submission_text(path: Path) -> str:
    if path.is_symlink():
        fail(f"submission file must not be a symbolic link: {path.relative_to(ROOT)}")
    try:
        if not path.is_file():
            fail(f"submission path is not a regular file: {path.relative_to(ROOT)}")
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise VerificationError(f"missing required file: {path.relative_to(ROOT)}") from exc
    except UnicodeDecodeError as exc:
        raise VerificationError(f"required file is not UTF-8: {path.relative_to(ROOT)}") from exc


def verify_research_record() -> None:
    text = read_submission_text(RESEARCH)
    sections = re.split(r"(?m)^##\s+", text)[1:]
    if len(sections) < 2:
        fail("docs/research-sources.md must contain at least two '##' source entries")

    urls: set[str] = set()
    for index, section in enumerate(sections, start=1):
        lines = section.splitlines()
        title = lines[0].strip() if lines else ""
        if not title:
            fail(f"research source {index} has no title")

        fields: dict[str, str] = {}
        for line in lines[1:]:
            match = re.match(r"^\s*(Publisher|Accessed|URL|Used fact):\s*(.*?)\s*$", line, re.I)
            if match:
                fields[match.group(1).lower()] = match.group(2).rstrip().rstrip("  ").strip()
        missing = {"publisher", "accessed", "url", "used fact"} - set(fields)
        if missing:
            fail(f"research source {index} is missing fields: {sorted(missing)}")
        if not fields["publisher"]:
            fail(f"research source {index} has an empty publisher")
        try:
            date.fromisoformat(fields["accessed"])
        except ValueError as exc:
            raise VerificationError(
                f"research source {index} Accessed must be an ISO date"
            ) from exc

        parsed = urlparse(fields["url"])
        host = (parsed.hostname or "").lower()
        official_broadcom = host == "broadcom.com" or host.endswith(".broadcom.com")
        official_vmware_repo = (
            host == "github.com" and parsed.path.startswith("/vmware/vcf-api-specs")
        )
        if parsed.scheme != "https" or not (official_broadcom or official_vmware_repo):
            fail(f"research source {index} URL is not an official reachable-source URL")
        if fields["url"] in urls:
            fail(f"research source {index} duplicates an earlier URL")
        urls.add(fields["url"])
        if len(fields["used fact"]) < 20:
            fail(f"research source {index} has no substantive compatibility or upgrade fact")

    normalized = text.lower()
    required_topics = {
        "compatibility/interoperability": r"compatib|interoperab",
        "bill of materials": r"bill of materials|bundle|component builds",
        "supported upgrade path": r"upgrad|lifecycle",
    }
    for topic, pattern in required_topics.items():
        if re.search(pattern, normalized) is None:
            fail(f"research record does not cover {topic}")


def inspect_powershell_artifacts() -> dict[str, Any]:
    manifest_text = read_submission_text(MANIFEST)
    module_text = read_submission_text(MODULE)

    for module_name in ("VMware.Sdk.Vcf.Installer", "VMware.Sdk.Vcf.SddcManager"):
        vendored = [
            path
            for path in ROOT.rglob(module_name)
            if path != MANIFEST and path != MODULE
        ]
        if vendored:
            fail(f"VMware SDK module was vendored: {vendored[0].relative_to(ROOT)}")

    parser_script = r"""
$ErrorActionPreference = 'Stop'
$results = @()
foreach ($path in @($env:VCFARCH_MANIFEST_PATH, $env:VCFARCH_MODULE_PATH)) {
    $tokens = $null
    $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile(
        $path, [ref] $tokens, [ref] $errors)
    if ($errors.Count -gt 0) {
        throw "$path has parser errors: $($errors[0].Message)"
    }
    $results += $ast
}
$manifest = Import-PowerShellDataFile -LiteralPath $env:VCFARCH_MANIFEST_PATH
$requiredModules = @($manifest.RequiredModules | ForEach-Object {
    if ($_ -is [string]) { $_ }
    elseif ($_.ModuleName) { [string] $_.ModuleName }
})
$moduleAst = $results[1]
$functionAst = @($moduleAst.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'New-VcfMixedEstateArchitecture'
}, $true))[0]
$parameters = if ($null -eq $functionAst) { @() } else {
    @($functionAst.Body.ParamBlock.Parameters | ForEach-Object {
        $_.Name.VariablePath.UserPath
    })
}
$commands = @($moduleAst.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.CommandAst]
}, $true) | ForEach-Object { $_.GetCommandName() } | Where-Object { $_ } | Select-Object -Unique)
[ordered]@{
    rootModule = [string] $manifest.RootModule
    requiredModules = $requiredModules
    functionsToExport = @($manifest.FunctionsToExport)
    parameters = $parameters
    commands = $commands
} | ConvertTo-Json -Depth 5 -Compress
"""
    try:
        powershell_environment = os.environ.copy()
        powershell_environment["VCFARCH_MANIFEST_PATH"] = str(MANIFEST)
        powershell_environment["VCFARCH_MODULE_PATH"] = str(MODULE)
        completed = subprocess.run(
            [
                "pwsh",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                parser_script,
            ],
            cwd=ROOT,
            env=powershell_environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise VerificationError(f"unable to inspect PowerShell module: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        fail(f"PowerShell artifact inspection failed: {detail[-1] if detail else 'unknown error'}")
    try:
        inspected = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise VerificationError("PowerShell artifact inspection returned malformed output") from exc

    require_equal(inspected["rootModule"], "VcfMixedEstate.psm1", "manifest RootModule")
    required_modules = set(inspected["requiredModules"])
    expected_modules = {"VMware.Sdk.Vcf.Installer", "VMware.Sdk.Vcf.SddcManager"}
    if not expected_modules.issubset(required_modules):
        fail("manifest RequiredModules omits a required VMware SDK module")
    if "New-VcfMixedEstateArchitecture" not in inspected["functionsToExport"]:
        fail("manifest does not export New-VcfMixedEstateArchitecture")
    expected_parameters = {"InventoryPath", "CompatibilitySnapshotPath", "OutputPath"}
    if not expected_parameters.issubset(set(inspected["parameters"])):
        fail("New-VcfMixedEstateArchitecture is missing a required parameter")

    commands = set(inspected["commands"])
    required_initializers = {
        "Initialize-VcfInstallerSddcSpec",
        "Initialize-VcfInstallerSddcVcenterSpec",
        "Initialize-VcfInstallerSddcNetworkSpec",
        "Initialize-VcfInstallerDnsSpec",
    }
    missing_initializers = required_initializers - commands
    if missing_initializers:
        fail(f"module is missing installer SDK model calls: {sorted(missing_initializers)}")
    if not any(name and "VcfSddcManager" in name for name in commands):
        fail("module does not use the required VMware.Sdk.Vcf.SddcManager surface")
    if "Export-ModuleMember" not in commands:
        fail("module does not export its public function")
    forbidden_commands = {
        "Connect-VcfInstallerServer",
        "Connect-VcfSddcManagerServer",
        "Invoke-RestMethod",
        "Invoke-WebRequest",
        "Get-Date",
        "Get-Random",
        "New-Guid",
    }
    used_forbidden_commands = forbidden_commands.intersection(commands)
    if used_forbidden_commands:
        fail(
            "module connects externally or introduces nondeterminism: "
            f"{sorted(used_forbidden_commands)}"
        )

    fixture_literals = (
        "chi-legacy-01",
        "chi91-m01",
        "chi91-vc01.vcf.example.com",
        "8.0.3.00900-25413364",
        "8.0.3-25429389",
        "4.2.4.0.0-25410638",
        "9.1.0.0.25370922",
        "9.1.0.0.25370933",
        "9.1.0.0.25318225",
    )
    for literal in fixture_literals:
        if literal in module_text or literal in manifest_text:
            fail(f"module hard-codes fixture-specific value {literal!r}")
    return inspected


def resolve_pointer(root_schema: Any, reference: str) -> Any:
    if not reference.startswith("#/"):
        raise SchemaValidationError(f"only local schema references are supported: {reference}")
    node = root_schema
    for encoded_part in reference[2:].split("/"):
        part = encoded_part.replace("~1", "/").replace("~0", "~")
        try:
            node = node[part]
        except (KeyError, TypeError) as exc:
            raise SchemaValidationError(f"unresolvable schema reference: {reference}") from exc
    return node


def json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isfinite(float(left)) and math.isfinite(float(right)) and left == right
    return type(left) is type(right) and left == right


def instance_has_type(instance: Any, expected: str) -> bool:
    checks = {
        "null": lambda value: value is None,
        "object": lambda value: isinstance(value, dict),
        "array": lambda value: isinstance(value, list),
        "string": lambda value: isinstance(value, str),
        "boolean": lambda value: isinstance(value, bool),
        "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
        "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    }
    if expected not in checks:
        raise SchemaValidationError(f"unsupported schema type: {expected}")
    return checks[expected](instance)


def validate_schema(instance: Any, schema: Any, root_schema: Any, path: str = "$") -> None:
    if isinstance(schema, bool):
        if not schema:
            raise SchemaValidationError(f"{path}: rejected by false schema")
        return
    if not isinstance(schema, dict):
        raise SchemaValidationError(f"{path}: malformed schema")

    if "$ref" in schema:
        validate_schema(instance, resolve_pointer(root_schema, schema["$ref"]), root_schema, path)
        return

    for subschema in schema.get("allOf", []):
        validate_schema(instance, subschema, root_schema, path)

    if "anyOf" in schema:
        matches = 0
        for subschema in schema["anyOf"]:
            try:
                validate_schema(instance, subschema, root_schema, path)
                matches += 1
            except SchemaValidationError:
                pass
        if matches == 0:
            raise SchemaValidationError(f"{path}: does not match any allowed schema")

    if "oneOf" in schema:
        matches = 0
        for subschema in schema["oneOf"]:
            try:
                validate_schema(instance, subschema, root_schema, path)
                matches += 1
            except SchemaValidationError:
                pass
        if matches != 1:
            raise SchemaValidationError(f"{path}: must match exactly one schema, matched {matches}")

    if "not" in schema:
        try:
            validate_schema(instance, schema["not"], root_schema, path)
        except SchemaValidationError:
            pass
        else:
            raise SchemaValidationError(f"{path}: matches a prohibited schema")

    if instance is None and schema.get("nullable") is True:
        return

    if "const" in schema and not json_equal(instance, schema["const"]):
        raise SchemaValidationError(f"{path}: must equal {schema['const']!r}")
    if "enum" in schema and not any(json_equal(instance, item) for item in schema["enum"]):
        raise SchemaValidationError(f"{path}: value is not in the allowed enum")

    expected_type = schema.get("type")
    if expected_type is not None:
        allowed_types = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(instance_has_type(instance, item) for item in allowed_types):
            raise SchemaValidationError(
                f"{path}: expected {' or '.join(allowed_types)}, got {type(instance).__name__}"
            )

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for name in required:
            if name not in instance:
                raise SchemaValidationError(f"{path}: missing required property {name!r}")
        if "minProperties" in schema and len(instance) < schema["minProperties"]:
            raise SchemaValidationError(f"{path}: too few properties")
        if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
            raise SchemaValidationError(f"{path}: too many properties")

        properties = schema.get("properties", {})
        for name, value in instance.items():
            child_path = f"{path}.{name}"
            if name in properties:
                validate_schema(value, properties[name], root_schema, child_path)
            elif schema.get("additionalProperties") is False:
                raise SchemaValidationError(f"{path}: additional property {name!r} is not allowed")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_schema(value, schema["additionalProperties"], root_schema, child_path)

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            raise SchemaValidationError(f"{path}: too few items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            raise SchemaValidationError(f"{path}: too many items")
        if schema.get("uniqueItems"):
            canonical = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(canonical) != len(set(canonical)):
                raise SchemaValidationError(f"{path}: items must be unique")
        if "items" in schema:
            for index, item in enumerate(instance):
                validate_schema(item, schema["items"], root_schema, f"{path}[{index}]")

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            raise SchemaValidationError(f"{path}: string is too short")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            raise SchemaValidationError(f"{path}: string is too long")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], instance) is not None
            except re.error as exc:
                raise SchemaValidationError(f"{path}: invalid schema pattern: {exc}") from exc
            if not matched:
                raise SchemaValidationError(f"{path}: string does not match required pattern")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            raise SchemaValidationError(f"{path}: number is below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            raise SchemaValidationError(f"{path}: number is above maximum")
        exclusive_minimum = schema.get("exclusiveMinimum")
        if isinstance(exclusive_minimum, (int, float)) and not isinstance(exclusive_minimum, bool):
            if instance <= exclusive_minimum:
                raise SchemaValidationError(f"{path}: number is not above exclusive minimum")
        elif exclusive_minimum is True and "minimum" in schema and instance <= schema["minimum"]:
            raise SchemaValidationError(f"{path}: number is not above exclusive minimum")
        exclusive_maximum = schema.get("exclusiveMaximum")
        if isinstance(exclusive_maximum, (int, float)) and not isinstance(exclusive_maximum, bool):
            if instance >= exclusive_maximum:
                raise SchemaValidationError(f"{path}: number is not below exclusive maximum")
        elif exclusive_maximum is True and "maximum" in schema and instance >= schema["maximum"]:
            raise SchemaValidationError(f"{path}: number is not below exclusive maximum")


def require_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        fail(f"{label}: expected {expected!r}, got {actual!r}")


def verify_target_design(target: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    design = inventory["targetDesign"]
    target_bundle = snapshot["targetBundle"]
    target_versions = {
        item["componentId"]: item["version"] for item in target_bundle["components"]
    }

    require_equal(target.get("sddcId"), design["sddcId"], "targetSddcSpec.sddcId")
    require_equal(target.get("workflowType"), "VCF", "targetSddcSpec.workflowType")
    require_equal(target.get("version"), target_bundle["version"], "targetSddcSpec.version")
    require_equal(
        target.get("vcfInstanceName"), design["vcfInstanceName"], "targetSddcSpec.vcfInstanceName"
    )
    require_equal(target.get("dnsSpec"), design["dns"], "targetSddcSpec.dnsSpec")
    require_equal(target.get("ntpServers"), design["ntpServers"], "targetSddcSpec.ntpServers")
    require_equal(target.get("networkSpecs"), design["networks"], "targetSddcSpec.networkSpecs")

    vcenter = target.get("vcenterSpec", {})
    require_equal(vcenter.get("vcenterHostname"), design["vcenter"]["hostname"], "vcenter hostname")
    require_equal(
        vcenter.get("rootVcenterPassword"),
        design["vcenter"]["rootPasswordPlaceholder"],
        "vCenter password placeholder",
    )
    require_equal(vcenter.get("vmSize"), design["vcenter"]["vmSize"], "vCenter VM size")
    require_equal(vcenter.get("storageSize"), design["vcenter"]["storageSize"], "vCenter storage size")
    require_equal(vcenter.get("ssoDomain"), design["vcenter"]["ssoDomain"], "vCenter SSO domain")
    require_equal(vcenter.get("version"), target_versions["vcenter"], "vCenter target version")
    require_equal(vcenter.get("useExistingDeployment"), False, "vCenter deployment mode")


def applicable_gates(
    component_id: str, current_version: str, target_version: str, snapshot: dict[str, Any]
) -> list[dict[str, Any]]:
    return [
        gate
        for gate in snapshot["gates"]
        if component_id in gate["componentIds"]
        and gate["currentVersion"] == current_version
        and gate["targetVersion"] == target_version
    ]


def verify_plan(artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    plan = artifact["migrationPlan"]
    route = snapshot["routeConstraints"]
    target_bundle = snapshot["targetBundle"]
    inventory_components = {item["id"]: item for item in inventory["components"]}
    target_versions = {
        item["componentId"]: item["version"] for item in target_bundle["components"]
    }
    plans = {item["componentId"]: item for item in plan["components"]}

    require_equal(artifact["estateId"], inventory["estateId"], "estateId")
    require_equal(plan["targetBundleVersion"], target_bundle["version"], "target bundle")
    require_equal(plan["strategy"], route["requiredStrategy"], "migration strategy")
    require_equal(plan["finalState"], route["finalState"], "migration final state")
    require_equal(
        len(plan["components"]), len(inventory_components), "planned component record count"
    )
    require_equal(set(plans), set(inventory_components), "planned component IDs")
    require_equal(set(target_versions), set(inventory_components), "snapshot component IDs")

    all_gate_ids = {gate["id"] for gate in snapshot["gates"]}
    if not any(gate["kind"] == "back-in-time" for gate in snapshot["gates"]):
        fail("snapshot does not exercise a back-in-time route")

    for component_id, source in inventory_components.items():
        component_plan = plans[component_id]
        target_version = target_versions[component_id]
        require_equal(component_plan["name"], source["name"], f"{component_id} name")
        require_equal(
            component_plan["currentVersion"], source["version"], f"{component_id} current version"
        )
        require_equal(
            component_plan["targetVersion"], target_version, f"{component_id} target version"
        )
        gates = applicable_gates(component_id, source["version"], target_version, snapshot)
        expected_gate_ids = {gate["id"] for gate in gates}
        require_equal(
            set(component_plan["gates"]), expected_gate_ids, f"{component_id} applicable gates"
        )
        for gate in gates:
            if component_plan["disposition"] in gate.get("prohibitedDispositions", []):
                fail(f"{component_id} uses disposition prohibited by {gate['id']}")
            required_disposition = gate.get("requiredDisposition")
            if required_disposition is not None:
                require_equal(
                    component_plan["disposition"],
                    required_disposition,
                    f"{component_id} disposition required by {gate['id']}",
                )

    steps = plan["steps"]
    require_equal([step["order"] for step in steps], list(range(1, len(steps) + 1)), "step order")
    step_ids = [step["id"] for step in steps]
    if len(step_ids) != len(set(step_ids)):
        fail("migration step IDs must be unique")

    seen: set[str] = set()
    used_gate_ids: set[str] = set()
    phases: list[str] = []
    actions: set[str] = set()
    for step in steps:
        unknown_dependencies = set(step["dependsOn"]) - seen
        if unknown_dependencies:
            fail(f"step {step['id']} depends on non-prior steps: {sorted(unknown_dependencies)}")
        unknown_components = set(step["components"]) - set(inventory_components)
        if unknown_components:
            fail(f"step {step['id']} names unknown components: {sorted(unknown_components)}")
        unknown_gates = set(step["gates"]) - all_gate_ids
        if unknown_gates:
            fail(f"step {step['id']} names unknown gates: {sorted(unknown_gates)}")
        for gate_id in step["gates"]:
            gate = next(item for item in snapshot["gates"] if item["id"] == gate_id)
            if not set(gate["componentIds"]).intersection(step["components"]):
                fail(f"step {step['id']} cites {gate_id} without an affected component")
        if not phases or phases[-1] != step["phase"]:
            phases.append(step["phase"])
        actions.add(step["action"])
        used_gate_ids.update(step["gates"])
        seen.add(step["id"])

    require_equal(phases, route["requiredPhaseOrder"], "migration phase order")
    missing_actions = set(route["requiredActions"]) - actions
    if missing_actions:
        fail(f"migration plan is missing required actions: {sorted(missing_actions)}")
    forbidden_actions = set(route["forbiddenActions"]).intersection(actions)
    if forbidden_actions:
        fail(f"migration plan uses forbidden actions: {sorted(forbidden_actions)}")
    require_equal(used_gate_ids, all_gate_ids, "gates exercised by migration steps")

    for action in ("deploy-target", "validate-target", "retire-source"):
        covered = set().union(
            *(set(step["components"]) for step in steps if step["action"] == action)
        )
        require_equal(covered, set(inventory_components), f"components covered by {action}")

    target_combo = {item["componentId"]: item["version"] for item in target_bundle["components"]}
    supported_target_combos = [
        item["components"]
        for item in snapshot["supportedCombinations"]
        if item["context"] == "fresh-deployment"
    ]
    if target_combo not in supported_target_combos:
        fail("target component combination is not authorized for a fresh deployment")

    require_equal(steps[-1]["phase"], "retirement", "final migration phase")
    require_equal(steps[-1]["action"], "retire-source", "final migration action")


def main() -> int:
    try:
        # Protect and load only the submitted artifact and the pinned upstream spec first.
        check_protected(SPEC)
        if ARTIFACT.is_symlink():
            fail("out/architecture.json must not be a symbolic link")
        artifact = read_json(ARTIFACT)
        installer_spec = read_json(SPEC)
        if not isinstance(artifact, dict) or "targetSddcSpec" not in artifact:
            fail("out/architecture.json must contain targetSddcSpec")

        # This is intentionally the first content validation performed.
        validate_schema(
            artifact["targetSddcSpec"],
            {"$ref": "#/components/schemas/SddcSpec"},
            installer_spec,
            "$.targetSddcSpec",
        )

        # Only after the installer schema passes may estate-specific checks run.
        for protected_path in (INVENTORY, SNAPSHOT, ARCH_SCHEMA):
            check_protected(protected_path)
        inventory = read_json(INVENTORY)
        snapshot = read_json(SNAPSHOT)
        architecture_schema = read_json(ARCH_SCHEMA)
        validate_schema(artifact, architecture_schema, architecture_schema)
        verify_target_design(artifact["targetSddcSpec"], inventory, snapshot)
        verify_plan(artifact, inventory, snapshot)
        inspect_powershell_artifacts()
        verify_research_record()
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print("PASS: target SddcSpec and mixed-estate migration architecture are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
