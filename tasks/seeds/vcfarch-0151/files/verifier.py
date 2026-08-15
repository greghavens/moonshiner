#!/usr/bin/env python3
"""Deterministic verifier for the Northstar VCF migration architecture."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent


class VerificationError(Exception):
    pass


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VerificationError(f"missing required file: {path.name}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(f"invalid JSON in {path.name}: {exc}") from exc


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


def resolve_ref(root_schema: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/"):
        raise VerificationError(f"unsupported schema reference: {reference}")
    current: Any = root_schema
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            raise VerificationError(f"unresolvable schema reference: {reference}")
        current = current[part]
    if not isinstance(current, dict):
        raise VerificationError(f"schema reference is not an object: {reference}")
    return current


def validate_schema(instance: Any, schema: dict[str, Any], root_schema: dict[str, Any], path: str = "$") -> list[str]:
    """Validate the JSON-Schema features used by installer-spec.json."""
    if "$ref" in schema:
        return validate_schema(instance, resolve_ref(root_schema, schema["$ref"]), root_schema, path)

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
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                errors.append(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in instance:
                if key not in properties:
                    errors.append(f"{path}: additional property {key!r} is not allowed")
        for key, child_schema in properties.items():
            if key in instance:
                errors.extend(validate_schema(instance[key], child_schema, root_schema, f"{path}.{key}"))

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
                errors.extend(validate_schema(item, item_schema, root_schema, f"{path}[{index}]"))

    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            errors.append(f"{path}: string is shorter than {schema['minLength']}")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            errors.append(f"{path}: string does not match {schema['pattern']!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: value is below minimum {schema['minimum']}")
    return errors


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def by_id(items: list[dict[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    values = [item.get(key) for item in items]
    require(all(isinstance(value, str) for value in values), f"{label} has a missing {key}")
    require(len(values) == len(set(values)), f"{label} contains duplicate {key} values")
    return {item[key]: item for item in items}


def verify_research(plan: dict[str, Any]) -> None:
    sources = plan["researchConsulted"]
    seen_uris: set[str] = set()
    all_topics: list[str] = []
    try:
        generated_on = date.fromisoformat(plan["generatedOn"])
    except ValueError as exc:
        raise VerificationError("generatedOn must be a valid calendar date") from exc

    for index, source in enumerate(sources):
        label = f"researchConsulted[{index}]"
        uri = source["uri"]
        parsed = urlsplit(uri)
        host = (parsed.hostname or "").lower()
        require(parsed.scheme.lower() == "https", f"{label}: source URI must use HTTPS")
        require(host == "broadcom.com" or host.endswith(".broadcom.com"), f"{label}: source URI must use a Broadcom-published host")
        require(parsed.username is None and parsed.password is None, f"{label}: source URI must not contain credentials")
        require(bool(parsed.path.strip("/") or parsed.query), f"{label}: source URI must identify a published page")
        require(uri not in seen_uris, f"{label}: duplicate research URI")
        seen_uris.add(uri)
        try:
            accessed_on = date.fromisoformat(source["accessedOn"])
        except ValueError as exc:
            raise VerificationError(f"{label}: accessedOn must be a valid calendar date") from exc
        require(accessed_on <= generated_on, f"{label}: source cannot be accessed after the plan is generated")
        all_topics.extend(topic.casefold() for topic in source["topics"])

    topic_text = " ".join(all_topics)
    required_topic_groups = {
        "migration paths": ("migration", "upgrade", "fleet", "import"),
        "content and integration compatibility": ("content", "integration", "management pack", "removed feature"),
        "sizing and continuous availability": ("sizing", "capacity", "continuous availability", "witness"),
        "support boundaries": ("support", "eogs", "end of general"),
    }
    for label, terms in required_topic_groups.items():
        require(any(term in topic_text for term in terms), f"research topics do not cover {label}")


def verify_migrations(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    products = by_id(inventory["products"], "id", "inventory products")
    rules = by_id(snapshot["migrationRules"], "sourceProductId", "migration rules")
    migrations = by_id(plan["migrations"], "id", "artifact migrations")
    require(set(migrations) == set(products) == set(rules), "artifact must contain exactly one migration for every inventoried product")

    for product_id, product in products.items():
        migration = migrations[product_id]
        rule = rules[product_id]
        expected_source = {
            "id": product_id,
            "name": product["name"],
            "formerName": product["formerName"],
            "version": product["version"],
        }
        expected_target = {
            "id": rule["targetComponentId"],
            "name": rule["targetName"],
            "version": rule["targetVersion"],
        }
        require(migration["source"] == expected_source, f"{product_id}: source identity/version does not match inventory")
        require(migration["target"] == expected_target, f"{product_id}: target identity/version does not match snapshot")
        require(migration["path"] == {"mode": rule["mode"], "supported": rule["supported"]}, f"{product_id}: migration path does not match snapshot")
        require(
            migration["supportBoundary"] == {
                "endOfGeneralSupport": rule["endOfGeneralSupport"],
                "statusAtPlanDate": "supported",
            },
            f"{product_id}: support boundary does not match snapshot",
        )

        inventory_content = by_id(product["content"], "id", f"{product_id} inventory content")
        content_rules = by_id(snapshot["contentRules"][product_id], "contentId", f"{product_id} content rules")
        dispositions = by_id(migration["contentDisposition"], "contentId", f"{product_id} content dispositions")
        require(set(dispositions) == set(inventory_content) == set(content_rules), f"{product_id}: every content/configuration item must be accounted for exactly once")
        for content_id, source_item in inventory_content.items():
            expected = {
                "contentId": content_id,
                "type": source_item["type"],
                "name": source_item["name"],
                "disposition": content_rules[content_id]["disposition"],
                "targetTreatment": content_rules[content_id]["targetTreatment"],
            }
            require(dispositions[content_id] == expected, f"{product_id}/{content_id}: disposition does not match pinned compatibility rule")


def verify_architecture(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    actual = plan["architecture"]
    expected = snapshot["targetDesign"]
    expected_management = {
        "name": expected["managementDomain"]["name"],
        "cluster": expected["managementDomain"]["cluster"],
        "stretchedAcross": expected["managementDomain"]["dataSites"],
        "foundationWitness": {
            "name": expected["managementDomain"]["foundationWitnessName"],
            "site": expected["managementDomain"]["foundationWitnessSite"],
            "runsTargetWorkloads": expected["managementDomain"]["foundationWitnessRunsTargetWorkloads"],
        },
    }
    require(actual["managementDomain"] == expected_management, "management-domain and vSAN witness placement must match the pinned design")

    actual_targets = by_id(actual["targets"], "componentId", "architecture targets")
    expected_targets = by_id(expected["components"], "componentId", "snapshot targets")
    require(set(actual_targets) == set(expected_targets), "architecture must contain exactly the three pinned target components")
    for component_id, component in expected_targets.items():
        require(actual_targets[component_id] == component, f"{component_id}: sizing, placement, or capacity basis does not match pinned design")
        require(component["nodeCount"] == len(component["nodes"]), f"{component_id}: nodeCount does not match node list")

    operations = actual_targets["vcf-operations"]
    analytics_sites = sorted(node["site"] for node in operations["nodes"] if node["role"] == "analytics")
    witness_nodes = [node for node in operations["nodes"] if node["role"] == "ca-witness"]
    require(analytics_sites == ["site-a", "site-b"], "VCF Operations CA analytics nodes must be balanced across the two data sites")
    require(len(witness_nodes) == 1 and witness_nodes[0]["site"] == "site-witness", "VCF Operations CA witness must be in the third site")
    require(not any(node["role"] in {"analytics", "data"} and node["site"] == "site-witness" for node in operations["nodes"]), "the witness site must not host Operations data roles")

    foundation = inventory["foundation"]
    capacity_by_site = {entry["site"]: entry for entry in foundation["managementDomain"]["siteFreeCapacity"]}
    witness_capacity = foundation["witnessSite"]["freeCapacity"]
    used: dict[str, dict[str, int]] = {}
    for component in actual["targets"]:
        for node in component["nodes"]:
            totals = used.setdefault(node["site"], {"vCpu": 0, "memoryGb": 0, "storageGb": 0})
            totals["vCpu"] += node["vCpu"]
            totals["memoryGb"] += node["memoryGb"]
            totals["storageGb"] += node["storageGb"]
    for site, totals in used.items():
        available = witness_capacity if site == foundation["witnessSite"]["id"] else capacity_by_site[site]
        for resource in ("vCpu", "memoryGb", "storageGb"):
            require(totals[resource] <= available[resource], f"target placement exceeds {site} free {resource}")


def verify_steps(plan: dict[str, Any], snapshot: dict[str, Any]) -> None:
    steps = plan["orderedSteps"]
    rules = snapshot["orderedStepRules"]
    require(len(steps) == len(rules), "ordered migration plan has the wrong number of steps")
    require([step["order"] for step in steps] == sorted(step["order"] for step in steps), "migration steps are not in ascending order")
    for index, (step, rule) in enumerate(zip(steps, rules)):
        require((step["order"], step["id"], step["migrationId"]) == (rule["order"], rule["id"], rule["migrationId"]), f"migration step {index + 1} does not match pinned order")
        gate_ids = [gate["id"] for gate in step["gates"]]
        require(len(gate_ids) == len(set(gate_ids)), f"{step['id']}: duplicate gate ids")
        require(set(gate_ids) == set(snapshot["requiredGateIds"][step["id"]]), f"{step['id']}: required gates do not match pinned snapshot")
        expected_dependencies = [] if index == 0 else [steps[index - 1]["id"]]
        require(step["dependsOn"] == expected_dependencies, f"{step['id']}: each step must depend directly on its predecessor")


def verify_module(spec: dict[str, Any], plan: dict[str, Any]) -> None:
    manifest_path = ROOT / spec["module"]["manifestPath"]
    implementation_path = ROOT / spec["module"]["implementationPath"]
    require(manifest_path.is_file(), f"missing module manifest: {manifest_path.relative_to(ROOT)}")
    require(implementation_path.is_file(), f"missing module implementation: {implementation_path.relative_to(ROOT)}")
    manifest = manifest_path.read_text(encoding="utf-8")
    implementation = implementation_path.read_text(encoding="utf-8")

    for module_name in spec["module"]["requiredPowerCliModules"]:
        require(module_name in manifest, f"module manifest must declare required module {module_name}")
        require(re.search(rf"Import-Module\s+['\"]?{re.escape(module_name)}['\"]?", implementation, re.IGNORECASE) is not None, f"module implementation must import {module_name}")
    for function_name in spec["module"]["requiredExports"]:
        require(re.search(rf"function\s+{re.escape(function_name)}\b", implementation, re.IGNORECASE) is not None, f"missing exported function {function_name}")
        require(function_name in manifest, f"manifest does not export {function_name}")
    for command in ("Connect-VcfSddcManagerServer", "Invoke-VcfGetDomains", "Invoke-VcfGetClusters", "Disconnect-VcfSddcManagerServer"):
        require(re.search(rf"\b{re.escape(command)}\b", implementation, re.IGNORECASE) is not None, f"connected mode must use {command}")

    module_dir = ROOT / spec["module"]["directory"]
    allowed = {manifest_path.resolve(), implementation_path.resolve()}
    extra_files = [path for path in module_dir.rglob("*") if path.is_file() and path.resolve() not in allowed]
    require(not extra_files, "module directory contains vendored or unexpected files: " + ", ".join(str(path.relative_to(ROOT)) for path in extra_files))

    pwsh = shutil.which("pwsh")
    require(pwsh is not None, "pwsh is required to parse the PowerShell module")
    parser_script = (
        "$tokens=$null;$errors=$null;"
        "$ast=[System.Management.Automation.Language.Parser]::ParseFile($env:VCF_ARCH_MODULE,[ref]$tokens,[ref]$errors);"
        "if($errors.Count -gt 0){$errors|ForEach-Object{Write-Error $_.Message};exit 1};"
        "$functions=@($ast.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst]},$true));"
        "$confirm=@($functions|Where-Object Name -CEQ 'Confirm-VcfConnectedInventory');"
        "if($confirm.Count -ne 1){Write-Error 'connected inventory helper missing or duplicated';exit 1};"
        "$commands=@($confirm[0].Body.FindAll({param($node) $node -is [System.Management.Automation.Language.CommandAst]},$true)|ForEach-Object{$_.GetCommandName()});"
        "$required=@('Import-Module','Connect-VcfSddcManagerServer','Invoke-VcfGetDomains','Invoke-VcfGetClusters','Disconnect-VcfSddcManagerServer');"
        "foreach($name in $required){if($name -notin $commands){Write-Error \"missing command AST: $name\";exit 1}};"
        "$generator=@($functions|Where-Object Name -CEQ 'New-VcfMigrationArchitecture');"
        "if($generator.Count -ne 1){Write-Error 'generator missing or duplicated';exit 1};"
        "$generatorCommands=@($generator[0].Body.FindAll({param($node) $node -is [System.Management.Automation.Language.CommandAst]},$true)|ForEach-Object{$_.GetCommandName()});"
        "if('Confirm-VcfConnectedInventory' -notin $generatorCommands){Write-Error 'generator does not invoke connected inventory validation';exit 1};"
        "$manifest=Import-PowerShellDataFile -LiteralPath $env:VCF_ARCH_MANIFEST;"
        "$moduleNames=@($manifest.RequiredModules|ForEach-Object{if($_ -is [string]){$_}else{$_.ModuleName}});"
        "if('VMware.Sdk.Vcf.SddcManager' -notin $moduleNames){Write-Error 'manifest dependency missing';exit 1};"
        "$exports=@($manifest.FunctionsToExport);"
        "foreach($name in @('New-VcfMigrationArchitecture','Test-VcfMigrationArchitecture')){if($name -notin $exports){Write-Error \"manifest export missing: $name\";exit 1}}"
    )
    parsed = subprocess.run(
        [pwsh, "-NoLogo", "-NoProfile", "-Command", parser_script],
        cwd=ROOT,
        env={
            **os.environ,
            "VCF_ARCH_MODULE": str(implementation_path),
            "VCF_ARCH_MANIFEST": str(manifest_path),
        },
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    require(parsed.returncode == 0, "PowerShell parser rejected module: " + (parsed.stderr.strip() or parsed.stdout.strip()))

    with tempfile.TemporaryDirectory(prefix="vcf-architecture-verifier-") as temporary_directory:
        generated_path = Path(temporary_directory) / "migration-plan.json"
        runtime_script = (
            "$ErrorActionPreference='Stop';"
            "$plan=Get-Content -LiteralPath $env:VCF_ARCH_PLAN -Raw -Encoding utf8|ConvertFrom-Json -Depth 100;"
            "Import-Module $env:VCF_ARCH_MODULE -Force;"
            "$valid=Test-VcfMigrationArchitecture -Plan $plan -InstallerSpecPath $env:VCF_ARCH_SPEC;"
            "if($valid -ne $true){throw 'validator did not return true'};"
            "$invalid=$plan|ConvertTo-Json -Depth 100|ConvertFrom-Json -Depth 100;"
            "$invalid.architecture.targets[0].nodes[0].vCpu=0;"
            "$rejected=$false;"
            "try{Test-VcfMigrationArchitecture -Plan $invalid -InstallerSpecPath $env:VCF_ARCH_SPEC|Out-Null}catch{$rejected=$true};"
            "if(-not $rejected){throw 'validator accepted a nested schema violation'};"
            "New-VcfMigrationArchitecture -InventoryPath $env:VCF_ARCH_INVENTORY "
            "-CompatibilitySnapshotPath $env:VCF_ARCH_SNAPSHOT -InstallerSpecPath $env:VCF_ARCH_SPEC "
            "-ResearchSources @($plan.researchConsulted) -OutputPath $env:VCF_ARCH_GENERATED "
            "-GeneratedOn $plan.generatedOn -Offline|Out-Null"
        )
        exercised = subprocess.run(
            [pwsh, "-NoLogo", "-NoProfile", "-Command", runtime_script],
            cwd=ROOT,
            env={
                **os.environ,
                "VCF_ARCH_MODULE": str(implementation_path),
                "VCF_ARCH_PLAN": str(ROOT / spec["artifactPath"]),
                "VCF_ARCH_SPEC": str(ROOT / "installer-spec.json"),
                "VCF_ARCH_INVENTORY": str(ROOT / spec["inputs"]["estateInventory"]),
                "VCF_ARCH_SNAPSHOT": str(ROOT / spec["inputs"]["compatibilitySnapshot"]),
                "VCF_ARCH_GENERATED": str(generated_path),
            },
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        require(exercised.returncode == 0, "PowerShell module behavior failed: " + (exercised.stderr.strip() or exercised.stdout.strip()))
        generated = load_json(generated_path)
        require(generated == plan, "New-VcfMigrationArchitecture does not reproduce migration-plan.json from the supplied inputs")


def main() -> int:
    try:
        spec = load_json(ROOT / "installer-spec.json")
        artifact_path = ROOT / spec["artifactPath"]
        plan = load_json(artifact_path)

        # This is intentionally the first verification phase. No fixture, snapshot,
        # module, research trace, or network is examined before schema conformance.
        schema_errors = validate_schema(plan, spec["artifactSchema"], spec["artifactSchema"])
        if schema_errors:
            raise VerificationError("artifact schema validation failed:\n  - " + "\n  - ".join(schema_errors))

        inventory = load_json(ROOT / spec["inputs"]["estateInventory"])
        snapshot = load_json(ROOT / spec["inputs"]["compatibilitySnapshot"])
        require(plan["estateId"] == inventory["estateId"], "estateId does not match inventory")
        verify_research(plan)
        verify_migrations(plan, inventory, snapshot)
        verify_architecture(plan, inventory, snapshot)
        verify_steps(plan, snapshot)
        verify_module(spec, plan)
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: migration-plan.json and VcfMigrationArchitecture module satisfy the pinned architecture")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
