#!/usr/bin/env python3
"""Deterministic verifier for vcfarch-0159.

The verifier is deliberately offline.  It validates the submitted artifact with
the schema embedded in installer-spec.json before performing any semantic or
PowerShell implementation checks.  Research execution and the contents of its
record are not inspected, fetched, or evaluated here.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]


class VerificationError(Exception):
    pass


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VerificationError(f"missing required file: {path.relative_to(ROOT)}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(
            f"invalid JSON in {path.relative_to(ROOT)}: {exc.msg} at line {exc.lineno}"
        ) from exc


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
    raise VerificationError(f"unsupported schema type in installer specification: {expected}")


def validate_schema(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Validate the JSON-Schema subset used by the installer specification."""
    errors: list[str] = []

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value {value!r} is not in {schema['enum']!r}")

    expected_type = schema.get("type")
    if expected_type is not None and not json_type_matches(value, expected_type):
        errors.append(f"{path}: expected {expected_type}, got {type(value).__name__}")
        return errors

    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                errors.append(f"{path}: missing required property {key!r}")

        minimum_properties = schema.get("minProperties")
        if minimum_properties is not None and len(value) < minimum_properties:
            errors.append(f"{path}: expected at least {minimum_properties} properties")

        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in properties:
                errors.extend(validate_schema(child, properties[key], child_path))
            elif additional is False:
                errors.append(f"{path}: undeclared property {key!r}")
            elif isinstance(additional, dict):
                errors.extend(validate_schema(child, additional, child_path))

    if isinstance(value, list):
        minimum_items = schema.get("minItems")
        if minimum_items is not None and len(value) < minimum_items:
            errors.append(f"{path}: expected at least {minimum_items} items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(encoded) != len(set(encoded)):
                errors.append(f"{path}: array items must be unique")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, child in enumerate(value):
                errors.extend(validate_schema(child, item_schema, f"{path}[{index}]"))

    if isinstance(value, str):
        minimum_length = schema.get("minLength")
        if minimum_length is not None and len(value) < minimum_length:
            errors.append(f"{path}: string is shorter than {minimum_length}")
        pattern = schema.get("pattern")
        if pattern is not None and re.search(pattern, value) is None:
            errors.append(f"{path}: value does not match pattern {pattern!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        if minimum is not None and value < minimum:
            errors.append(f"{path}: value {value} is below minimum {minimum}")

    return errors


def index_inventory(inventory: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    products = {item["product"]: item for item in inventory["sourceProducts"]}
    clusters: dict[str, Any] = {}
    for domain in inventory["domains"]:
        for cluster in domain["clusters"]:
            clusters[cluster["clusterId"]] = {
                **cluster,
                "domainId": domain["domainId"],
            }
    return products, clusters


def check_capacity(placements: list[dict[str, Any]], clusters: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    used: dict[str, dict[str, float]] = defaultdict(
        lambda: {"vCpu": 0.0, "memoryGiB": 0.0, "storageTiB": 0.0}
    )
    for placement in placements:
        cluster_id = placement["clusterId"]
        cluster = clusters.get(cluster_id)
        if cluster is None:
            errors.append(f"placement uses unknown cluster {cluster_id!r}")
            continue
        if placement["domainId"] != cluster["domainId"]:
            errors.append(f"placement {placement['component']} uses the wrong domain for {cluster_id}")
        if placement["networkSegmentId"] not in cluster["networkSegmentIds"]:
            errors.append(
                f"placement {placement['component']} uses network absent from {cluster_id}"
            )
        for resource in used[cluster_id]:
            used[cluster_id][resource] += (
                placement["nodeCount"] * placement["perNodeResources"][resource]
            )

    for cluster_id, totals in used.items():
        headroom = clusters[cluster_id]["headroom"]
        for resource, amount in totals.items():
            if amount > headroom[resource] + 1e-9:
                errors.append(
                    f"{cluster_id} overcommits {resource}: requires {amount}, has {headroom[resource]}"
                )
    return errors


def exact_gate_ids(step: dict[str, Any]) -> list[str]:
    return [gate["gateId"] for gate in step["gates"]]


def inventory_content(products: dict[str, Any], product: str) -> dict[str, str]:
    return {item["itemId"]: item["category"] for item in products[product]["content"]}


def check_research(consulted: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for index, source in enumerate(consulted):
        label = f"research.consulted[{index}]"
        parsed = urlparse(source["url"])
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (
            hostname == "broadcom.com" or hostname.endswith(".broadcom.com")
        ):
            errors.append(f"{label} is not an HTTPS Broadcom-published source")
        if "broadcom" not in source["publisher"].lower():
            errors.append(f"{label} publisher does not identify Broadcom")
        try:
            date.fromisoformat(source["accessedOn"])
        except ValueError:
            errors.append(f"{label} accessedOn is not a real calendar date")
    return errors


def semantic_checks(
    artifact: dict[str, Any],
    inventory: dict[str, Any],
    snapshot: dict[str, Any],
    spec: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    products, clusters = index_inventory(inventory)

    if artifact["inventoryId"] != inventory["inventoryId"]:
        errors.append("artifact inventoryId does not match the supplied inventory")
    if artifact["compatibilitySnapshotId"] != snapshot["snapshotId"]:
        errors.append("artifact compatibilitySnapshotId does not match the pinned snapshot")
    if artifact["generatedBy"] != {
        "module": spec["module"]["name"],
        "moduleVersion": spec["module"]["version"],
        "sdkModules": spec["module"]["sdkModules"],
    }:
        errors.append("generatedBy does not match the installer module specification")

    errors.extend(check_research(artifact["research"]["consulted"]))

    architecture = artifact["architecture"]
    if architecture["targetRelease"] != snapshot["targetRelease"]:
        errors.append("architecture targetRelease does not match the pinned snapshot")
    if architecture["placements"] != snapshot["placementRules"]:
        errors.append("component placement/sizing differs from the pinned placement rules")
    errors.extend(check_capacity(architecture["placements"], clusters))

    expected_boundaries = {
        (path["sourceProduct"], path["sourceVersion"]): path["endOfGeneralSupport"]
        for path in snapshot["migrationPaths"]
    }
    actual_boundaries = {
        (item["sourceProduct"], item["sourceVersion"]): item["endOfGeneralSupport"]
        for item in artifact["supportBoundaries"]
    }
    if actual_boundaries != expected_boundaries:
        errors.append("support boundaries do not match the pinned product/version dates")

    plan = artifact["migrationPlan"]
    expected_order = snapshot["upgradeSequence"]
    if [step["stepId"] for step in plan] != expected_order:
        errors.append("migrationPlan step order differs from the pinned upgrade sequence")
    if [step["sequence"] for step in plan] != list(range(1, len(expected_order) + 1)):
        errors.append("migrationPlan sequence values must be contiguous and one-based")

    steps = {step["stepId"]: step for step in plan}
    expected_modes = {
        "patch-lifecycle-manager": "prerequisite-patch",
        "upgrade-operations": "in-place-upgrade",
        "upgrade-automation": "fleet-import-and-upgrade",
        "deploy-logs": "parallel-fresh-deployment",
        "cutover-logs": "parallel-content-and-data-cutover",
        "retire-legacy": "retire-after-validation",
    }
    expected_components = {
        "patch-lifecycle-manager": "platform-prerequisite",
        "upgrade-operations": "VCF Operations",
        "upgrade-automation": "VCF Automation",
        "deploy-logs": "VCF Operations for Logs",
        "cutover-logs": "VCF Operations for Logs",
        "retire-legacy": "VCF Operations for Logs",
    }
    for index, step_id in enumerate(expected_order):
        step = steps.get(step_id)
        if step is None:
            continue
        if step["migrationMode"] != expected_modes[step_id]:
            errors.append(f"{step_id} has the wrong migrationMode")
        if step["placementComponent"] != expected_components[step_id]:
            errors.append(f"{step_id} does not reference the required architecture placement")
        expected_dependencies = [] if index == 0 else [expected_order[index - 1]]
        if step["dependsOn"] != expected_dependencies:
            errors.append(f"{step_id} has incorrect dependsOn ordering")

    lifecycle = snapshot["lifecyclePrerequisite"]
    lifecycle_step = steps.get("patch-lifecycle-manager")
    if lifecycle_step:
        expected_source = {
            "instanceId": inventory["foundation"]["lifecycleManager"]["instanceId"],
            "product": lifecycle["sourceProduct"],
            "version": lifecycle["sourceVersion"],
        }
        expected_target = {
            "component": lifecycle["sourceProduct"],
            "version": lifecycle["targetVersion"],
        }
        if lifecycle_step["source"] != expected_source or lifecycle_step["target"] != expected_target:
            errors.append("lifecycle prerequisite source/target is incorrect")
        if exact_gate_ids(lifecycle_step) != lifecycle["requiredGateIds"]:
            errors.append("patch-lifecycle-manager gate IDs differ from the pinned snapshot")

    for path in snapshot["migrationPaths"]:
        source_product = path["sourceProduct"]
        inventory_product = products.get(source_product)
        if inventory_product is None:
            errors.append(f"snapshot source product {source_product!r} is absent from inventory")
            continue
        if inventory_product["version"] != path["sourceVersion"]:
            errors.append(f"inventory version for {source_product} differs from snapshot")

        selected_steps = [steps[step_id] for step_id in path["stepIds"] if step_id in steps]
        for step in selected_steps:
            expected_source = {
                "instanceId": inventory_product["instanceId"],
                "product": source_product,
                "version": path["sourceVersion"],
            }
            expected_target = {
                "component": path["targetComponent"],
                "version": path["targetVersion"],
            }
            if step["source"] != expected_source or step["target"] != expected_target:
                errors.append(f"{step['stepId']} source/target does not match the pinned migration path")

        carried = {
            item["itemId"]: item["method"]
            for step in selected_steps
            for item in step["carryForward"]
        }
        expected_carried = {item["itemId"]: item["method"] for item in path["carryForward"]}
        if carried != expected_carried:
            errors.append(f"carry-forward inventory for {source_product} is incomplete or incompatible")

        abandoned = {
            item["itemId"]: item["reasonCode"]
            for step in selected_steps
            for item in step["abandoned"]
        }
        expected_abandoned = {item["itemId"]: item["reasonCode"] for item in path["abandoned"]}
        if abandoned != expected_abandoned:
            errors.append(f"abandoned inventory for {source_product} is incomplete or incompatible")

        categories = inventory_content(products, source_product)
        for step in selected_steps:
            for item in step["carryForward"] + step["abandoned"]:
                if categories.get(item["itemId"]) != item["category"]:
                    errors.append(
                        f"{step['stepId']} category for {item['itemId']} differs from inventory"
                    )

        if "requiredGateIds" in path and selected_steps:
            if exact_gate_ids(selected_steps[0]) != path["requiredGateIds"]:
                errors.append(f"{selected_steps[0]['stepId']} gate IDs differ from the pinned snapshot")
        for step_id, gate_ids in path.get("requiredGateIdsByStep", {}).items():
            if step_id in steps and exact_gate_ids(steps[step_id]) != gate_ids:
                errors.append(f"{step_id} gate IDs differ from the pinned snapshot")

    return errors


def inspect_powershell_files(manifest: Path, implementation: Path) -> tuple[dict[str, Any] | None, str | None]:
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        return None, "pwsh is required to verify and execute the PowerShell deliverable"

    script = r"""
$ErrorActionPreference = 'Stop'
$manifest = Import-PowerShellDataFile -LiteralPath $env:VCFARCH_MANIFEST_PATH
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:VCFARCH_IMPLEMENTATION_PATH,
    [ref]$tokens,
    [ref]$parseErrors
)
$functions = @($ast.FindAll({
    param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst]
}, $true) | ForEach-Object Name)
$commands = @($ast.FindAll({
    param($node) $node -is [System.Management.Automation.Language.CommandAst]
}, $true) | ForEach-Object {
    [ordered]@{ name = $_.GetCommandName(); text = $_.Extent.Text }
})
$requiredModules = @($manifest.RequiredModules | ForEach-Object {
    if ($_ -is [string]) { $_ } else { $_.ModuleName }
})
[ordered]@{
    rootModule = [string]$manifest.RootModule
    moduleVersion = [string]$manifest.ModuleVersion
    functionsToExport = @($manifest.FunctionsToExport)
    requiredModules = $requiredModules
    functions = $functions
    commands = $commands
    parseErrors = @($parseErrors | ForEach-Object { $_.Message })
} | ConvertTo-Json -Depth 10 -Compress
"""
    environment = dict(os.environ)
    environment["VCFARCH_MANIFEST_PATH"] = str(manifest)
    environment["VCFARCH_IMPLEMENTATION_PATH"] = str(implementation)
    completed = subprocess.run(
        [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=20,
        env=environment,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        return None, f"PowerShell manifest/AST inspection failed: {detail}"
    try:
        return json.loads(completed.stdout), None
    except json.JSONDecodeError:
        return None, "PowerShell manifest/AST inspection returned invalid JSON"


def module_checks(spec: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    manifest = ROOT / spec["module"]["manifestPath"]
    implementation = ROOT / spec["module"]["implementationPath"]
    for path in (manifest, implementation):
        if not path.is_file():
            errors.append(f"missing PowerShell module file: {path.relative_to(ROOT)}")
    if errors:
        return errors

    inspection, inspection_error = inspect_powershell_files(manifest, implementation)
    if inspection_error:
        return [inspection_error]
    assert inspection is not None

    expected_root = Path(spec["module"]["implementationPath"]).name
    if inspection["rootModule"] != expected_root:
        errors.append("module manifest RootModule does not name the supplied implementation")
    if inspection["moduleVersion"] != spec["module"]["version"]:
        errors.append("module manifest version differs from the installer specification")
    if spec["module"]["exportedFunction"] not in inspection["functionsToExport"]:
        errors.append("module manifest does not export New-VcfMigrationPlan")
    if inspection["requiredModules"] != spec["module"]["sdkModules"]:
        errors.append("module manifest RequiredModules differs from the installer specification")
    if inspection["parseErrors"]:
        errors.append("PowerShell implementation has parse errors: " + "; ".join(inspection["parseErrors"]))

    function_names = {str(name).lower() for name in inspection["functions"]}
    for function_name in (spec["module"]["exportedFunction"], "Get-VcfSdkTopology"):
        if function_name.lower() not in function_names:
            errors.append(f"PowerShell implementation is missing function {function_name!r}")

    commands = inspection["commands"]
    command_names = {
        str(command["name"]).lower()
        for command in commands
        if command.get("name") is not None
    }
    for command_name in (
        "Import-Module",
        *spec["module"]["liveTopologyCmdlets"],
        "ConvertFrom-Json",
        "ConvertTo-Json",
    ):
        if command_name.lower() not in command_names:
            errors.append(f"PowerShell implementation is missing command {command_name!r}")

    import_extents = [
        command["text"]
        for command in commands
        if str(command.get("name", "")).lower() == "import-module"
    ]
    for sdk_module in spec["module"]["sdkModules"]:
        if not any(sdk_module.lower() in extent.lower() for extent in import_extents):
            errors.append(f"PowerShell implementation does not import {sdk_module!r}")

    module_dir = manifest.parent
    vendored = [
        path
        for path in module_dir.rglob("*")
        if path.is_file() and "vmware.sdk.vcf" in path.name.lower()
    ]
    if vendored:
        errors.append("VMware.Sdk.Vcf dependencies must not be vendored in the deliverable")
    return errors


GENERATOR_RUNNER = r"""
param(
    [Parameter(Mandatory)] [string] $ModulePath,
    [Parameter(Mandatory)] [string] $InventoryPath,
    [Parameter(Mandatory)] [string] $SnapshotPath,
    [Parameter(Mandatory)] [string] $SpecPath
)
$ErrorActionPreference = 'Stop'
Import-Module -Name $ModulePath -Force -ErrorAction Stop
New-VcfMigrationPlan `
    -InventoryPath $InventoryPath `
    -CompatibilitySnapshotPath $SnapshotPath `
    -InstallerSpecPath $SpecPath | Out-Null
"""


def run_generator(
    inventory: dict[str, Any], snapshot: dict[str, Any], spec: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        return None, "pwsh is required to execute New-VcfMigrationPlan"

    try:
        with tempfile.TemporaryDirectory(prefix="vcfarch-0159-") as temp_name:
            temp_root = Path(temp_name)
            module_source = ROOT / Path(spec["module"]["implementationPath"]).parent
            module_target = temp_root / module_source.name
            shutil.copytree(module_source, module_target)

            inventory_path = temp_root / "estate-inventory.json"
            snapshot_path = temp_root / "compatibility-snapshot.json"
            spec_path = temp_root / "installer-spec.json"
            runner_path = temp_root / "run-generator.ps1"
            inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
            snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            runner_path.write_text(GENERATOR_RUNNER, encoding="utf-8")

            implementation = module_target / Path(spec["module"]["implementationPath"]).name
            completed = subprocess.run(
                [
                    pwsh,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-File",
                    str(runner_path),
                    "-ModulePath",
                    str(implementation),
                    "-InventoryPath",
                    str(inventory_path),
                    "-SnapshotPath",
                    str(snapshot_path),
                    "-SpecPath",
                    str(spec_path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout).strip()
                return None, f"New-VcfMigrationPlan failed: {detail}"

            generated_path = temp_root / spec["artifactPath"]
            if not generated_path.is_file():
                return None, f"New-VcfMigrationPlan did not create {spec['artifactPath']}"
            try:
                return json.loads(generated_path.read_text(encoding="utf-8-sig")), None
            except json.JSONDecodeError as exc:
                return None, f"New-VcfMigrationPlan produced invalid JSON: {exc.msg}"
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"could not execute New-VcfMigrationPlan: {exc}"


def generator_checks(
    artifact: dict[str, Any],
    inventory: dict[str, Any],
    snapshot: dict[str, Any],
    spec: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    generated, generation_error = run_generator(inventory, snapshot, spec)
    if generation_error:
        return [generation_error]
    if generated != artifact:
        errors.append("checked-in artifact is not the output of New-VcfMigrationPlan")

    inventory_probe = deepcopy(inventory)
    inventory_probe["inventoryId"] = "vcfarch-probe-inventory"
    generated, generation_error = run_generator(inventory_probe, snapshot, spec)
    if generation_error:
        errors.append(f"inventory-input probe failed: {generation_error}")
    elif generated.get("inventoryId") != inventory_probe["inventoryId"]:
        errors.append("New-VcfMigrationPlan does not consume inventoryId from the inventory input")

    snapshot_probe = deepcopy(snapshot)
    snapshot_probe["snapshotId"] = "vcfarch-probe-snapshot"
    snapshot_probe["targetRelease"] = "99.99.99"
    generated, generation_error = run_generator(inventory, snapshot_probe, spec)
    if generation_error:
        errors.append(f"compatibility-input probe failed: {generation_error}")
    elif (
        generated.get("compatibilitySnapshotId") != snapshot_probe["snapshotId"]
        or generated.get("architecture", {}).get("targetRelease") != snapshot_probe["targetRelease"]
    ):
        errors.append("New-VcfMigrationPlan does not consume the compatibility snapshot input")

    spec_probe = deepcopy(spec)
    spec_probe["artifactPath"] = "generated/probe-plan.json"
    spec_probe["module"]["name"] = "VcfMigrationDesignProbe"
    spec_probe["module"]["version"] = "7.8.9"
    generated, generation_error = run_generator(inventory, snapshot, spec_probe)
    if generation_error:
        errors.append(f"installer-spec input probe failed: {generation_error}")
    elif generated.get("generatedBy", {}).get("module") != spec_probe["module"]["name"] or generated.get(
        "generatedBy", {}
    ).get("moduleVersion") != spec_probe["module"]["version"]:
        errors.append("New-VcfMigrationPlan does not consume the installer specification input")

    return errors


def print_errors(title: str, errors: list[str]) -> int:
    print(title, file=sys.stderr)
    for error in errors:
        print(f"- {error}", file=sys.stderr)
    return 1


def main() -> int:
    try:
        # Phase 1: the installer specification and artifact are the only inputs
        # touched before schema validation completes.
        spec = load_json(ROOT / "installer-spec.json")
        artifact_path = ROOT / spec["artifactPath"]
        artifact = load_json(artifact_path)
        schema_errors = validate_schema(artifact, spec["artifactSchema"])
        if schema_errors:
            return print_errors("artifact schema validation failed", schema_errors)
        print("artifact schema validation: PASS")

        # Phase 2: deterministic checks against the fixture and frozen authority.
        inventory = load_json(ROOT / "estate-inventory.json")
        snapshot = load_json(ROOT / "compatibility-snapshot.json")
        semantic_errors = semantic_checks(artifact, inventory, snapshot, spec)
        if semantic_errors:
            return print_errors("artifact semantic verification failed", semantic_errors)
        print("artifact fixture/snapshot verification: PASS")

        # Phase 3: implementation shape and external SDK dependency use.
        powershell_errors = module_checks(spec)
        if powershell_errors:
            return print_errors("PowerShell module verification failed", powershell_errors)
        print("PowerShell module verification: PASS")

        # Phase 4: execute the deterministic generator and verify provenance.
        generation_errors = generator_checks(artifact, inventory, snapshot, spec)
        if generation_errors:
            return print_errors("PowerShell generation verification failed", generation_errors)
        print("PowerShell generation verification: PASS")
        return 0
    except VerificationError as exc:
        return print_errors("verification could not start", [str(exc)])


if __name__ == "__main__":
    raise SystemExit(main())
