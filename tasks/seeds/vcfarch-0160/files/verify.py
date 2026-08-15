#!/usr/bin/env python3
"""Deterministic offline acceptance verifier for the VCF migration architecture."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
ERRORS: list[str] = []


def fail(message: str) -> None:
    ERRORS.append(message)


def load_json(name: str) -> Any:
    path = ROOT / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"missing required file: {name}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {name}: {exc}")
    return None


def exact_keys(value: Any, required: set[str], where: str) -> bool:
    if not isinstance(value, dict):
        fail(f"{where} must be an object")
        return False
    keys = set(value)
    missing = required - keys
    extra = keys - required
    if missing:
        fail(f"{where} missing fields: {sorted(missing)}")
    if extra:
        fail(f"{where} has fields outside the fixed schema: {sorted(extra)}")
    return not missing and not extra


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_research(doc: Any) -> None:
    if not exact_keys(doc, {"publisher", "sources"}, "research-sources"):
        return
    if doc["publisher"] != "Broadcom":
        fail("research-sources.publisher must be Broadcom")
    sources = doc["sources"]
    if not isinstance(sources, list) or not sources:
        fail("research-sources.sources must be a non-empty array")
        return

    all_topics: set[str] = set()
    seen_urls: set[str] = set()
    for i, source in enumerate(sources):
        where = f"research-sources.sources[{i}]"
        if not exact_keys(source, {"title", "url", "accessedOn", "topics"}, where):
            continue
        if not isinstance(source["title"], str) or not source["title"].strip():
            fail(f"{where}.title must be a non-empty string")
        url = source["url"]
        if not isinstance(url, str):
            fail(f"{where}.url must be a string")
        else:
            parsed = urlparse(url)
            hostname = (parsed.hostname or "").lower()
            if parsed.scheme != "https" or not (hostname == "broadcom.com" or hostname.endswith(".broadcom.com")):
                fail(f"{where}.url must be an HTTPS Broadcom-published page")
            if url in seen_urls:
                fail(f"{where}.url duplicates another consulted source")
            seen_urls.add(url)
        accessed_on = source["accessedOn"]
        if not isinstance(accessed_on, str):
            fail(f"{where}.accessedOn must be an ISO date")
        else:
            try:
                date.fromisoformat(accessed_on)
            except ValueError:
                fail(f"{where}.accessedOn must be an ISO date")
        topics = source["topics"]
        if not isinstance(topics, list) or not topics or not all(isinstance(topic, str) and topic for topic in topics):
            fail(f"{where}.topics must be a non-empty array of strings")
        elif len(topics) != len(set(topics)):
            fail(f"{where}.topics must not contain duplicates")
        else:
            all_topics.update(topics)

    required_topics = {
        "operations-migration", "automation-migration", "logs-migration",
        "content-compatibility", "sizing-placement", "source-eogs",
    }
    missing_topics = required_topics - all_topics
    if missing_topics:
        fail(f"research sources do not cover required topics: {sorted(missing_topics)}")


def validate_shape(doc: Any) -> None:
    top = {
        "schemaVersion", "architectureId", "generatedBy", "sourceInventoryId",
        "compatibilitySnapshotId", "targetVcfVersion", "resilience",
        "targetComponents", "supportBoundaries", "migrationPlan",
    }
    if not exact_keys(doc, top, "architecture"):
        return
    if doc["schemaVersion"] != "1.0":
        fail("schemaVersion must be 1.0")
    if not isinstance(doc["architectureId"], str) or not doc["architectureId"]:
        fail("architectureId must be a non-empty string")

    if exact_keys(doc["generatedBy"], {"module", "moduleVersion", "sdkModules"}, "generatedBy"):
        if doc["generatedBy"]["module"] != "VcfAriaMigration":
            fail("generatedBy.module must be VcfAriaMigration")
        if not re.fullmatch(r"\d+\.\d+\.\d+", str(doc["generatedBy"]["moduleVersion"])):
            fail("generatedBy.moduleVersion must be semantic x.y.z")
        if not isinstance(doc["generatedBy"]["sdkModules"], list):
            fail("generatedBy.sdkModules must be an array")

    resilience_keys = {
        "topology", "managementDomainId", "siteFailuresToTolerate",
        "hostFailuresToToleratePerSite", "raidLevel", "dataSites",
        "totalDataHostCount", "vsanWitness",
    }
    resilience = doc["resilience"]
    if exact_keys(resilience, resilience_keys, "resilience"):
        for field in ("siteFailuresToTolerate", "hostFailuresToToleratePerSite", "totalDataHostCount"):
            if not is_int(resilience[field]) or resilience[field] < 0:
                fail(f"resilience.{field} must be a non-negative integer")
        if not isinstance(resilience["dataSites"], list):
            fail("resilience.dataSites must be an array")
        else:
            for i, site in enumerate(resilience["dataSites"]):
                if exact_keys(site, {"siteId", "faultDomainId", "dataHostCount"}, f"resilience.dataSites[{i}]"):
                    if not is_int(site["dataHostCount"]) or site["dataHostCount"] < 1:
                        fail(f"resilience.dataSites[{i}].dataHostCount must be a positive integer")
        exact_keys(
            resilience["vsanWitness"],
            {"assetId", "siteId", "faultDomainId", "role", "hostsWorkloads"},
            "resilience.vsanWitness",
        )

    if not isinstance(doc["targetComponents"], list):
        fail("targetComponents must be an array")
    else:
        for i, component in enumerate(doc["targetComponents"]):
            where = f"targetComponents[{i}]"
            if not exact_keys(component, {"component", "version", "deploymentMode", "sizing", "placement"}, where):
                continue
            sizing_keys = {
                "profile", "nodeCount", "vCpuPerNode", "memoryGbPerNode",
                "witnessNodeCount", "witnessVCpu", "witnessMemoryGb",
                "capacityUnit", "requiredCapacity", "plannedCapacity",
            }
            if exact_keys(component["sizing"], sizing_keys, f"{where}.sizing"):
                for field in (
                    "nodeCount", "vCpuPerNode", "memoryGbPerNode", "witnessNodeCount",
                    "witnessVCpu", "witnessMemoryGb", "requiredCapacity", "plannedCapacity",
                ):
                    if not is_int(component["sizing"][field]) or component["sizing"][field] < 0:
                        fail(f"{where}.sizing.{field} must be a non-negative integer")
            placement = component["placement"]
            if exact_keys(placement, {"managementDomainId", "siteDistribution", "antiAffinity", "applicationWitness"}, f"{where}.placement"):
                if not isinstance(placement["siteDistribution"], list):
                    fail(f"{where}.placement.siteDistribution must be an array")
                else:
                    for j, site in enumerate(placement["siteDistribution"]):
                        if exact_keys(site, {"siteId", "nodeCount"}, f"{where}.placement.siteDistribution[{j}]"):
                            if not is_int(site["nodeCount"]) or site["nodeCount"] < 1:
                                fail(f"{where}.placement.siteDistribution[{j}].nodeCount must be a positive integer")
                app_witness = placement["applicationWitness"]
                if app_witness is not None:
                    exact_keys(app_witness, {"siteId", "computeId", "role", "hostsWorkloads"}, f"{where}.placement.applicationWitness")

    if not isinstance(doc["supportBoundaries"], list):
        fail("supportBoundaries must be an array")
    else:
        for i, boundary in enumerate(doc["supportBoundaries"]):
            exact_keys(
                boundary,
                {"sourceProduct", "sourceVersion", "endOfGeneralSupport", "requiredAction"},
                f"supportBoundaries[{i}]",
            )

    if not isinstance(doc["migrationPlan"], list):
        fail("migrationPlan must be an array")
    else:
        step_keys = {
            "order", "stepId", "source", "target", "route", "inPlaceSupported",
            "carries", "abandons", "gates", "dependsOn", "rollbackBoundary",
        }
        for i, step in enumerate(doc["migrationPlan"]):
            where = f"migrationPlan[{i}]"
            if not exact_keys(step, step_keys, where):
                continue
            exact_keys(step["source"], {"instanceId", "product", "version"}, f"{where}.source")
            exact_keys(step["target"], {"component", "version"}, f"{where}.target")
            if not isinstance(step["carries"], list):
                fail(f"{where}.carries must be an array")
            else:
                for j, item in enumerate(step["carries"]):
                    exact_keys(item, {"itemId", "mode"}, f"{where}.carries[{j}]")
            if not isinstance(step["abandons"], list):
                fail(f"{where}.abandons must be an array")
            else:
                for j, item in enumerate(step["abandons"]):
                    exact_keys(item, {"itemId", "handling"}, f"{where}.abandons[{j}]")
            for field in ("gates", "dependsOn"):
                if not isinstance(step[field], list) or not all(isinstance(v, str) for v in step[field]):
                    fail(f"{where}.{field} must be an array of strings")


def canonical_pairs(items: list[dict[str, Any]], value_key: str) -> set[tuple[str, str]]:
    return {(str(item.get("itemId")), str(item.get(value_key))) for item in items}


def validate_authority(doc: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    if doc.get("sourceInventoryId") != inventory["inventoryId"]:
        fail("sourceInventoryId does not match estate-inventory.json")
    if doc.get("compatibilitySnapshotId") != snapshot["snapshotId"]:
        fail("compatibilitySnapshotId does not match compatibility-snapshot.json")
    if doc.get("targetVcfVersion") != inventory["targetVcfVersion"] or doc.get("targetVcfVersion") != snapshot["targetVcfVersion"]:
        fail("targetVcfVersion does not match both protected authorities")
    if set(doc.get("generatedBy", {}).get("sdkModules", [])) != set(snapshot["sdkModules"]):
        fail("generatedBy.sdkModules must exactly name the pinned VMware.Sdk.Vcf modules")

    expected_components = {item["component"]: item for item in snapshot["targetComponents"]}
    actual_list = doc.get("targetComponents", [])
    actual_components = {item.get("component"): item for item in actual_list if isinstance(item, dict)}
    if len(actual_list) != len(actual_components) or set(actual_components) != set(expected_components):
        fail("targetComponents must contain every pinned target exactly once")
    for name, expected in expected_components.items():
        actual = actual_components.get(name)
        if not actual:
            continue
        if actual.get("version") != expected["version"]:
            fail(f"{name} target version contradicts snapshot")
        if actual.get("deploymentMode") != expected["deploymentMode"]:
            fail(f"{name} deployment mode contradicts snapshot")
        if actual.get("sizing") != expected["sizing"]:
            fail(f"{name} sizing contradicts snapshot")
        placement = actual.get("placement", {})
        if placement.get("managementDomainId") != inventory["managementDomain"]["id"]:
            fail(f"{name} is not placed in the stated management domain")
        if placement.get("siteDistribution") != expected["siteDistribution"]:
            fail(f"{name} site distribution contradicts snapshot")
        if placement.get("antiAffinity") is not expected["antiAffinity"]:
            fail(f"{name} anti-affinity contradicts snapshot")
        if placement.get("applicationWitness") != expected["applicationWitness"]:
            fail(f"{name} application witness placement contradicts snapshot")
        node_sum = sum(x.get("nodeCount", 0) for x in placement.get("siteDistribution", []) if isinstance(x, dict))
        if node_sum != actual.get("sizing", {}).get("nodeCount"):
            fail(f"{name} placement node count does not equal sizing node count")

    routes = snapshot["routes"]
    expected_steps = {route["stepId"]: route for route in routes}
    actual_steps_list = doc.get("migrationPlan", [])
    actual_steps = {step.get("stepId"): step for step in actual_steps_list if isinstance(step, dict)}
    if len(actual_steps_list) != len(actual_steps) or set(actual_steps) != set(expected_steps):
        fail("migrationPlan must contain one and only one step for each pinned route")
    actual_orders = [step.get("order") for step in actual_steps_list if isinstance(step, dict)]
    if actual_orders != list(range(1, len(routes) + 1)):
        fail("migrationPlan must be stored in strict order 1..N")

    inventory_instances = {item["instanceId"]: item for item in inventory["sourceInstances"]}
    instance_by_product_version = {(item["product"], item["version"]): item for item in inventory["sourceInstances"]}
    for route in routes:
        step = actual_steps.get(route["stepId"])
        if not step:
            continue
        source = step.get("source", {})
        source_key = (route["sourceProduct"], route["sourceVersion"])
        fixture_source = instance_by_product_version.get(source_key)
        if not fixture_source:
            fail(f"snapshot route {route['stepId']} has no fixture source")
            continue
        expected_source = {
            "instanceId": fixture_source["instanceId"],
            "product": route["sourceProduct"],
            "version": route["sourceVersion"],
        }
        if source != expected_source:
            fail(f"{route['stepId']} source product/version/instance is incorrect")
        if source.get("instanceId") not in inventory_instances:
            fail(f"{route['stepId']} names an unknown source instance")
        if step.get("order") != route["order"]:
            fail(f"{route['stepId']} order contradicts snapshot")
        if step.get("target") != {"component": route["targetComponent"], "version": route["targetVersion"]}:
            fail(f"{route['stepId']} target contradicts snapshot")
        for field in ("route", "inPlaceSupported", "gates", "dependsOn", "rollbackBoundary"):
            if step.get(field) != route[field]:
                fail(f"{route['stepId']} {field} contradicts snapshot")
        if canonical_pairs(step.get("carries", []), "mode") != canonical_pairs(route["carries"], "mode"):
            fail(f"{route['stepId']} carry-forward decisions contradict snapshot")
        if canonical_pairs(step.get("abandons", []), "handling") != canonical_pairs(route["abandons"], "handling"):
            fail(f"{route['stepId']} abandonment decisions contradict snapshot")
        decided = [item.get("itemId") for item in step.get("carries", [])] + [item.get("itemId") for item in step.get("abandons", [])]
        if len(decided) != len(set(decided)) or set(decided) != set(fixture_source["contentItems"]):
            fail(f"{route['stepId']} must decide every fixture content item exactly once")

    boundaries = doc.get("supportBoundaries", [])
    expected_boundaries = {
        (r["sourceProduct"], r["sourceVersion"]): r["endOfGeneralSupport"] for r in routes
    }
    actual_boundaries: dict[tuple[str, str], str] = {}
    for boundary in boundaries:
        if not isinstance(boundary, dict):
            continue
        key = (boundary.get("sourceProduct"), boundary.get("sourceVersion"))
        if key in actual_boundaries:
            fail(f"duplicate support boundary for {key}")
        actual_boundaries[key] = boundary.get("endOfGeneralSupport")
        if boundary.get("requiredAction") != "complete-supported-transition-before-eogs":
            fail(f"support boundary {key} has incorrect requiredAction")
    if actual_boundaries != expected_boundaries:
        fail("supportBoundaries do not exactly match the pinned source products, versions, and EOGS dates")


def validate_resilience(doc: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    actual = doc.get("resilience", {})
    rules = snapshot["resilienceRules"]
    mgmt = inventory["managementDomain"]
    expected_sites = {
        site["siteId"]: {
            "siteId": site["siteId"],
            "faultDomainId": site["faultDomainId"],
            "dataHostCount": len(site["hosts"]),
        }
        for site in mgmt["dataSites"]
    }
    actual_site_list = actual.get("dataSites", [])
    actual_sites = {site.get("siteId"): site for site in actual_site_list if isinstance(site, dict)}
    if len(actual_site_list) != len(actual_sites) or actual_sites != expected_sites:
        fail("resilience data-site host counts/fault domains contradict the estate inventory")

    declared_ftt = actual.get("hostFailuresToToleratePerSite")
    minimums = rules["minimumDataHostsPerSiteByHostFtt"]
    minimum_for_declared_ftt = minimums.get(str(declared_ftt))
    if minimum_for_declared_ftt is None:
        fail(f"no pinned host-count rule exists for declared host FTT {declared_ftt}")
    else:
        for site_id, site in actual_sites.items():
            count = site.get("dataHostCount")
            if not is_int(count) or count < minimum_for_declared_ftt:
                fail(
                    f"{site_id} has {count} data hosts but declared host FTT {declared_ftt} "
                    f"requires at least {minimum_for_declared_ftt} per site"
                )
            if is_int(count) and count < rules["minimumManagementDataHostsPerSite"]:
                fail(f"{site_id} is below the management-domain minimum host count")
    summed_hosts = sum(site.get("dataHostCount", 0) for site in actual_sites.values())
    if actual.get("totalDataHostCount") != summed_hosts:
        fail("totalDataHostCount does not equal the sum of data-site host counts")
    if summed_hosts < rules["minimumManagementDataHostsTotal"]:
        fail("stretched management domain is below the pinned total data-host minimum")

    scalar_expectations = {
        "topology": rules["topology"],
        "managementDomainId": mgmt["id"],
        "siteFailuresToTolerate": rules["siteFailuresToTolerate"],
        "hostFailuresToToleratePerSite": rules["hostFailuresToToleratePerSite"],
        "raidLevel": rules["raidLevel"],
    }
    for field, expected in scalar_expectations.items():
        if actual.get(field) != expected:
            fail(f"resilience.{field} contradicts pinned topology")

    expected_vsan = {
        "assetId": mgmt["vsanWitness"]["assetId"],
        "siteId": rules["vsanWitness"]["requiredSiteId"],
        "faultDomainId": rules["vsanWitness"]["requiredFaultDomainId"],
        "role": rules["vsanWitness"]["requiredRole"],
        "hostsWorkloads": rules["vsanWitness"]["hostsWorkloads"],
    }
    if actual.get("vsanWitness") != expected_vsan:
        fail("vSAN witness is not correctly placed in the independent third failure domain")
    data_site_ids = set(expected_sites)
    if actual.get("vsanWitness", {}).get("siteId") in data_site_ids:
        fail("vSAN witness must not be placed in either data site")


def validate_module(
    inventory: dict[str, Any], snapshot: dict[str, Any], architecture: dict[str, Any]
) -> None:
    module_dir = ROOT / "VcfAriaMigration"
    manifest = module_dir / "VcfAriaMigration.psd1"
    implementation = module_dir / "VcfAriaMigration.psm1"
    if not manifest.is_file():
        fail("missing PowerShell module manifest VcfAriaMigration/VcfAriaMigration.psd1")
    if not implementation.is_file():
        fail("missing PowerShell module implementation VcfAriaMigration/VcfAriaMigration.psm1")
    if not manifest.is_file() or not implementation.is_file():
        return

    unexpected = sorted(
        str(path.relative_to(module_dir))
        for path in module_dir.rglob("*")
        if path.is_file() and path.suffix.lower() not in {".psd1", ".psm1"}
    )
    if unexpected:
        fail(f"module directory appears to vendor unsupported files: {unexpected}")

    ps_script = r"""
$ErrorActionPreference = 'Stop'
$manifest = Import-PowerShellDataFile -LiteralPath $args[0]
$required = @($manifest.RequiredModules | ForEach-Object {
    if ($_ -is [string]) { $_ } elseif ($_.ModuleName) { $_.ModuleName } else { [string]$_ }
})
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($args[1], [ref]$tokens, [ref]$parseErrors)
$functionCommands = [ordered]@{}
$functionAsts = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst]
}, $true))
foreach ($functionAst in $functionAsts) {
    $functionCommands[$functionAst.Name] = @(
        $functionAst.Body.FindAll({
            param($node)
            $node -is [System.Management.Automation.Language.CommandAst]
        }, $true) |
            ForEach-Object { $_.GetCommandName() } |
            Where-Object { $null -ne $_ }
    )
}
[ordered]@{
    RootModule = $manifest.RootModule
    ModuleVersion = [string]$manifest.ModuleVersion
    RequiredModules = $required
    FunctionsToExport = @($manifest.FunctionsToExport)
    ParseErrors = @($parseErrors | ForEach-Object { $_.Message })
    FunctionCommands = $functionCommands
} | ConvertTo-Json -Compress -Depth 5
"""
    try:
        result = subprocess.run(
            ["pwsh", "-NoLogo", "-NoProfile", "-CommandWithArgs", ps_script, str(manifest), str(implementation)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        fail(f"could not parse PowerShell module: {exc}")
        return
    if result.returncode != 0:
        fail(f"PowerShell manifest/AST parse failed: {result.stderr.strip()}")
        return
    try:
        module_info = json.loads(result.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        fail("PowerShell module metadata parse did not return JSON")
        return
    if module_info.get("RootModule") != "VcfAriaMigration.psm1":
        fail("module manifest RootModule is incorrect")
    if not re.fullmatch(r"\d+\.\d+\.\d+", module_info.get("ModuleVersion", "")):
        fail("module manifest ModuleVersion must be semantic x.y.z")
    if module_info.get("ModuleVersion") != architecture.get("generatedBy", {}).get("moduleVersion"):
        fail("generatedBy.moduleVersion must match the module manifest")
    if set(module_info.get("RequiredModules", [])) != set(snapshot["sdkModules"]):
        fail("module manifest must require exactly the two pinned VMware.Sdk.Vcf modules")
    if set(module_info.get("FunctionsToExport", [])) != {"Get-VcfSdkInventory", "New-VcfMigrationArchitecture"}:
        fail("module must export exactly Get-VcfSdkInventory and New-VcfMigrationArchitecture")
    if module_info.get("ParseErrors"):
        fail(f"PowerShell module has parse errors: {module_info['ParseErrors']}")

    function_commands = module_info.get("FunctionCommands", {})
    expected_function_names = {"Get-VcfSdkInventory", "New-VcfMigrationArchitecture"}
    if set(function_commands) != expected_function_names:
        fail("PowerShell implementation must define exactly the two exported functions")
    live_commands = set(function_commands.get("Get-VcfSdkInventory", []))
    required_live_commands = {
        "Connect-VcfSddcManagerServer", "Invoke-VcfGetDomains", "Invoke-VcfGetHosts",
        "Connect-VcfOpsServer", "Invoke-VcfOpsGetDomainSummary",
        "Disconnect-VcfOpsServer", "Disconnect-VcfSddcManagerServer",
    }
    if not required_live_commands.issubset(live_commands):
        fail(f"Get-VcfSdkInventory is missing real SDK calls: {sorted(required_live_commands - live_commands)}")
    generation_commands = set(function_commands.get("New-VcfMigrationArchitecture", []))
    required_generation_commands = {"Get-Content", "ConvertFrom-Json", "ConvertTo-Json", "Set-Content"}
    if not required_generation_commands.issubset(generation_commands):
        fail(f"New-VcfMigrationArchitecture is not fixture driven: {sorted(required_generation_commands - generation_commands)}")

    generation_script = r"""
$ErrorActionPreference = 'Stop'
Import-Module -Name $args[0] -Force
New-VcfMigrationArchitecture -InventoryPath $args[1] -CompatibilitySnapshotPath $args[2] -OutputPath $args[3] | Out-Null
"""
    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temp_dir:
        generated_path = Path(temp_dir) / "migration-architecture.json"
        try:
            generated = subprocess.run(
                [
                    "pwsh", "-NoLogo", "-NoProfile", "-CommandWithArgs", generation_script,
                    str(implementation), str(ROOT / "estate-inventory.json"),
                    str(ROOT / "compatibility-snapshot.json"), str(generated_path),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            fail(f"could not execute fixture-driven architecture generation: {exc}")
            return
        if generated.returncode != 0:
            fail(f"fixture-driven architecture generation failed: {generated.stderr.strip()}")
            return
        try:
            generated_architecture = json.loads(generated_path.read_text(encoding="utf-8-sig"))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            fail(f"fixture-driven architecture generation did not produce valid JSON: {exc}")
            return
        if generated_architecture != architecture:
            fail("committed migration-architecture.json is not the output of New-VcfMigrationArchitecture")

        probe_inventory = json.loads(json.dumps(inventory))
        probe_snapshot = json.loads(json.dumps(snapshot))
        probe_inventory["inventoryId"] += "-probe"
        probe_snapshot["snapshotId"] += "-probe"
        probe_inventory["managementDomain"]["id"] += "-probe"
        probe_inventory["managementDomain"]["dataSites"][0]["hosts"].append("esx-probe")
        probe_snapshot["targetComponents"][0]["sizing"]["plannedCapacity"] += 1
        probe_snapshot["routes"][0]["gates"].append("probe-gate")
        probe_inventory_path = Path(temp_dir) / "estate-inventory-probe.json"
        probe_snapshot_path = Path(temp_dir) / "compatibility-snapshot-probe.json"
        probe_output_path = Path(temp_dir) / "migration-architecture-probe.json"
        probe_inventory_path.write_text(json.dumps(probe_inventory), encoding="utf-8")
        probe_snapshot_path.write_text(json.dumps(probe_snapshot), encoding="utf-8")
        try:
            probe = subprocess.run(
                [
                    "pwsh", "-NoLogo", "-NoProfile", "-CommandWithArgs", generation_script,
                    str(implementation), str(probe_inventory_path), str(probe_snapshot_path),
                    str(probe_output_path),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            fail(f"could not execute fixture mutation probe: {exc}")
            return
        if probe.returncode != 0:
            fail(f"fixture mutation probe failed: {probe.stderr.strip()}")
            return
        try:
            probe_architecture = json.loads(probe_output_path.read_text(encoding="utf-8-sig"))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            fail(f"fixture mutation probe did not produce valid JSON: {exc}")
            return
        probe_management_id = probe_inventory["managementDomain"]["id"]
        probe_resilience = probe_architecture.get("resilience", {})
        probe_sites = probe_resilience.get("dataSites", [])
        probe_components = probe_architecture.get("targetComponents", [])
        probe_plan = probe_architecture.get("migrationPlan", [])
        probe_checks = [
            probe_architecture.get("sourceInventoryId") == probe_inventory["inventoryId"],
            probe_architecture.get("compatibilitySnapshotId") == probe_snapshot["snapshotId"],
            probe_resilience.get("managementDomainId") == probe_management_id,
            bool(probe_sites) and probe_sites[0].get("dataHostCount") == 5,
            probe_resilience.get("totalDataHostCount") == 9,
            bool(probe_components),
            all(
                item.get("placement", {}).get("managementDomainId") == probe_management_id
                for item in probe_components
            ),
            bool(probe_components) and probe_components[0].get("sizing", {}).get("plannedCapacity")
            == probe_snapshot["targetComponents"][0]["sizing"]["plannedCapacity"],
            bool(probe_plan) and probe_plan[0].get("gates", [])[-1:] == ["probe-gate"],
        ]
        if not all(probe_checks):
            fail("New-VcfMigrationArchitecture does not derive its output from the supplied fixture paths")


def main() -> int:
    inventory = load_json("estate-inventory.json")
    snapshot = load_json("compatibility-snapshot.json")
    schema = load_json("architecture.schema.json")
    research = load_json("research-sources.json")
    architecture = load_json("migration-architecture.json")
    if schema is None:
        fail("fixed architecture schema could not be loaded")
    if research is not None:
        validate_research(research)
    if all(value is not None for value in (inventory, snapshot, architecture)):
        validate_shape(architecture)
        validate_authority(architecture, inventory, snapshot)
        validate_resilience(architecture, inventory, snapshot)
        validate_module(inventory, snapshot, architecture)

    if ERRORS:
        print(f"FAIL: {len(ERRORS)} acceptance error(s)")
        for error in ERRORS:
            print(f" - {error}")
        return 1
    print("PASS: VCF migration architecture and research record match the fixture, pinned snapshot, module contract, sizing, placement, content decisions, gates, and FTT host rules")
    return 0


if __name__ == "__main__":
    sys.exit(main())
