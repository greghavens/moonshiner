#!/usr/bin/env python3
"""Deterministic, offline acceptance verifier for vcfarch-0156."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
PROTECTED_HASHES = {
    "estate.inventory.json": "449ccbfd0551fb80802aba569e4dde581a40b4171c319668dce9099f11dabd8a",
    "compatibility.snapshot.json": "61d579b62795a982364c9aba013f56cbc02a62d9e5f8ecfe5ae035469c974553",
    "installer-spec.json": "a27383c376aaf18743a2c53ef4b783c6bda01646328bb33b9e060e324e496b8b",
}


class VerificationError(Exception):
    pass


def fail(message: str) -> None:
    raise VerificationError(message)


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"missing required file: {path.relative_to(ROOT)}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {path.relative_to(ROOT)}: {exc}")


def resolve_ref(root_schema: dict, ref: str) -> dict:
    if not ref.startswith("#/"):
        fail(f"schema uses unsupported external reference: {ref}")
    node = root_schema
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            fail(f"schema contains unresolved reference: {ref}")
        node = node[part]
    return node


def json_type_matches(value, expected: str) -> bool:
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
    fail(f"schema uses unsupported type: {expected}")


def validate_format(value: str, fmt: str, path: str) -> None:
    if fmt == "date":
        try:
            parsed = dt.date.fromisoformat(value)
        except ValueError:
            fail(f"schema validation failed at {path}: not an ISO date")
        if parsed.isoformat() != value:
            fail(f"schema validation failed at {path}: date is not canonical")
    elif fmt == "uri":
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            fail(f"schema validation failed at {path}: not an absolute HTTP(S) URI")


def validate_schema(instance, schema: dict, root_schema: dict, path: str = "$") -> None:
    """Validate the JSON Schema subset used by installer-spec.json."""
    if "$ref" in schema:
        validate_schema(instance, resolve_ref(root_schema, schema["$ref"]), root_schema, path)
        return

    if "const" in schema and instance != schema["const"]:
        fail(f"schema validation failed at {path}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        fail(f"schema validation failed at {path}: value is outside enum")

    expected_type = schema.get("type")
    if expected_type and not json_type_matches(instance, expected_type):
        fail(f"schema validation failed at {path}: expected {expected_type}")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        missing = [name for name in required if name not in instance]
        if missing:
            fail(f"schema validation failed at {path}: missing {', '.join(missing)}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = sorted(set(instance) - set(properties))
            if extra:
                fail(f"schema validation failed at {path}: unexpected {', '.join(extra)}")
        for name, value in instance.items():
            if name in properties:
                validate_schema(value, properties[name], root_schema, f"{path}.{name}")

    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            fail(f"schema validation failed at {path}: too few items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            fail(f"schema validation failed at {path}: too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(encoded) != len(set(encoded)):
                fail(f"schema validation failed at {path}: duplicate array items")
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(instance):
                validate_schema(item, item_schema, root_schema, f"{path}[{index}]")

    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            fail(f"schema validation failed at {path}: string is too short")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            fail(f"schema validation failed at {path}: pattern mismatch")
        if "format" in schema:
            validate_format(instance, schema["format"], path)

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            fail(f"schema validation failed at {path}: below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            fail(f"schema validation failed at {path}: above maximum")


def keyed(items: list[dict], field: str, label: str) -> dict[str, dict]:
    result = {}
    for item in items:
        key = item[field]
        if key in result:
            fail(f"duplicate {label} {key}")
        result[key] = item
    return result


def verify_protected_inputs() -> None:
    for name, expected in PROTECTED_HASHES.items():
        path = ROOT / name
        if not path.is_file():
            fail(f"missing protected input: {name}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            fail(f"protected input changed: {name}")


def verify_research(plan: dict) -> None:
    required_topics = {
        "migration-path",
        "content-compatibility",
        "support-boundary",
        "sizing",
        "sequencing",
    }
    actual_topics = set()
    urls = set()
    for source in plan["researchConsulted"]:
        parsed = urlparse(source["url"])
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (
            hostname == "broadcom.com" or hostname.endswith(".broadcom.com")
        ):
            fail("research sources must be Broadcom-published HTTPS pages")
        if source["url"] in urls:
            fail("researchConsulted contains a duplicate source URL")
        urls.add(source["url"])
        if "broadcom" not in source["publisher"].lower():
            fail("research source publisher must identify Broadcom")
        actual_topics.update(source["topics"])
    missing_topics = sorted(required_topics - actual_topics)
    if missing_topics:
        fail(f"researchConsulted does not cover: {', '.join(missing_topics)}")


def verify_architecture(plan: dict, inventory: dict, snapshot: dict) -> None:
    expected_components = keyed(
        snapshot["targetArchitecture"]["components"], "componentId", "snapshot component"
    )
    actual_components = keyed(plan["architecture"]["components"], "componentId", "component")
    if set(actual_components) != set(expected_components):
        fail("architecture component set does not match the pinned snapshot")

    for component_id, expected in expected_components.items():
        actual = actual_components[component_id]
        for field in (
            "component",
            "version",
            "migrationMode",
            "placement",
            "sizing",
            "capacityBasis",
        ):
            if actual[field] != expected[field]:
                fail(f"{component_id} {field} does not match the pinned snapshot")
        sizing = actual["sizing"]
        if len(actual["placement"]["nodeFaultDomains"]) != sizing["nodeCount"]:
            fail(f"{component_id} does not assign every node to a fault domain")

    computed = {"vCpu": 0, "memoryGb": 0, "storageGb": 0}
    for component in actual_components.values():
        sizing = component["sizing"]
        computed["vCpu"] += sizing["nodeCount"] * sizing["vCpuPerNode"]
        computed["memoryGb"] += sizing["nodeCount"] * sizing["memoryGbPerNode"]
        computed["storageGb"] += sizing["nodeCount"] * sizing["diskGbPerNode"]

    pinned_totals = snapshot["targetArchitecture"]["resourceTotals"]
    if computed != pinned_totals:
        fail(f"pinned component sizes do not total correctly: {computed!r}")
    totals = plan["architecture"]["resourceTotals"]
    if totals["required"] != computed:
        fail("architecture required resource totals are incorrect")
    available = inventory["managementPlane"]["availableCapacity"]
    if totals["available"] != available:
        fail("architecture available capacity does not match inventory")
    remaining = {key: available[key] - computed[key] for key in computed}
    if any(value < 0 for value in remaining.values()):
        fail("target architecture exceeds management-domain capacity")
    if totals["remaining"] != remaining:
        fail("architecture remaining capacity is incorrect")


def verify_content_and_boundaries(plan: dict, inventory: dict, snapshot: dict) -> None:
    inventory_products = keyed(inventory["sourceProducts"], "id", "inventory product")
    rules = keyed(snapshot["sourceRules"], "sourceProductId", "source rule")
    dispositions = keyed(plan["contentDisposition"], "sourceProductId", "content disposition")
    boundaries = keyed(plan["supportBoundaries"], "sourceProductId", "support boundary")
    expected_ids = set(inventory_products)
    if set(rules) != expected_ids or set(dispositions) != expected_ids or set(boundaries) != expected_ids:
        fail("every inventory source product must have exactly one rule, disposition and support boundary")

    for product_id in sorted(expected_ids):
        inventory_product = inventory_products[product_id]
        rule = rules[product_id]
        disposition = dispositions[product_id]
        for field in ("sourceProduct", "sourceVersion", "targetComponent", "targetVersion"):
            if disposition[field] != rule[field]:
                fail(f"{product_id} disposition has wrong {field}")
        if disposition["sourceProduct"] != inventory_product["product"]:
            fail(f"{product_id} source product name differs from inventory")
        if disposition["sourceVersion"] != inventory_product["version"]:
            fail(f"{product_id} source version differs from inventory")
        if disposition["transitions"] != rule["transitions"]:
            fail(f"{product_id} migration transitions differ from pinned compatibility")

        inventory_items = keyed(inventory_product["content"], "id", f"{product_id} inventory item")
        content_rules = keyed(rule["contentRules"], "inventoryId", f"{product_id} content rule")
        actual_items = keyed(disposition["items"], "inventoryId", f"{product_id} plan item")
        if set(inventory_items) != set(content_rules) or set(actual_items) != set(inventory_items):
            fail(f"{product_id} must account for every inventoried content item exactly once")
        for item_id, item in actual_items.items():
            if item["kind"] != inventory_items[item_id]["kind"]:
                fail(f"{item_id} kind differs from inventory")
            if item["disposition"] != content_rules[item_id]["disposition"]:
                fail(f"{item_id} has the wrong compatibility disposition")
            if item["destination"] != content_rules[item_id]["destination"]:
                fail(f"{item_id} has the wrong target destination")

        boundary = boundaries[product_id]
        expected_boundary = {
            "sourceProductId": product_id,
            "sourceProduct": rule["sourceProduct"],
            "sourceVersion": rule["sourceVersion"],
            "endOfGeneralSupport": rule["endOfGeneralSupport"],
            "targetReleaseDate": snapshot["targetReleaseDate"],
            "boundary": rule["supportBoundary"],
        }
        for field, expected in expected_boundary.items():
            if boundary[field] != expected:
                fail(f"{product_id} support boundary has wrong {field}")

    if not any(item["boundary"] == "ends-before-target-release" for item in boundaries.values()):
        fail("plan misses the source whose support ends before the target release")


def target_id_for(name: str) -> str:
    mapping = {
        "VCF 9.0.2 management platform": "platform",
        "VMware Aria Suite Lifecycle": "platform",
        "VMware Aria Operations": "ops-prod",
        "VCF Operations cloud proxy": "vcf-operations",
        "VCF Operations": "vcf-operations",
        "VCF Automation": "vcf-automation",
        "VCF Operations for Logs": "vcf-operations-logs",
    }
    try:
        return mapping[name]
    except KeyError:
        fail(f"snapshot has unmapped target component {name}")


def verify_steps(plan: dict, snapshot: dict) -> None:
    expected_steps = snapshot["requiredSteps"]
    actual_steps = plan["steps"]
    if [step["order"] for step in actual_steps] != sorted(step["order"] for step in actual_steps):
        fail("migration steps are not ordered")
    if len(actual_steps) != len(expected_steps):
        fail("migration plan must contain exactly the pinned sequence")
    actual_by_id = keyed(actual_steps, "stepId", "migration step")
    if set(actual_by_id) != {step["stepId"] for step in expected_steps}:
        fail("migration step set differs from the pinned sequence")

    for expected in expected_steps:
        actual = actual_by_id[expected["stepId"]]
        scalar_expectations = {
            "order": expected["order"],
            "action": expected["action"],
            "method": expected["method"],
            "dependsOn": expected["dependsOn"],
            "carries": expected["carryIds"],
            "abandons": expected["abandonIds"],
        }
        for field, value in scalar_expectations.items():
            if actual[field] != value:
                fail(f"{expected['stepId']} has wrong {field}")
        expected_source = {
            "productId": expected["sourceProductId"],
            "product": expected["sourceProduct"],
            "version": expected["sourceVersion"],
        }
        expected_target = {
            "productId": target_id_for(expected["targetComponent"]),
            "product": expected["targetComponent"],
            "version": expected["targetVersion"],
        }
        if actual["source"] != expected_source or actual["target"] != expected_target:
            fail(f"{expected['stepId']} has wrong source or target endpoint")
        gate_ids = [gate["gateId"] for gate in actual["gates"]]
        if gate_ids != expected["requiredGateIds"]:
            fail(f"{expected['stepId']} gates differ from the pinned sequence")

    completed = set()
    for step in actual_steps:
        if not set(step["dependsOn"]).issubset(completed):
            fail(f"{step['stepId']} depends on a step that has not completed")
        completed.add(step["stepId"])


def verify_generated_plan(
    generated: dict, spec: dict, inventory: dict, snapshot: dict, label: str
) -> None:
    validate_schema(generated, spec["schema"], spec["schema"])
    if generated["estateId"] != inventory["estateId"]:
        fail(f"{label} does not consume InventoryPath")
    if generated["snapshotId"] != snapshot["snapshotId"]:
        fail(f"{label} does not consume CompatibilityPath")
    if generated["targetVersion"] != snapshot["targetVersion"]:
        fail(f"{label} has the wrong target version")
    if generated["sdkModules"] != snapshot["requiredSdkModules"]:
        fail(f"{label} has the wrong SDK prerequisites")
    verify_research(generated)
    verify_architecture(generated, inventory, snapshot)
    verify_content_and_boundaries(generated, inventory, snapshot)
    verify_steps(generated, snapshot)


def verify_module(spec: dict, plan_path: Path, inventory: dict, snapshot: dict) -> None:
    manifest = ROOT / spec["module"]["manifestPath"]
    root_module = ROOT / spec["module"]["rootModulePath"]
    if not manifest.is_file() or not root_module.is_file():
        fail("PowerShell module manifest or root module is missing")
    manifest_text = manifest.read_text(encoding="utf-8")
    source_text = root_module.read_text(encoding="utf-8")

    for module_name in spec["module"]["requiredModules"]:
        if manifest_text.count(module_name) != 1:
            fail(f"manifest must declare {module_name} exactly once")
        if not re.search(rf"Import-Module\s+['\"]?{re.escape(module_name)}", source_text, re.I):
            fail(f"New-VcfMigrationPlan must load prerequisite {module_name}")
    for function_name in spec["module"]["exportedFunctions"]:
        if not re.search(rf"function\s+{re.escape(function_name)}\b", source_text, re.I):
            fail(f"missing exported function {function_name}")
        if function_name not in manifest_text:
            fail(f"manifest does not export {function_name}")
    for parameter in (
        "InventoryPath",
        "CompatibilityPath",
        "SpecificationPath",
        "ResearchConsulted",
        "OutputPath",
    ):
        if not re.search(rf"\${parameter}\b", source_text):
            fail(f"module does not implement required parameter {parameter}")
    for operation in ("ConvertFrom-Json", "ConvertTo-Json", "Test-Json"):
        if operation not in source_text:
            fail(f"module is missing required JSON operation {operation}")

    # The harness installs declared prerequisites below .sandbox-home.  Inspect only
    # submission-owned paths so those genuine runtime modules are not called vendored.
    harness_roots = {".git", ".moonshiner", ".sandbox-home"}
    vendored = []
    for path in ROOT.rglob("VMware.Sdk.Vcf*"):
        relative = path.relative_to(ROOT)
        if relative.parts and relative.parts[0] in harness_roots:
            continue
        if path.is_dir() or path.suffix.lower() in {".psd1", ".psm1", ".dll"}:
            vendored.append(path)
    if vendored:
        fail(f"VMware.Sdk.Vcf dependency was vendored: {vendored[0].relative_to(ROOT)}")

    pwsh = shutil.which("pwsh")
    if not pwsh:
        fail("pwsh is required to validate the PowerShell module")
    powershell_environment = os.environ.copy()
    powershell_environment["VCF_MIGRATION_MANIFEST"] = str(manifest)
    powershell_environment["VCF_MIGRATION_MODULE"] = str(root_module)
    parse_script = r"""
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
[void] [System.Management.Automation.Language.Parser]::ParseFile(
    $env:VCF_MIGRATION_MODULE, [ref] $tokens, [ref] $errors)
if ($errors.Count) { throw $errors[0].Message }
$manifestData = Import-PowerShellDataFile -LiteralPath $env:VCF_MIGRATION_MANIFEST
$requiredModules = @($manifestData.RequiredModules | ForEach-Object {
    if ($_ -is [string]) { $_ }
    elseif ($_.ModuleName) { [string] $_.ModuleName }
})
[ordered] @{
    rootModule = [string] $manifestData.RootModule
    requiredModules = $requiredModules
    functionsToExport = @($manifestData.FunctionsToExport)
} | ConvertTo-Json -Depth 4 -Compress
"""
    parsed = subprocess.run(
        [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", parse_script],
        cwd=ROOT,
        env=powershell_environment,
        text=True,
        capture_output=True,
        timeout=30,
    )
    if parsed.returncode != 0:
        detail = (parsed.stderr or parsed.stdout).strip().splitlines()
        fail(f"PowerShell artifact inspection failed: {detail[-1] if detail else 'unknown error'}")
    try:
        manifest_data = json.loads(parsed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        fail("PowerShell manifest inspection returned malformed output")
    if manifest_data["rootModule"] != root_module.name:
        fail("manifest RootModule does not name the supplied root module")
    if manifest_data["requiredModules"] != spec["module"]["requiredModules"]:
        fail("manifest RequiredModules does not exactly match installer-spec.json")
    if manifest_data["functionsToExport"] != spec["module"]["exportedFunctions"]:
        fail("manifest FunctionsToExport does not exactly match installer-spec.json")

    powershell_environment["VCF_MIGRATION_PLAN"] = str(plan_path)
    powershell_environment["VCF_MIGRATION_SPEC"] = str(ROOT / "installer-spec.json")
    test_script = r"""
$ErrorActionPreference = 'Stop'
Import-Module $env:VCF_MIGRATION_MODULE -Force
if (-not (Test-VcfMigrationPlan -PlanPath $env:VCF_MIGRATION_PLAN -SpecificationPath $env:VCF_MIGRATION_SPEC)) {
    throw 'Test-VcfMigrationPlan returned false'
}
if (Test-VcfMigrationPlan -PlanPath $env:VCF_MIGRATION_SPEC `
        -SpecificationPath $env:VCF_MIGRATION_SPEC -ErrorAction SilentlyContinue) {
    throw 'Test-VcfMigrationPlan accepted an invalid plan'
}
"""
    tested = subprocess.run(
        [
            pwsh,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            test_script,
        ],
        cwd=ROOT,
        env=powershell_environment,
        text=True,
        capture_output=True,
        timeout=30,
    )
    if tested.returncode != 0:
        fail(f"Test-VcfMigrationPlan rejected the artifact: {tested.stderr.strip()}")

    with tempfile.TemporaryDirectory(prefix="vcf-migration-verifier-") as temp_name:
        temp = Path(temp_name)
        first_output = temp / "first.json"
        second_output = temp / "second.json"
        variant_output = temp / "variant.json"
        variant_inventory_path = temp / "estate.variant.json"
        variant_snapshot_path = temp / "compatibility.variant.json"

        variant_inventory = json.loads(json.dumps(inventory))
        variant_inventory["estateId"] = "northstar-generator-probe"
        for resource in ("vCpu", "memoryGb", "storageGb"):
            variant_inventory["managementPlane"]["availableCapacity"][resource] += 1
        variant_snapshot = json.loads(json.dumps(snapshot))
        variant_snapshot["snapshotId"] = "generator-probe-snapshot"
        variant_inventory_path.write_text(
            json.dumps(variant_inventory, indent=2) + "\n", encoding="utf-8"
        )
        variant_snapshot_path.write_text(
            json.dumps(variant_snapshot, indent=2) + "\n", encoding="utf-8"
        )

        generation_environment = powershell_environment.copy()
        generation_environment.update(
            {
                "VCF_MIGRATION_INVENTORY": str(ROOT / "estate.inventory.json"),
                "VCF_MIGRATION_COMPATIBILITY": str(ROOT / "compatibility.snapshot.json"),
                "VCF_MIGRATION_FIRST": str(first_output),
                "VCF_MIGRATION_SECOND": str(second_output),
                "VCF_MIGRATION_VARIANT_INVENTORY": str(variant_inventory_path),
                "VCF_MIGRATION_VARIANT_COMPATIBILITY": str(variant_snapshot_path),
                "VCF_MIGRATION_VARIANT_OUTPUT": str(variant_output),
            }
        )
        generation_script = r"""
$ErrorActionPreference = 'Stop'
Import-Module $env:VCF_MIGRATION_MODULE -Force
$research = @((Get-Content -LiteralPath $env:VCF_MIGRATION_PLAN -Raw -Encoding utf8 |
    ConvertFrom-Json -Depth 100).researchConsulted)
$common = @{
    InventoryPath = $env:VCF_MIGRATION_INVENTORY
    CompatibilityPath = $env:VCF_MIGRATION_COMPATIBILITY
    SpecificationPath = $env:VCF_MIGRATION_SPEC
    ResearchConsulted = $research
}
$null = New-VcfMigrationPlan @common -OutputPath $env:VCF_MIGRATION_FIRST
$null = New-VcfMigrationPlan @common -OutputPath $env:VCF_MIGRATION_SECOND
$null = New-VcfMigrationPlan `
    -InventoryPath $env:VCF_MIGRATION_VARIANT_INVENTORY `
    -CompatibilityPath $env:VCF_MIGRATION_VARIANT_COMPATIBILITY `
    -SpecificationPath $env:VCF_MIGRATION_SPEC `
    -ResearchConsulted $research `
    -OutputPath $env:VCF_MIGRATION_VARIANT_OUTPUT
"""
        generated = subprocess.run(
            [
                pwsh,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                generation_script,
            ],
            cwd=ROOT,
            env=generation_environment,
            text=True,
            capture_output=True,
            timeout=90,
        )
        if generated.returncode != 0:
            detail = (generated.stderr or generated.stdout).strip().splitlines()
            fail(f"New-VcfMigrationPlan failed: {detail[-1] if detail else 'unknown error'}")
        if first_output.read_bytes() != second_output.read_bytes():
            fail("New-VcfMigrationPlan is not deterministic for identical inputs")
        try:
            first_plan = json.loads(first_output.read_text(encoding="utf-8-sig"))
            variant_plan = json.loads(variant_output.read_text(encoding="utf-8-sig"))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            fail(f"New-VcfMigrationPlan did not write valid JSON: {exc}")
        verify_generated_plan(first_plan, spec, inventory, snapshot, "generated plan")
        verify_generated_plan(
            variant_plan, spec, variant_inventory, variant_snapshot, "variant generated plan"
        )
        verify_protected_inputs()


def main() -> int:
    # Contract order is intentional: schema validation happens before protected-hash,
    # compatibility, inventory, module, or semantic checks.
    spec = load_json(ROOT / "installer-spec.json")
    plan_path = ROOT / spec.get("artifactPath", "migration-plan.json")
    plan = load_json(plan_path)
    validate_schema(plan, spec["schema"], spec["schema"])

    verify_protected_inputs()
    inventory = load_json(ROOT / "estate.inventory.json")
    snapshot = load_json(ROOT / "compatibility.snapshot.json")

    if plan["estateId"] != inventory["estateId"]:
        fail("plan estateId does not match inventory")
    if plan["snapshotId"] != snapshot["snapshotId"]:
        fail("plan snapshotId does not match pinned snapshot")
    if plan["targetVersion"] != snapshot["targetVersion"]:
        fail("plan target version does not match pinned snapshot")
    if plan["sdkModules"] != snapshot["requiredSdkModules"]:
        fail("plan SDK modules do not match the required VMware.Sdk.Vcf modules")

    verify_research(plan)
    verify_architecture(plan, inventory, snapshot)
    verify_content_and_boundaries(plan, inventory, snapshot)
    verify_steps(plan, snapshot)
    verify_module(spec, plan_path, inventory, snapshot)

    print("PASS: migration architecture matches schema, inventory, and pinned compatibility snapshot")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
