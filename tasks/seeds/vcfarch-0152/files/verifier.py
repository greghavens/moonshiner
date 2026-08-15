#!/usr/bin/env python3
"""Deterministic verifier for the VCF Aria migration architecture seed."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
PROTECTED = (
    ROOT / "estate-inventory.json",
    ROOT / "compatibility-snapshot.json",
    ROOT / "installer-spec.schema.json",
    Path(__file__).resolve(),
)
FIXTURE_HASHES = {
    ROOT / "estate-inventory.json": "63ad3b04bf42bd944967bec85706fed9336db3f818f5c965400123b41a5fdb93",
    ROOT / "compatibility-snapshot.json": "84476c862262b7033db681a2b17edb4de4f5214c130e41129b0a1a7e5bcef1ff",
    ROOT / "installer-spec.schema.json": "7972722e07846d6459072f67e78302b16f7cef57fd2bb4e2970f371a7aecf2d4",
}


class VerificationError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VerificationError(f"missing required file: {path.relative_to(ROOT)}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(f"invalid JSON in {path.relative_to(ROOT)}: {exc}") from exc


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_fixture_hashes() -> None:
    for path, expected in FIXTURE_HASHES.items():
        require(digest(path) == expected, f"protected input was modified: {path.name}")


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
    return True


def validate_schema(instance: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Validate every JSON Schema keyword used by installer-spec.schema.json."""
    errors: list[str] = []
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: value {instance!r} is not in {schema['enum']!r}")

    expected_type = schema.get("type")
    if expected_type and not json_type_matches(instance, expected_type):
        errors.append(f"{path}: expected {expected_type}, got {type(instance).__name__}")
        return errors

    if isinstance(instance, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing required property {key!r}")
        if schema.get("additionalProperties") is False:
            for key in instance:
                if key not in properties:
                    errors.append(f"{path}: additional property {key!r} is not allowed")
        for key, child_schema in properties.items():
            if key in instance:
                errors.extend(validate_schema(instance[key], child_schema, f"{path}.{key}"))

    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            errors.append(f"{path}: expected at least {schema['minItems']} items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: expected at most {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(encoded) != len(set(encoded)):
                errors.append(f"{path}: array items must be unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(instance):
                errors.extend(validate_schema(item, item_schema, f"{path}[{index}]"))

    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            errors.append(f"{path}: string is shorter than {schema['minLength']}")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            errors.append(f"{path}: string does not match {schema['pattern']!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: value is below minimum {schema['minimum']}")
    return errors


def by_key(items: list[dict[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    values = [item.get(key) for item in items]
    require(all(isinstance(value, str) and value for value in values), f"{label} contains a missing {key}")
    require(len(values) == len(set(values)), f"{label} contains duplicate {key} values")
    return {item[key]: item for item in items}


def verify_support_boundaries(plan: dict[str, Any], snapshot: dict[str, Any]) -> None:
    actual = by_key(plan["supportBoundaries"], "sourceId", "support boundaries")
    expected = by_key(snapshot["productRules"], "sourceId", "snapshot product rules")
    require(set(actual) == set(expected), "support boundaries must cover every and only the inventoried source products")
    fields = (
        "sourceId",
        "sourceProduct",
        "sourceVersion",
        "targetComponent",
        "targetVersion",
        "transitionMode",
        "endOfGeneralSupport",
    )
    for source_id, rule in expected.items():
        require(actual[source_id] == {field: rule[field] for field in fields}, f"{source_id}: source-to-target or support-boundary mapping differs from the pinned snapshot")


def verify_topology_and_sizing(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    topology = plan["topology"]
    domain = inventory["foundation"]["managementDomain"]
    expected_hosts = [host["id"] for host in domain["hosts"]]
    minimum_hosts = snapshot["minimumSupportedManagementHostCount"]
    require(topology["siteId"] == inventory["site"]["id"], "topology site does not match inventory")
    require(topology["deploymentModel"] == inventory["foundation"]["deploymentModel"], "topology deployment model does not match inventory")
    actual_domain = topology["managementDomain"]
    require(actual_domain["name"] == domain["name"], "management-domain name does not match inventory")
    require(actual_domain["cluster"] == domain["cluster"], "management-domain cluster does not match inventory")
    require(actual_domain["storage"] == domain["storage"], "management-domain storage does not match inventory")
    require(actual_domain["hostCount"] == minimum_hosts == len(expected_hosts), "design must use exactly the pinned minimum host count")
    require(len(actual_domain["hosts"]) == len(expected_hosts) and set(actual_domain["hosts"]) == set(expected_hosts), "management-domain hosts must exactly match inventory")

    actual_targets = by_key(plan["targetComponents"], "component", "target components")
    expected_targets = by_key(snapshot["targetSizing"], "component", "snapshot target sizing")
    require(set(actual_targets) == set(expected_targets), "target sizing must contain exactly the three pinned components")
    placement_hosts: dict[str, list[str]] = {}
    for component, sizing in expected_targets.items():
        target = actual_targets[component]
        expected_fields = (
            "deploymentModel",
            "preset",
            "nodeCount",
            "vcpuPerNode",
            "memoryGbPerNode",
            "dataDiskGbPerNode",
        )
        require(target["version"] == snapshot["targetRelease"], f"{component}: target version differs from snapshot")
        for field in expected_fields:
            require(target[field] == sizing[field], f"{component}: {field} differs from pinned sizing")
        placement = target["placement"]
        require(len(placement) == target["nodeCount"], f"{component}: every node must have exactly one placement")
        nodes = [entry["node"] for entry in placement]
        hosts = [entry["host"] for entry in placement]
        require(len(nodes) == len(set(nodes)), f"{component}: placement contains duplicate node names")
        require(all(host in expected_hosts for host in hosts), f"{component}: placement uses a host outside the management domain")
        placement_hosts[component] = hosts
    require(len(set(placement_hosts["VCF Operations for Logs"])) == expected_targets["VCF Operations for Logs"]["nodeCount"], "Logs nodes must be placed on distinct management hosts")

    target_vcpu = sum(item["nodeCount"] * item["vcpuPerNode"] for item in snapshot["targetSizing"])
    target_memory = sum(item["nodeCount"] * item["memoryGbPerNode"] for item in snapshot["targetSizing"])
    target_storage = sum(item["nodeCount"] * item["dataDiskGbPerNode"] for item in snapshot["targetSizing"])
    source_logs = next(product for product in inventory["sourceProducts"] if product["id"] == "aria-logs")
    expected_peak = {
        "vcpu": target_vcpu + source_logs["deployment"]["nodes"] * source_logs["deployment"]["vcpuPerNode"],
        "memoryGb": target_memory + source_logs["deployment"]["nodes"] * source_logs["deployment"]["memoryGbPerNode"],
        "storageGb": target_storage + source_logs["deployment"]["dataStorageGb"],
        "includesParallelLogs": True,
    }
    require(topology["peakSuiteDemand"] == expected_peak, "peak suite demand must include the complete target plus the parallel source Logs cluster")

    core_load = domain["existingCoreManagementLoad"]
    host_capacity = {
        "vcpu": sum(host["physicalCores"] for host in domain["hosts"]),
        "memoryGb": sum(host["memoryGb"] for host in domain["hosts"]),
        "storageGb": domain["usableStorageGb"],
    }
    for resource in ("vcpu", "memoryGb", "storageGb"):
        require(core_load[resource] + expected_peak[resource] <= host_capacity[resource], f"coexistence design exceeds supplied four-host {resource} capacity")


def verify_steps(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    steps = plan["steps"]
    action_rules = snapshot["requiredActions"]
    require(len(steps) == len(action_rules), "migration must contain exactly one step for every required action")
    orders = [step["order"] for step in steps]
    require(orders == sorted(orders) and len(orders) == len(set(orders)), "migration step order values must be strictly increasing")
    require([step["action"] for step in steps] == [rule["action"] for rule in action_rules], "migration actions are not in the pinned dependency order")
    step_ids = [step["id"] for step in steps]
    require(len(step_ids) == len(set(step_ids)), "migration step ids must be unique")
    step_by_action = {step["action"]: step for step in steps}

    products = by_key(inventory["sourceProducts"], "id", "inventory source products")
    product_rules = by_key(snapshot["productRules"], "sourceId", "snapshot product rules")
    component_action_sources = {
        "upgrade-operations": {"aria-operations"},
        "remediate-automation": {"aria-automation"},
        "import-automation": {"aria-automation"},
        "upgrade-automation": {"aria-automation"},
        "deploy-logs": {"aria-logs"},
        "reconfigure-and-transfer-logs": {"aria-logs"},
    }
    content_source: dict[str, str] = {}
    inventory_content: set[str] = set()
    for source_id, product in products.items():
        for item in product["content"]:
            require(item["id"] not in inventory_content, f"inventory contains duplicate content id {item['id']}")
            inventory_content.add(item["id"])
            content_source[item["id"]] = source_id

    dispositions: dict[str, tuple[str, dict[str, Any], str]] = {}
    for step, rule in zip(steps, action_rules):
        expected_dependencies = [step_by_action[action]["id"] for action in rule["dependsOn"]]
        require(step["dependsOn"] == expected_dependencies, f"{step['id']}: dependencies differ from the pinned action graph")
        gate_ids = [gate["id"] for gate in step["gates"]]
        require(len(gate_ids) == len(set(gate_ids)), f"{step['id']}: gate ids must be unique within a step")
        require(set(rule["gates"]).issubset(gate_ids), f"{step['id']}: a pinned required gate is missing")

        actual_source_ids = [source["sourceId"] for source in step["sources"]]
        require(len(actual_source_ids) == len(set(actual_source_ids)), f"{step['id']}: source products are duplicated")
        if step["action"] in component_action_sources:
            require(set(actual_source_ids) == component_action_sources[step["action"]], f"{step['id']}: component-specific action names the wrong source product")
        for source in step["sources"]:
            require(source["sourceId"] in products, f"{step['id']}: source product is not in inventory")
            product = products[source["sourceId"]]
            require(source == {"sourceId": product["id"], "product": product["product"], "version": product["version"]}, f"{step['id']}: source product name or version differs from inventory")
        expected_targets = {product_rules[source_id]["targetComponent"] for source_id in actual_source_ids}
        require(set(step["targets"]) == expected_targets and len(step["targets"]) == len(expected_targets), f"{step['id']}: target components do not match its sources")

        for disposition, field in (("carry", "carries"), ("abandon", "abandons")):
            for item in step[field]:
                content_id = item["contentId"]
                require(content_id not in dispositions, f"content item {content_id} is dispositioned more than once")
                require(content_id in content_source, f"unknown content item {content_id} is dispositioned")
                require(content_source[content_id] in actual_source_ids, f"{step['id']}: content {content_id} does not belong to a named source")
                dispositions[content_id] = (disposition, item, step["id"])

    named_sources = {source["sourceId"] for step in steps for source in step["sources"]}
    require(named_sources == set(products), "ordered plan must include every inventoried source product")

    rules = by_key(snapshot["contentRules"], "contentId", "snapshot content rules")
    require(set(rules) == inventory_content, "pinned content rules must cover the complete inventory")
    require(set(dispositions) == inventory_content, "every inventoried content item must be dispositioned exactly once")
    for content_id, rule in rules.items():
        disposition, item, _ = dispositions[content_id]
        require(disposition == rule["disposition"], f"{content_id}: carry/abandon disposition differs from snapshot")
        require(item["method"] == rule["method"], f"{content_id}: disposition method differs from snapshot")
        if disposition == "abandon":
            require(item["reasonCode"] == rule["reasonCode"], f"{content_id}: abandonment reason code differs from snapshot")


def powershell(command: str, env: dict[str, str], timeout: int = 25) -> subprocess.CompletedProcess[str]:
    pwsh = shutil.which("pwsh")
    require(pwsh is not None, "pwsh 7.2 or later is required")
    result = subprocess.run(
        [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=ROOT,
        env={**os.environ, **env},
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return result


def verify_module(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    module_dir = ROOT / "VcfAriaMigration"
    manifest_path = module_dir / "VcfAriaMigration.psd1"
    implementation_path = module_dir / "VcfAriaMigration.psm1"
    require(manifest_path.is_file(), "missing VcfAriaMigration/VcfAriaMigration.psd1")
    require(implementation_path.is_file(), "missing VcfAriaMigration/VcfAriaMigration.psm1")
    allowed = {manifest_path.resolve(), implementation_path.resolve()}
    extras = [path for path in module_dir.rglob("*") if path.is_file() and path.resolve() not in allowed]
    require(not extras, "module directory contains vendored or unexpected files")

    manifest_result = powershell(
        "$ErrorActionPreference='Stop';"
        "$data=Import-PowerShellDataFile -LiteralPath $env:VCF_MANIFEST;"
        "$data|ConvertTo-Json -Depth 20 -Compress",
        {"VCF_MANIFEST": str(manifest_path)},
    )
    require(manifest_result.returncode == 0, "PowerShell rejected the module manifest: " + (manifest_result.stderr.strip() or manifest_result.stdout.strip()))
    try:
        manifest = json.loads(manifest_result.stdout)
    except json.JSONDecodeError as exc:
        raise VerificationError("module manifest did not evaluate to structured data") from exc
    required_modules_raw = manifest.get("RequiredModules", [])
    if isinstance(required_modules_raw, str):
        required_modules = [required_modules_raw]
    else:
        required_modules = [item if isinstance(item, str) else item.get("ModuleName") for item in required_modules_raw]
    exports_raw = manifest.get("FunctionsToExport", [])
    exports = [exports_raw] if isinstance(exports_raw, str) else list(exports_raw)
    require(manifest.get("RootModule") == "VcfAriaMigration.psm1", "manifest RootModule is incorrect")
    require(set(required_modules) == set(snapshot["requiredSdkModules"]) and len(required_modules) == len(snapshot["requiredSdkModules"]), "manifest must declare exactly the two pinned VMware SDK dependencies")
    require(exports == ["New-VcfAriaMigrationInstallerSpec"], "manifest must export exactly New-VcfAriaMigrationInstallerSpec")

    ast_check = (
        "$ErrorActionPreference='Stop';$tokens=$null;$errors=$null;"
        "$ast=[System.Management.Automation.Language.Parser]::ParseFile($env:VCF_IMPL,[ref]$tokens,[ref]$errors);"
        "if($errors.Count){$errors|ForEach-Object{Write-Error $_.Message};exit 1};"
        "$functions=@($ast.FindAll({param($n)$n -is [System.Management.Automation.Language.FunctionDefinitionAst]},$true));"
        "$generator=@($functions|Where-Object Name -CEQ 'New-VcfAriaMigrationInstallerSpec');"
        "if($generator.Count -ne 1){Write-Error 'generator function missing or duplicated';exit 1};"
        "$params=@($generator[0].Body.ParamBlock.Parameters|ForEach-Object{$_.Name.VariablePath.UserPath});"
        "$required=@('InventoryPath','CompatibilitySnapshotPath','SchemaPath','OutputPath');"
        "$missing=@($required|Where-Object{$_ -notin $params});"
        "if($missing.Count){Write-Error 'generator is missing a required parameter';exit 1};"
        "$exports=@($ast.FindAll({param($n)$n -is [System.Management.Automation.Language.CommandAst] -and $n.GetCommandName() -eq 'Export-ModuleMember'},$true));"
        "if($exports.Count -ne 1){Write-Error 'Export-ModuleMember missing or duplicated';exit 1}"
    )
    ast_result = powershell(ast_check, {"VCF_IMPL": str(implementation_path)})
    require(ast_result.returncode == 0, "PowerShell parser rejected the module implementation: " + (ast_result.stderr.strip() or ast_result.stdout.strip()))

    with tempfile.TemporaryDirectory(prefix="vcf-aria-verifier-") as temporary_directory:
        temporary = Path(temporary_directory)
        module_root = temporary / "modules"
        for module_name in snapshot["requiredSdkModules"]:
            stub_dir = module_root / module_name
            stub_dir.mkdir(parents=True)
            (stub_dir / f"{module_name}.psm1").write_text("# Import-only test dependency; no VMware behavior is replaced.\n", encoding="utf-8")

        generated_path = temporary / "generated.json"
        runtime = (
            "$ErrorActionPreference='Stop';"
            "Import-Module -Name $env:VCF_IMPL -Force;"
            "New-VcfAriaMigrationInstallerSpec -InventoryPath $env:VCF_INVENTORY "
            "-CompatibilitySnapshotPath $env:VCF_SNAPSHOT -SchemaPath $env:VCF_SCHEMA "
            "-OutputPath $env:VCF_OUTPUT|Out-Null;"
            "$loaded=@(Get-Module -All|Where-Object Name -In @('VMware.Sdk.Vcf.Installer','VMware.Sdk.Vcf.SddcManager')|Select-Object -ExpandProperty Name);"
            "foreach($name in @('VMware.Sdk.Vcf.Installer','VMware.Sdk.Vcf.SddcManager')){if($name -notin $loaded){throw \"generator did not import $name\"}}"
        )
        runtime_env = {
            "VCF_IMPL": str(implementation_path),
            "VCF_INVENTORY": str(ROOT / "estate-inventory.json"),
            "VCF_SNAPSHOT": str(ROOT / "compatibility-snapshot.json"),
            "VCF_SCHEMA": str(ROOT / "installer-spec.schema.json"),
            "VCF_OUTPUT": str(generated_path),
            "PSModulePath": str(module_root) + os.pathsep + os.environ.get("PSModulePath", ""),
        }
        runtime_result = powershell(runtime, runtime_env)
        require(runtime_result.returncode == 0, "generator failed with supplied inputs: " + (runtime_result.stderr.strip() or runtime_result.stdout.strip()))
        generated = load_json(generated_path)
        require(generated == plan, "generator output does not exactly reproduce migration-installer-spec.json")

        alternate_inventory = json.loads(json.dumps(inventory))
        alternate_inventory["estateId"] = "alternate-estate"
        alternate_inventory["site"]["id"] = "alternate-site"
        for index, host in enumerate(alternate_inventory["foundation"]["managementDomain"]["hosts"], start=1):
            host["id"] = f"alternate-host-{index:02d}"
        alternate_inventory_path = temporary / "alternate-inventory.json"
        alternate_inventory_path.write_text(json.dumps(alternate_inventory), encoding="utf-8")
        alternate_snapshot = json.loads(json.dumps(snapshot))
        alternate_snapshot["targetRelease"] = "9.0.0-test"
        for rule in alternate_snapshot["productRules"]:
            rule["targetVersion"] = "9.0.0-test"
        alternate_snapshot["targetSizing"][0]["vcpuPerNode"] += 1
        alternate_snapshot_path = temporary / "alternate-snapshot.json"
        alternate_snapshot_path.write_text(json.dumps(alternate_snapshot), encoding="utf-8")
        alternate_path = temporary / "alternate.json"
        alternate_env = {
            **runtime_env,
            "VCF_INVENTORY": str(alternate_inventory_path),
            "VCF_SNAPSHOT": str(alternate_snapshot_path),
            "VCF_OUTPUT": str(alternate_path),
        }
        alternate_result = powershell(runtime, alternate_env)
        require(alternate_result.returncode == 0, "generator failed when exercising its input parameters")
        alternate = load_json(alternate_path)
        require(alternate["topology"]["siteId"] == "alternate-site", "generator does not derive siteId from InventoryPath")
        require(alternate["topology"]["managementDomain"]["hosts"] == [f"alternate-host-{index:02d}" for index in range(1, 5)], "generator does not derive management hosts from InventoryPath")
        alternate_placements = [entry["host"] for target in alternate["targetComponents"] for entry in target["placement"]]
        require(all(host.startswith("alternate-host-") for host in alternate_placements), "generator hard-codes node placements instead of using InventoryPath")
        require(all(target["version"] == "9.0.0-test" for target in alternate["targetComponents"]), "generator does not derive target versions from CompatibilitySnapshotPath")
        alternate_targets = by_key(alternate["targetComponents"], "component", "alternate target components")
        expected_ops_vcpu = next(item["vcpuPerNode"] for item in snapshot["targetSizing"] if item["component"] == "VCF Operations") + 1
        require(alternate_targets["VCF Operations"]["vcpuPerNode"] == expected_ops_vcpu, "generator does not derive sizing from CompatibilitySnapshotPath")
        require(all(boundary["targetVersion"] == "9.0.0-test" for boundary in alternate["supportBoundaries"]), "generator does not derive support mappings from CompatibilitySnapshotPath")

        malformed_schema_path = temporary / "malformed-schema.json"
        malformed_schema_path.write_text("not JSON\n", encoding="utf-8")
        malformed_env = {**runtime_env, "VCF_SCHEMA": str(malformed_schema_path), "VCF_OUTPUT": str(temporary / "must-not-generate.json")}
        malformed_result = powershell(runtime, malformed_env)
        require(malformed_result.returncode != 0, "generator ignored SchemaPath instead of reading the supplied schema")


def verify() -> None:
    schema = load_json(ROOT / "installer-spec.schema.json")
    plan = load_json(ROOT / "migration-installer-spec.json")

    errors = validate_schema(plan, schema)
    require(not errors, "migration-installer-spec.json does not validate against installer-spec.schema.json:\n" + "\n".join(errors[:20]))
    verify_fixture_hashes()
    before = {path: digest(path) for path in PROTECTED}
    inventory = load_json(ROOT / "estate-inventory.json")
    snapshot = load_json(ROOT / "compatibility-snapshot.json")
    require(len(plan["generatedBy"]["sdkModules"]) == len(snapshot["requiredSdkModules"]) and set(plan["generatedBy"]["sdkModules"]) == set(snapshot["requiredSdkModules"]), "generatedBy.sdkModules differs from the pinned dependency list")
    verify_support_boundaries(plan, snapshot)
    verify_topology_and_sizing(plan, inventory, snapshot)
    verify_steps(plan, inventory, snapshot)
    verify_module(plan, inventory, snapshot)

    after = {path: digest(path) for path in PROTECTED}
    require(before == after, "generator modified a protected input or verifier file")


if __name__ == "__main__":
    try:
        verify()
    except (VerificationError, subprocess.TimeoutExpired) as exc:
        print(f"VERIFICATION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print("VERIFICATION PASSED: migration architecture and executable generator match the protected inputs.")
