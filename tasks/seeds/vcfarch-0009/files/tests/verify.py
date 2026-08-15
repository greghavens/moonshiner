#!/usr/bin/env python3
"""Offline acceptance verifier for the VCF architecture seed."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
OPENAPI_PATH = ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
SUBMITTED_SDDC = ROOT / "out" / "greenfield-sddc-spec.json"


class VerificationError(AssertionError):
    pass


def read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise VerificationError(f"missing required artifact: {path.relative_to(ROOT)}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(f"invalid JSON in {path.relative_to(ROOT)}: {exc}") from exc


def json_pointer(document: Any, pointer: str) -> Any:
    if not pointer.startswith("#/"):
        raise VerificationError(f"only local OpenAPI references are supported, got {pointer!r}")
    current = document
    for raw in pointer[2:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        current = current[token]
    return current


def type_matches(value: Any, expected: str) -> bool:
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


def schema_errors(value: Any, schema: Any, document: Any, path: str = "$") -> list[str]:
    if isinstance(schema, bool):
        return [] if schema else [f"{path}: value is forbidden by schema"]
    if not isinstance(schema, dict):
        return [f"{path}: malformed schema"]
    if "$ref" in schema:
        return schema_errors(value, json_pointer(document, schema["$ref"]), document, path)
    if value is None and schema.get("nullable") is True:
        return []

    errors: list[str] = []
    for sub in schema.get("allOf", []):
        errors.extend(schema_errors(value, sub, document, path))
    if "anyOf" in schema and not any(not schema_errors(value, sub, document, path) for sub in schema["anyOf"]):
        errors.append(f"{path}: value does not satisfy anyOf")
    if "oneOf" in schema:
        matches = sum(not schema_errors(value, sub, document, path) for sub in schema["oneOf"])
        if matches != 1:
            errors.append(f"{path}: value satisfies {matches} oneOf branches, expected exactly one")
    if "not" in schema and not schema_errors(value, schema["not"], document, path):
        errors.append(f"{path}: value satisfies a forbidden schema")

    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        matched_type = any(type_matches(value, item) for item in expected_type)
    elif isinstance(expected_type, str):
        matched_type = type_matches(value, expected_type)
    else:
        matched_type = True
    if not matched_type:
        return errors + [f"{path}: expected {expected_type}, got {type(value).__name__}"]

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {schema['enum']!r}")

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path}: string is shorter than minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: string is longer than maxLength")
        if "pattern" in schema:
            try:
                if re.search(schema["pattern"], value) is None:
                    errors.append(f"{path}: {value!r} does not match {schema['pattern']!r}")
            except re.error as exc:
                raise VerificationError(f"invalid regex in protected schema at {path}: {exc}") from exc

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {value} is below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {value} is above maximum {schema['maximum']}")

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path}: array has fewer than minItems")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: array has more than maxItems")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(encoded) != len(set(encoded)):
                errors.append(f"{path}: array items are not unique")
        if "items" in schema:
            for index, item in enumerate(value):
                errors.extend(schema_errors(item, schema["items"], document, f"{path}[{index}]"))

    if isinstance(value, dict):
        if len(value) < schema.get("minProperties", 0):
            errors.append(f"{path}: object has fewer than minProperties")
        required = schema.get("required", [])
        for name in required:
            if name not in value:
                errors.append(f"{path}: missing required property {name!r}")
        properties = schema.get("properties", {})
        for name, item in value.items():
            if name in properties:
                errors.extend(schema_errors(item, properties[name], document, f"{path}.{name}"))
            elif "additionalProperties" in schema:
                extra = schema["additionalProperties"]
                if extra is False:
                    errors.append(f"{path}: unexpected property {name!r}")
                elif isinstance(extra, dict):
                    errors.extend(schema_errors(item, extra, document, f"{path}.{name}"))
    return errors


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def validate_with_schema(value: Any, schema: Any, document: Any, label: str) -> None:
    errors = schema_errors(value, schema, document)
    if errors:
        detail = "\n  - ".join(errors[:30])
        raise VerificationError(f"{label} schema validation failed:\n  - {detail}")


def validate_installer_schema_first() -> tuple[dict[str, Any], dict[str, Any]]:
    """This is deliberately the first verification phase."""
    sddc = read_json(SUBMITTED_SDDC)
    openapi = read_json(OPENAPI_PATH)
    sddc_schema = openapi["components"]["schemas"]["SddcSpec"]
    validate_with_schema(sddc, sddc_schema, openapi, "greenfield SddcSpec against VCF Installer OpenAPI")
    print("PASS phase 1: greenfield SddcSpec validates against the protected VCF Installer OpenAPI SddcSpec")
    return sddc, openapi


def by_key(items: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        name = item[key]
        require(name not in result, f"duplicate {key} {name!r}")
        result[name] = item
    return result


def choose_ops_tier(snapshot: dict[str, Any], requirements: dict[str, Any]) -> dict[str, Any]:
    count = requirements["capacity"]["vcfOperationsMonitoredObjects"]
    for tier in snapshot["sizing"]["vcfOperations"]:
        if count <= tier["maxMonitoredObjects"]:
            return tier
    raise VerificationError("VCF Operations demand exceeds every pinned sizing tier")


def choose_automation_tier(snapshot: dict[str, Any], requirements: dict[str, Any]) -> dict[str, Any]:
    capacity = requirements["capacity"]
    for tier in snapshot["sizing"]["vcfAutomation"]:
        if (capacity["automationManagedProjects"] <= tier["maxManagedProjects"] and
                capacity["automationConcurrentDeployments"] <= tier["maxConcurrentDeployments"]):
            return tier
    raise VerificationError("VCF Automation demand exceeds every pinned sizing tier")


def choose_logs_tier(snapshot: dict[str, Any], requirements: dict[str, Any]) -> dict[str, Any]:
    capacity = requirements["capacity"]
    for tier in snapshot["sizing"]["vcfOperationsForLogs"]:
        if (capacity["logIngestGbPerDay"] <= tier["maxIngestGbPerDay"] and
                capacity["logHotRetentionDays"] <= tier["maxHotRetentionDays"]):
            return tier
    raise VerificationError("log demand exceeds every pinned sizing tier")


def validate_sddc_semantics(sddc: dict[str, Any], req: dict[str, Any], snap: dict[str, Any]) -> None:
    installer = req["installer"]
    combo = snap["supportedGreenfieldCombination"]
    require(sddc.get("sddcId") == installer["sddcId"], "SddcSpec sddcId does not match requirements")
    require(sddc.get("vcfInstanceName") == installer["vcfInstanceName"], "SddcSpec vcfInstanceName mismatch")
    require(sddc.get("workflowType") == combo["workflowType"], "unsupported greenfield workflowType")
    require(sddc.get("version") == snap["targetRelease"], "SddcSpec target version mismatch")

    management = next(domain for domain in req["domains"] if domain["type"] == "MANAGEMENT")
    actual_hosts = [item["hostname"] for item in sddc.get("hostSpecs", [])]
    require(actual_hosts == management["hosts"], "SddcSpec must contain the four management hosts in requirement order")
    require(len(actual_hosts) >= snap["placementRules"]["minimumManagementHosts"], "management host minimum is not met")

    require(sddc["dnsSpec"] == {"subdomain": installer["dnsDomain"], "nameservers": installer["dnsServers"]}, "DNS specification mismatch")
    require(sddc.get("ntpServers") == installer["ntpServers"], "NTP server list mismatch")
    require(sddc["vcenterSpec"]["vcenterHostname"] == installer["vcenterFqdn"], "vCenter FQDN mismatch")
    require(sddc["vcenterSpec"].get("version") == combo["vcenter"], "vCenter version is not the pinned combination")
    require(sddc["sddcManagerSpec"]["hostname"] == installer["sddcManagerFqdn"], "SDDC Manager FQDN mismatch")
    require(sddc["sddcManagerSpec"].get("version") == combo["sddcManager"], "SDDC Manager version mismatch")
    nsx = sddc["nsxtSpec"]
    require([item["hostname"] for item in nsx["nsxtManagers"]] == installer["nsxManagerFqdns"], "NSX manager placement mismatch")
    require(nsx["vipFqdn"] == installer["nsxVipFqdn"] and nsx.get("version") == combo["nsx"], "NSX VIP/version mismatch")
    require(sddc.get("datastoreSpec", {}).get("vsanSpec", {}).get("failuresToTolerate") == req["availability"]["managementHostFailuresToTolerate"], "management vSAN FTT mismatch")

    expected_networks = by_key(installer["networks"], "networkType")
    actual_networks = by_key(sddc.get("networkSpecs", []), "networkType")
    require(set(actual_networks) == set(expected_networks), "SddcSpec network types do not match the requirements")
    for name, expected in expected_networks.items():
        actual = actual_networks[name]
        for field in ("vlanId", "subnet", "gateway", "subnetMask", "mtu"):
            require(actual.get(field) == expected[field], f"network {name} has wrong {field}")
        require(actual.get("includeIpAddressRanges") == [{"startIpAddress": expected["ipRangeStart"], "endIpAddress": expected["ipRangeEnd"]}], f"network {name} has wrong address range")
    require(nsx.get("transportVlanId") == expected_networks["HOST_OVERLAY"]["vlanId"], "NSX transport VLAN must use the HOST_OVERLAY network")

    infrastructure = sddc.get("vcfManagementComponentsInfrastructureSpec", {})
    for field, network_type in (("localRegionNetwork", "VM_MANAGEMENT"), ("xRegionNetwork", "FLEET_MANAGEMENT")):
        expected = expected_networks[network_type]
        require(infrastructure.get(field) == {
            "networkName": network_type,
            "subnetMask": expected["subnetMask"],
            "gateway": expected["gateway"],
        }, f"management component {field} does not match the {network_type} network")

    ops_tier = choose_ops_tier(snap, req)
    ops = sddc.get("vcfOperationsSpec", {})
    require(ops.get("version") == combo["vcfOperations"], "VCF Operations version mismatch")
    require(ops.get("applianceSize") == ops_tier["size"], "VCF Operations SddcSpec size mismatch")
    require(len(ops.get("nodes", [])) == ops_tier["nodeCount"], "VCF Operations node count mismatch")
    require([node.get("type") for node in ops["nodes"]] == ["master", "replica", "data"], "VCF Operations HA node roles must be master, replica, data")

    auto_tier = choose_automation_tier(snap, req)
    automation = sddc.get("vcfAutomationSpec", {})
    require(automation.get("version") == combo["vcfAutomation"], "VCF Automation version mismatch")
    require(automation.get("size") == auto_tier["size"], "VCF Automation SddcSpec size mismatch")
    require(automation.get("ipPool") == installer["automationIpPool"], "VCF Automation IP pool mismatch")
    require(sddc.get("vspClusterSpec", {}).get("size") == "large", "high-availability management services require the large VSP cluster")
    require(sddc.get("vspClusterSpec", {}).get("ipv4Pool", {}).get("addresses") == installer["managementServiceIpv4Pool"], "management service 12-address pool mismatch")

    serialized = json.dumps(sddc)
    require('"useExistingDeployment": true' not in serialized, "greenfield SddcSpec cannot reuse existing deployments")


def validate_architecture(architecture: dict[str, Any], req: dict[str, Any], snap: dict[str, Any], schema: dict[str, Any]) -> None:
    validate_with_schema(architecture, schema, schema, "architecture manifest")
    require(architecture["scenarioId"] == req["scenarioId"], "architecture scenarioId mismatch")
    require(architecture["targetVersion"] == snap["targetRelease"], "architecture targetVersion mismatch")

    expected_sites = {item["id"]: item for item in req["sites"]}
    actual_sites = by_key(architecture["sites"], "id")
    require(set(actual_sites) == set(expected_sites), "architecture must contain both stated sites")
    for site_id, expected in expected_sites.items():
        require(actual_sites[site_id] == {key: expected[key] for key in ("id", "role", "location")}, f"site {site_id} mismatch")

    combo = snap["supportedGreenfieldCombination"]
    expected_domains = by_key(req["domains"], "name")
    actual_domains = by_key(architecture["domains"], "name")
    require(set(actual_domains) == set(expected_domains), "management and workload domain set mismatch")
    for name, expected in expected_domains.items():
        actual = actual_domains[name]
        require(actual["type"] == expected["type"] and actual["site"] == expected["site"], f"domain {name} placement mismatch")
        require(actual["hosts"] == expected["hosts"], f"domain {name} host assignment mismatch")
        ftt = req["availability"]["managementHostFailuresToTolerate" if expected["type"] == "MANAGEMENT" else "workloadHostFailuresToTolerate"]
        require(actual["availability"]["hostFailuresToTolerate"] == ftt, f"domain {name} availability mismatch")
        require(actual["usableCapacity"] == {"cpuCores": expected["usableCpuCores"], "memoryGiB": expected["usableMemoryGiB"], "storageTiB": expected["usableStorageTiB"]}, f"domain {name} usable capacity mismatch")
        versions = actual["componentVersions"]
        require(versions["vcenter"] == combo["vcenter"] and versions["esxi"] == combo["esxi"] and versions["nsx"] == combo["nsx"], f"domain {name} uses an unsupported component combination")
        if expected["type"] == "MANAGEMENT":
            require(versions.get("sddcManager") == combo["sddcManager"], "management domain SDDC Manager version mismatch")

    components = by_key(architecture["managementComponents"], "name")
    require(set(components) == {"VCF Operations", "VCF Automation", "VCF Operations for Logs"}, "all three management products must be designed exactly once")
    primary = next(item["id"] for item in req["sites"] if item["role"] == "primary")
    management_name = next(item["name"] for item in req["domains"] if item["type"] == "MANAGEMENT")
    for name, component in components.items():
        require(component["site"] == primary and component["placementDomain"] == management_name, f"{name} must be placed in the primary management domain")

    ops_tier = choose_ops_tier(snap, req)
    ops = components["VCF Operations"]
    require((ops["version"], ops["size"], ops["deploymentModel"], ops["instances"]) == (combo["vcfOperations"], ops_tier["size"], ops_tier["deploymentModel"], ops_tier["nodeCount"]), "VCF Operations sizing mismatch")
    require(ops["capacityBasis"] == {"monitoredObjects": req["capacity"]["vcfOperationsMonitoredObjects"]}, "VCF Operations capacity basis mismatch")

    auto_tier = choose_automation_tier(snap, req)
    automation = components["VCF Automation"]
    require((automation["version"], automation["size"], automation["deploymentModel"], automation["instances"]) == (combo["vcfAutomation"], auto_tier["size"], auto_tier["deploymentModel"], auto_tier["serviceReplicas"]), "VCF Automation sizing mismatch")
    require(automation["capacityBasis"] == {"managedProjects": req["capacity"]["automationManagedProjects"], "concurrentDeployments": req["capacity"]["automationConcurrentDeployments"]}, "VCF Automation capacity basis mismatch")

    logs_tier = choose_logs_tier(snap, req)
    logs = components["VCF Operations for Logs"]
    require((logs["version"], logs["size"], logs["deploymentModel"], logs["instances"]) == (combo["vcfOperationsForLogs"], logs_tier["size"], logs_tier["deploymentModel"], logs_tier["serviceReplicas"]), "VCF Operations for Logs sizing/deployment mismatch")
    require(logs["capacityBasis"] == {"ingestGbPerDay": req["capacity"]["logIngestGbPerDay"], "hotRetentionDays": req["capacity"]["logHotRetentionDays"]}, "log capacity basis mismatch")
    require(by_key(logs.get("collectors", []), "site") == {site_id: {"site": site_id, "count": 1} for site_id in expected_sites}, "logs require one collector placement at every site")

    recovery = architecture["recovery"]
    recovery_site = next(item["id"] for item in req["sites"] if item["role"] == "recovery")
    protected = [item["name"] for item in req["domains"] if item["type"] == "WORKLOAD" and item["site"] == primary]
    require(recovery == {"mode": req["availability"]["siteRecovery"], "primarySite": primary, "recoverySite": recovery_site, "rpoMinutes": req["availability"]["rpoMinutes"], "rtoMinutes": req["availability"]["rtoMinutes"], "protectedDomains": protected}, "warm-site recovery design mismatch")


def validate_migration(plan: dict[str, Any], inventory: dict[str, Any], snap: dict[str, Any], schema: dict[str, Any]) -> None:
    validate_with_schema(plan, schema, schema, "migration plan")
    require(plan["estateId"] == inventory["estateId"], "migration estateId mismatch")
    require(plan["targetVersion"] == inventory["targetVersion"] == snap["targetRelease"], "migration target version mismatch")
    components = by_key(inventory["components"], "id")
    rules = by_key(snap["upgradeRules"], "componentId")
    steps = by_key(plan["steps"], "componentId")
    require(set(steps) == set(components) == set(rules), "migration plan must name every and only inventoried component")
    require([step["order"] for step in plan["steps"]] == list(range(1, len(plan["steps"]) + 1)), "migration steps must be stored in contiguous execution order")
    require([step["componentId"] for step in plan["steps"]] == [rule["componentId"] for rule in snap["upgradeRules"]], "migration sequence differs from pinned upgrade order")
    completed: set[str] = set()
    for step in plan["steps"]:
        source = components[step["componentId"]]
        rule = rules[step["componentId"]]
        require(step["component"] == source["name"] and step["currentVersion"] == source["version"], f"source identity/version mismatch for {step['componentId']}")
        require(step["order"] == rule["order"] and step["action"] == rule["action"], f"action/order mismatch for {step['componentId']}")
        require(step["target"] == {"component": rule["targetComponent"], "version": rule["targetVersion"]}, f"target mismatch for {step['componentId']}")
        require(step["gates"] == rule["gates"], f"gates mismatch for {step['componentId']}")
        require(step["dependsOn"] == rule["dependsOn"], f"dependencies mismatch for {step['componentId']}")
        require(set(step["dependsOn"]).issubset(completed), f"{step['componentId']} depends on a component that has not completed")
        completed.add(step["componentId"])


def validate_research_sources() -> None:
    sources = read_json(ROOT / "research-sources.json")
    require(isinstance(sources, list) and sources, "research-sources.json must contain at least one consulted source")
    required_fields = {"url", "title", "accessedAt", "usedFor"}
    urls: set[str] = set()
    for index, source in enumerate(sources):
        require(isinstance(source, dict), f"research source {index} must be an object")
        require(required_fields.issubset(source), f"research source {index} is missing one of {sorted(required_fields)}")
        for field in required_fields:
            require(isinstance(source[field], str) and source[field].strip(), f"research source {index} has an empty {field}")
        parsed = urlparse(source["url"])
        host = (parsed.hostname or "").lower()
        official = host in {"broadcom.com", "vmware.com"} or host.endswith((".broadcom.com", ".vmware.com"))
        require(parsed.scheme == "https" and official, f"research source {index} is not a Broadcom-published HTTPS URL")
        require(source["url"] not in urls, f"duplicate research URL {source['url']!r}")
        urls.add(source["url"])

    coverage = " ".join(f"{item['title']} {item['usedFor']}" for item in sources).lower()
    for topic, pattern in {
        "compatibility": r"compatib",
        "interoperability": r"interoperab",
        "upgrade path": r"upgrade",
        "sizing": r"siz",
        "VCF 9.1 documentation": r"9\.1.*(?:doc|architect|deploy)|(?:doc|architect|deploy).*9\.1",
    }.items():
        require(re.search(pattern, coverage) is not None, f"research record does not identify a source used for {topic}")


def validate_module() -> None:
    module = ROOT / "VcfArchitecture.psm1"
    manifest = ROOT / "VcfArchitecture.psd1"
    require(module.is_file(), "missing VcfArchitecture.psm1")
    require(manifest.is_file(), "missing VcfArchitecture.psd1")

    forbidden = []
    excluded_roots = {"specifications", "tests", ".verification-output", ".verification-variant", ".sandbox-home", ".git"}
    candidate_paths: list[Path] = []
    for top_level in ROOT.iterdir():
        if top_level.name in excluded_roots:
            continue
        candidate_paths.append(top_level)
        if top_level.is_dir():
            candidate_paths.extend(top_level.rglob("*"))
    for path in candidate_paths:
        rel = path.relative_to(ROOT).as_posix()
        lower = path.name.lower()
        if path.is_file() and (lower.endswith((".nupkg", ".dll")) or lower.startswith("vmware.sdk.vcf")):
            forbidden.append(rel)
    require(not forbidden, f"VMware SDK dependencies were vendored: {forbidden}")

    script = r"""
$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path './VcfArchitecture.psm1'), [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -ne 0) { throw ($parseErrors | ForEach-Object Message) -join '; ' }
$functions = @($ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true))
$handoff = @($functions | Where-Object Name -eq 'Invoke-VcfInstallerArchitecture')
if ($handoff.Count -ne 1) { throw 'Invoke-VcfInstallerArchitecture must be defined exactly once' }
$handoffCommands = @($handoff[0].Body.FindAll({ param($node) $node -is [System.Management.Automation.Language.CommandAst] }, $true))
$handoffNames = @($handoffCommands | ForEach-Object GetCommandName)
foreach ($name in @('Connect-VcfInstallerServer','Invoke-VcfInstallerValidateSddcSpec','Invoke-VcfInstallerDeploySddc','Disconnect-VcfInstallerServer')) {
    if ($name -notin $handoffNames) { throw "installer handoff does not invoke $name" }
}
$allCommandNames = @($ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.CommandAst] }, $true) | ForEach-Object GetCommandName)
if ('Initialize-VcfInstallerSddcSpec' -notin $allCommandNames) { throw 'installer handoff does not construct the SDK SddcSpec model' }
if ('Invoke-RestMethod' -in $allCommandNames -or 'Invoke-WebRequest' -in $allCommandNames) { throw 'module must use VMware.Sdk.Vcf rather than a parallel direct HTTP client' }
$imports = @($handoffCommands | Where-Object { $_.GetCommandName() -eq 'Import-Module' } | ForEach-Object {
    $_.CommandElements | Where-Object { $_ -is [System.Management.Automation.Language.StringConstantExpressionAst] } | ForEach-Object Value
})
foreach ($sdk in @('VMware.Sdk.Vcf.Installer','VMware.Sdk.Vcf.SddcManager','VMware.Sdk.Vcf.Ops')) {
    if ($sdk -notin $imports) { throw "installer handoff does not import $sdk" }
}
Import-Module ./VcfArchitecture.psd1 -Force
$wanted = @('New-VcfArchitecture','New-VcfMigrationPlan','Invoke-VcfInstallerArchitecture')
$actual = @(Get-Command -Module VcfArchitecture | Select-Object -ExpandProperty Name)
foreach ($name in $wanted) { if ($name -notin $actual) { throw "missing exported function $name" } }
"""
    result = subprocess.run(["pwsh", "-NoLogo", "-NoProfile", "-Command", script], cwd=ROOT, text=True, capture_output=True)
    require(result.returncode == 0, f"PowerShell module/manifest validation failed:\n{result.stdout}{result.stderr}")


def generate_fresh_outputs() -> Path:
    output = ROOT / ".verification-output"
    if output.exists():
        shutil.rmtree(output)
    output.mkdir()
    script = r"""
$ErrorActionPreference = 'Stop'
Import-Module ./VcfArchitecture.psd1 -Force
New-VcfArchitecture -RequirementsPath ./fixtures/greenfield-requirements.json -CompatibilityPath ./fixtures/compatibility-snapshot.json -OutputDirectory ./.verification-output
New-VcfMigrationPlan -InventoryPath ./fixtures/estate-inventory.json -CompatibilityPath ./fixtures/compatibility-snapshot.json -OutputPath ./.verification-output/migration-plan.json
"""
    result = subprocess.run(["pwsh", "-NoLogo", "-NoProfile", "-Command", script], cwd=ROOT, text=True, capture_output=True)
    require(result.returncode == 0, f"PowerShell generation failed:\n{result.stdout}{result.stderr}")
    return output


def validate_input_driven_generation(
        requirements: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any],
        openapi: dict[str, Any], architecture_schema: dict[str, Any], migration_schema: dict[str, Any]) -> None:
    variant_root = ROOT / ".verification-variant"
    if variant_root.exists():
        shutil.rmtree(variant_root)
    inputs = variant_root / "inputs"
    output = variant_root / "out"
    inputs.mkdir(parents=True)
    output.mkdir()

    variant_requirements = json.loads(json.dumps(requirements))
    variant_requirements["scenarioId"] = "verification-input-driven"
    variant_requirements["capacity"].update({
        "vcfOperationsMonitoredObjects": 12000,
        "automationManagedProjects": 150,
        "automationConcurrentDeployments": 200,
        "logIngestGbPerDay": 200,
        "logHotRetentionDays": 14,
    })
    variant_requirements["installer"]["sddcId"] = "variant01-m01"
    variant_requirements["installer"]["vcfInstanceName"] = "variant-vcf"
    for network in variant_requirements["installer"]["networks"]:
        if network["networkType"] == "HOST_OVERLAY":
            network["vlanId"] = 2290
        elif network["networkType"] == "VM_MANAGEMENT":
            network["gateway"] = "10.20.3.254"
        elif network["networkType"] == "FLEET_MANAGEMENT":
            network["gateway"] = "10.20.4.254"

    variant_inventory = json.loads(json.dumps(inventory))
    variant_inventory["estateId"] = "verification-estate"
    variant_inventory["components"][0]["version"] = "8.18.0-verification"
    requirements_path = inputs / "requirements.json"
    inventory_path = inputs / "inventory.json"
    requirements_path.write_text(json.dumps(variant_requirements, indent=2) + "\n", encoding="utf-8")
    inventory_path.write_text(json.dumps(variant_inventory, indent=2) + "\n", encoding="utf-8")

    script = r"""
$ErrorActionPreference = 'Stop'
Import-Module ./VcfArchitecture.psd1 -Force
New-VcfArchitecture -RequirementsPath ./.verification-variant/inputs/requirements.json -CompatibilityPath ./fixtures/compatibility-snapshot.json -OutputDirectory ./.verification-variant/out
New-VcfMigrationPlan -InventoryPath ./.verification-variant/inputs/inventory.json -CompatibilityPath ./fixtures/compatibility-snapshot.json -OutputPath ./.verification-variant/out/migration-plan.json
"""
    result = subprocess.run(["pwsh", "-NoLogo", "-NoProfile", "-Command", script], cwd=ROOT, text=True, capture_output=True)
    require(result.returncode == 0, f"PowerShell input-variation generation failed:\n{result.stdout}{result.stderr}")
    variant_sddc = read_json(output / "greenfield-sddc-spec.json")
    variant_architecture = read_json(output / "architecture.json")
    variant_migration = read_json(output / "migration-plan.json")
    validate_with_schema(variant_sddc, openapi["components"]["schemas"]["SddcSpec"], openapi, "input-variation SddcSpec against VCF Installer OpenAPI")
    validate_sddc_semantics(variant_sddc, variant_requirements, snapshot)
    validate_architecture(variant_architecture, variant_requirements, snapshot, architecture_schema)
    validate_migration(variant_migration, variant_inventory, snapshot, migration_schema)


def main() -> int:
    try:
        # Binding requirement: the installer specification's own SddcSpec schema is
        # the first check. Do not move module, fixture, snapshot, or research checks
        # above this call.
        submitted_sddc, openapi = validate_installer_schema_first()

        require(openapi.get("info", {}).get("version") == "9.1.0.0", "protected installer OpenAPI is not version 9.1.0.0")
        requirements = read_json(ROOT / "fixtures" / "greenfield-requirements.json")
        snapshot = read_json(ROOT / "fixtures" / "compatibility-snapshot.json")
        inventory = read_json(ROOT / "fixtures" / "estate-inventory.json")
        architecture_schema = read_json(ROOT / "schemas" / "architecture.schema.json")
        migration_schema = read_json(ROOT / "schemas" / "migration-plan.schema.json")
        architecture = read_json(ROOT / "out" / "architecture.json")
        migration = read_json(ROOT / "out" / "migration-plan.json")

        validate_sddc_semantics(submitted_sddc, requirements, snapshot)
        validate_architecture(architecture, requirements, snapshot, architecture_schema)
        validate_migration(migration, inventory, snapshot, migration_schema)
        validate_research_sources()
        validate_module()

        generated = generate_fresh_outputs()
        generated_sddc = read_json(generated / "greenfield-sddc-spec.json")
        validate_with_schema(generated_sddc, openapi["components"]["schemas"]["SddcSpec"], openapi, "freshly generated SddcSpec against VCF Installer OpenAPI")
        validate_sddc_semantics(generated_sddc, requirements, snapshot)
        validate_architecture(read_json(generated / "architecture.json"), requirements, snapshot, architecture_schema)
        validate_migration(read_json(generated / "migration-plan.json"), inventory, snapshot, migration_schema)
        require(generated_sddc == submitted_sddc, "submitted SddcSpec is not the deterministic module output")
        require(read_json(generated / "architecture.json") == architecture, "submitted architecture manifest is not the deterministic module output")
        require(read_json(generated / "migration-plan.json") == migration, "submitted migration plan is not the deterministic module output")
        validate_input_driven_generation(requirements, inventory, snapshot, openapi, architecture_schema, migration_schema)
        print("PASS: VCF 9.1 architecture, sizing, placement, SDK integration, and migration plan satisfy the protected offline contract")
        return 0
    except (VerificationError, KeyError, StopIteration, TypeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
