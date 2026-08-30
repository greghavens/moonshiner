#!/usr/bin/env python3
"""Deterministic verifier for the generated VCF migration architecture."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "architecture.json"
OPENAPI = ROOT / "specifications/vcf-installer/vcf-installer-openapi.json"
MIGRATION_SCHEMA = ROOT / "schemas/migration-architecture.schema.json"
INVENTORY = ROOT / "estate/inventory.json"
SNAPSHOT = ROOT / "authority/compatibility-snapshot.json"
MODULE = ROOT / "src/VcfFleetArchitecture.psm1"
MANIFEST = ROOT / "src/VcfFleetArchitecture.psd1"
RESEARCH = ROOT / "research/consulted-sources.json"

PINNED_HASHES = {
    "specifications/vcf-installer/vcf-installer-openapi.json": "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d",
    "schemas/migration-architecture.schema.json": "137ebc555244e9637df5c22ede87ad170f34daf17ac93575039e5baf3659b57c",
    "estate/inventory.json": "42b0d2cd12dfd99c6063de86e6d4168476bbbfc903b28a1addf2b49c5b656e72",
    "authority/compatibility-snapshot.json": "e65aaa215c832a57dc416257c4f9ee708bc4cbd9b2d3cae271cfd71481545c21"
}


class VerificationError(Exception):
    pass


def load_json(path: Path) -> Any:
    try:
        display_path = path.relative_to(ROOT)
    except ValueError:
        display_path = path.name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VerificationError(f"missing required file: {display_path}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(f"invalid JSON in {display_path}: {exc}") from exc


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


class SchemaValidator:
    def __init__(self, document: dict[str, Any]):
        self.document = document

    def resolve(self, reference: str) -> dict[str, Any]:
        if not reference.startswith("#/"):
            raise VerificationError(f"unsupported schema reference: {reference}")
        node: Any = self.document
        for token in reference[2:].split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            node = node[token]
        return node

    def validate(self, value: Any, schema: dict[str, Any], path: str = "$") -> None:
        if "$ref" in schema:
            self.validate(value, self.resolve(schema["$ref"]), path)
            return
        for branch in schema.get("allOf", []):
            self.validate(value, branch, path)
        if "const" in schema and value != schema["const"]:
            raise VerificationError(f"{path}: expected constant {schema['const']!r}")
        if "enum" in schema and value not in schema["enum"]:
            raise VerificationError(f"{path}: value {value!r} is not in the allowed enum")
        expected = schema.get("type")
        if expected:
            choices = expected if isinstance(expected, list) else [expected]
            if not any(type_matches(value, choice) for choice in choices):
                raise VerificationError(f"{path}: expected type {expected}, got {type(value).__name__}")
        if isinstance(value, dict):
            for key in schema.get("required", []):
                if key not in value:
                    raise VerificationError(f"{path}: missing required property {key!r}")
            properties = schema.get("properties", {})
            if schema.get("additionalProperties") is False:
                extras = set(value) - set(properties)
                if extras:
                    raise VerificationError(f"{path}: unexpected properties {sorted(extras)}")
            for key, child in value.items():
                if key in properties:
                    self.validate(child, properties[key], f"{path}.{key}")
        if isinstance(value, list):
            if len(value) < schema.get("minItems", 0):
                raise VerificationError(f"{path}: too few items")
            if "maxItems" in schema and len(value) > schema["maxItems"]:
                raise VerificationError(f"{path}: too many items")
            if schema.get("uniqueItems"):
                serialized = [json.dumps(item, sort_keys=True) for item in value]
                if len(serialized) != len(set(serialized)):
                    raise VerificationError(f"{path}: items must be unique")
            if "items" in schema:
                for index, item in enumerate(value):
                    self.validate(item, schema["items"], f"{path}[{index}]")
        if isinstance(value, str):
            if len(value) < schema.get("minLength", 0):
                raise VerificationError(f"{path}: string is too short")
            if "maxLength" in schema and len(value) > schema["maxLength"]:
                raise VerificationError(f"{path}: string is too long")
            if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
                raise VerificationError(f"{path}: does not match pattern {schema['pattern']!r}")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema and value < schema["minimum"]:
                raise VerificationError(f"{path}: below minimum")
            if "maximum" in schema and value > schema["maximum"]:
                raise VerificationError(f"{path}: above maximum")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def verify_pins() -> None:
    for relative, expected in PINNED_HASHES.items():
        actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        require(actual == expected, f"protected authority changed: {relative}")


def verify_research_record() -> None:
    records = load_json(RESEARCH)
    require(isinstance(records, list) and records, "consulted-sources.json must be a non-empty array")
    for index, record in enumerate(records):
        prefix = f"research source {index}"
        require(isinstance(record, dict), f"{prefix} must be an object")
        for field in ("title", "url", "accessedAtUtc", "decisionsInformed"):
            require(field in record, f"{prefix} is missing {field}")
        require(isinstance(record["title"], str) and record["title"].strip(), f"{prefix} has no title")
        require(isinstance(record["url"], str), f"{prefix} URL must be a string")
        parsed = urlparse(record["url"])
        hostname = (parsed.hostname or "").lower()
        require(parsed.scheme == "https" and (hostname == "broadcom.com" or hostname.endswith(".broadcom.com")), f"{prefix} is not an HTTPS Broadcom source")
        require(".invalid" not in hostname, f"{prefix} uses a non-reachable placeholder domain")
        accessed = record["accessedAtUtc"]
        require(isinstance(accessed, str), f"{prefix} accessedAtUtc must be a string")
        try:
            accessed_time = datetime.fromisoformat(accessed[:-1] + "+00:00" if accessed.endswith("Z") else accessed)
        except ValueError as exc:
            raise VerificationError(f"{prefix} accessedAtUtc is not ISO 8601") from exc
        require(accessed_time.tzinfo is not None and accessed_time.utcoffset().total_seconds() == 0, f"{prefix} accessedAtUtc must be UTC")
        decisions = record["decisionsInformed"]
        require(isinstance(decisions, list) and decisions, f"{prefix} must name decisions informed")
        require(all(isinstance(item, str) and item.strip() for item in decisions), f"{prefix} has a blank decision")


def verify_module_generates_artifact(artifact: dict[str, Any]) -> None:
    require(MODULE.is_file(), "missing PowerShell module implementation")
    require(MANIFEST.is_file(), "missing PowerShell module manifest")
    with tempfile.TemporaryDirectory(prefix="vcf-architecture-verify-") as temporary:
        temporary_path = Path(temporary)
        generated = temporary_path / "architecture.json"
        variant_generated = temporary_path / "variant-architecture.json"
        variant_inventory_path = temporary_path / "inventory.json"
        variant_snapshot_path = temporary_path / "compatibility-snapshot.json"

        variant_inventory = json.loads(json.dumps(load_json(INVENTORY)))
        variant_inventory["estateId"] = "verification-estate"
        variant_inventory["dns"]["subdomain"] = "verification.example.com"
        variant_inventory["dns"]["nameservers"] = ["192.0.2.53", "192.0.2.54"]
        variant_inventory["ntpServers"] = ["192.0.2.123"]
        variant_inventory["managementNetwork"] = {"networkType": "MANAGEMENT", "vlanId": 123}
        management_site_id = next(
            option["managementDomainSiteId"]
            for option in load_json(SNAPSHOT)["topologyOptions"]
            if option.get("managementDomainMode") == "convert-existing"
        )
        variant_management_site = next(site for site in variant_inventory["sites"] if site["siteId"] == management_site_id)
        variant_management_site["vcenterHostname"] = "verification-vc.example.com"
        variant_management_site["nsxVipFqdn"] = "verification-nsx.example.com"
        variant_management_site["nsxtManagerHostnames"] = ["verification-nsx01.example.com"]
        variant_inventory_path.write_text(json.dumps(variant_inventory), encoding="utf-8")

        variant_snapshot = json.loads(json.dumps(load_json(SNAPSHOT)))
        variant_snapshot["targetFleet"]["fleetId"] = "verification-vcf-fleet"
        variant_snapshot_path.write_text(json.dumps(variant_snapshot), encoding="utf-8")
        script = r"""
$ErrorActionPreference = 'Stop'
$manifest = Import-PowerShellDataFile -LiteralPath $env:VCF_ARCH_MANIFEST
if ($manifest.RootModule -ne 'VcfFleetArchitecture.psm1') { throw 'manifest RootModule mismatch' }
$requiredModules = @($manifest.RequiredModules | ForEach-Object {
    if ($_ -is [string]) { $_ } else { $_.ModuleName }
})
foreach ($name in @('VMware.Sdk.Vcf.Installer', 'VMware.Sdk.Vcf.SddcManager')) {
    if ($name -notin $requiredModules) { throw "manifest is missing required module $name" }
}
foreach ($name in @('New-VcfFleetArchitecture', 'Test-VcfFleetInstallerSpec')) {
    if ($name -notin @($manifest.FunctionsToExport)) { throw "manifest does not export $name" }
}

Import-Module $env:VCF_ARCH_MANIFEST -Force
$generator = Get-Command New-VcfFleetArchitecture -CommandType Function -ErrorAction Stop
$validator = Get-Command Test-VcfFleetInstallerSpec -CommandType Function -ErrorAction Stop
$validationCommandAsts = @($validator.ScriptBlock.Ast.FindAll({
    param($node) $node -is [System.Management.Automation.Language.CommandAst]
}, $true))
$validationCommands = @($validationCommandAsts | ForEach-Object { $_.GetCommandName() })
foreach ($name in @('ArchitecturePath', 'Server')) {
    if (-not $validator.Parameters.ContainsKey($name)) { throw "installer validation is missing parameter $name" }
}
foreach ($name in @(
    'Get-VcfSddcManagerOperation',
    'Initialize-VcfInstallerSddcSpec',
    'Invoke-VcfInstallerValidateSddcSpec'
)) {
    if ($name -notin $validationCommands) { throw "installer validation does not invoke $name" }
}
$sdkCommands = @($validationCommands | Where-Object {
    $_ -like 'Initialize-VcfInstaller*' -or
    $_ -like 'Invoke-VcfInstaller*' -or
    $_ -eq 'Get-VcfSddcManagerOperation'
} | Sort-Object -Unique)
foreach ($name in $sdkCommands) {
    $resolved = Get-Command $name -ErrorAction Stop
    if ($resolved.ModuleName -notin @('VMware.Sdk.Vcf.Installer', 'VMware.Sdk.Vcf.SddcManager')) {
        throw "$name does not resolve to a required VMware SDK module"
    }
}
$validationInvocation = @($validationCommandAsts | Where-Object {
    $_.GetCommandName() -eq 'Invoke-VcfInstallerValidateSddcSpec'
})
if ($validationInvocation.Count -ne 1) { throw 'expected one installer validation invocation' }
$serverArgument = @($validationInvocation[0].CommandElements | Where-Object {
    $_ -is [System.Management.Automation.Language.CommandParameterAst] -and $_.ParameterName -eq 'Server'
})
if ($serverArgument.Count -ne 1) { throw 'installer validation does not pass the supplied Server' }
foreach ($name in @('Invoke-RestMethod', 'Invoke-WebRequest', 'curl', 'wget')) {
    if ($name -in $validationCommands) { throw "direct HTTP command is not allowed: $name" }
}

& $generator -InventoryPath $env:VCF_ARCH_INVENTORY `
    -CompatibilitySnapshotPath $env:VCF_ARCH_SNAPSHOT `
    -OutputPath $env:VCF_ARCH_GENERATED | Out-Null
& $generator -InventoryPath $env:VCF_ARCH_VARIANT_INVENTORY `
    -CompatibilitySnapshotPath $env:VCF_ARCH_VARIANT_SNAPSHOT `
    -OutputPath $env:VCF_ARCH_VARIANT_GENERATED | Out-Null
"""
        environment = os.environ.copy()
        environment.update(
            {
                "VCF_ARCH_MANIFEST": str(MANIFEST),
                "VCF_ARCH_INVENTORY": str(INVENTORY),
                "VCF_ARCH_SNAPSHOT": str(SNAPSHOT),
                "VCF_ARCH_GENERATED": str(generated),
                "VCF_ARCH_VARIANT_INVENTORY": str(variant_inventory_path),
                "VCF_ARCH_VARIANT_SNAPSHOT": str(variant_snapshot_path),
                "VCF_ARCH_VARIANT_GENERATED": str(variant_generated),
            }
        )
        try:
            result = subprocess.run(
                ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except FileNotFoundError as exc:
            raise VerificationError("pwsh is required to verify the PowerShell deliverable") from exc
        except subprocess.TimeoutExpired as exc:
            raise VerificationError("PowerShell architecture generation timed out") from exc
        details = (result.stderr or result.stdout).strip()
        require(result.returncode == 0, f"PowerShell module verification failed: {details}")
        generated_artifact = load_json(generated)
        require(generated_artifact == artifact, "architecture.json was not generated by the submitted module and pinned inputs")
        variant_artifact = load_json(variant_generated)
        require(variant_artifact["migrationPlan"]["estateId"] == variant_inventory["estateId"], "generator does not read inventory estateId")
        require(variant_artifact["migrationPlan"]["targetFleet"]["fleetId"] == variant_snapshot["targetFleet"]["fleetId"], "generator does not read snapshot fleetId")
        require(variant_artifact["dnsSpec"] == variant_inventory["dns"], "generator does not read inventory DNS settings")
        require(variant_artifact["ntpServers"] == variant_inventory["ntpServers"], "generator does not read inventory NTP settings")
        require(variant_artifact["networkSpecs"] == [variant_inventory["managementNetwork"]], "generator does not read inventory management network")
        require(variant_artifact["vcenterSpec"]["vcenterHostname"] == variant_management_site["vcenterHostname"], "generator does not read management vCenter identity")
        require(variant_artifact["nsxtSpec"]["vipFqdn"] == variant_management_site["nsxVipFqdn"], "generator does not read management NSX identity")
        require([item["hostname"] for item in variant_artifact["nsxtSpec"]["nsxtManagers"]] == variant_management_site["nsxtManagerHostnames"], "generator does not read NSX manager identities")


def verify() -> None:
    # The artifact's SddcSpec validation is deliberately the first validation.
    artifact = load_json(ARTIFACT)
    openapi = load_json(OPENAPI)
    sddc_schema = openapi["components"]["schemas"]["SddcSpec"]
    SchemaValidator(openapi).validate(artifact, sddc_schema)

    # Only after SddcSpec validation may fixture/snapshot-derived checks begin.
    require(openapi.get("info", {}).get("version") == "9.1.0.0", "installer OpenAPI is not version 9.1.0.0")
    verify_pins()
    migration_schema = load_json(MIGRATION_SCHEMA)
    SchemaValidator(migration_schema).validate(artifact, migration_schema)
    inventory = load_json(INVENTORY)
    snapshot = load_json(SNAPSHOT)

    plan = artifact["migrationPlan"]
    target = plan["targetFleet"]
    expected_fleet = snapshot["targetFleet"]
    require(plan["estateId"] == inventory["estateId"], "migration plan estateId does not match inventory")
    for key in ("fleetId", "product", "version"):
        require(target[key] == expected_fleet[key], f"targetFleet.{key} does not match snapshot")

    options = {item["id"]: item for item in snapshot["topologyOptions"]}
    require(target["topologyId"] in options, "unknown topologyId")
    option = options[target["topologyId"]]
    licensed = inventory["entitlement"]["licensedCores"]
    require(target["licensedCores"] == licensed, "licensed core count does not match inventory")
    require(target["plannedCores"] == option["coreRequirement"], "planned core count does not match topology")
    require(target["plannedCores"] <= licensed, "selected topology exceeds licensed cores")
    require(option["managementDomainMode"] == "convert-existing", "entitlement requires an existing-site conversion topology")
    require(target["managementDomainSiteId"] == option["managementDomainSiteId"], "wrong management-domain site")
    require(target["workloadDomainSiteIds"] == option["workloadDomainSiteIds"], "wrong workload-domain sites")

    sites = {site["siteId"]: site for site in inventory["sites"]}
    management_site = sites[target["managementDomainSiteId"]]
    require(management_site["canHostManagementDomain"] is True, "selected site cannot host management domain")
    require(artifact["workflowType"] == "VCF", "SddcSpec workflowType must be VCF")
    require(artifact.get("version") == expected_fleet["version"], "SddcSpec target version mismatch")
    require(artifact["vcenterSpec"].get("useExistingDeployment") is True, "SddcSpec must convert an existing vCenter")
    require(artifact["vcenterSpec"]["vcenterHostname"] == management_site["vcenterHostname"], "SddcSpec vCenter is not the selected management site")
    require(artifact.get("nsxtSpec", {}).get("useExistingDeployment") is True, "SddcSpec must import the existing NSX deployment")
    require(artifact["nsxtSpec"]["vipFqdn"] == management_site["nsxVipFqdn"], "SddcSpec NSX is not the selected management site")
    require([item["hostname"] for item in artifact["nsxtSpec"]["nsxtManagers"]] == management_site["nsxtManagerHostnames"], "SddcSpec NSX managers do not match inventory")
    require(artifact["networkSpecs"] == [inventory["managementNetwork"]], "SddcSpec management network does not match inventory")
    require(artifact["dnsSpec"] == inventory["dns"], "SddcSpec DNS does not match inventory")
    require(artifact["ntpServers"] == inventory["ntpServers"], "SddcSpec NTP servers do not match inventory")

    bindings = snapshot["sdkBindings"]
    automation = artifact["automation"]
    require(set(automation["powerCliModules"]) == set(bindings["modules"]), "PowerCLI SDK module bindings mismatch")
    require(set(automation["installerOperations"]) == set(bindings["installerOperations"]), "installer operation bindings mismatch")
    require(automation["sddcManagerDiscovery"] == bindings["sddcManagerDiscovery"], "SDDC Manager discovery binding mismatch")

    components = {item["id"]: item for item in inventory["components"]}
    component_plans = plan["componentPlans"]
    plan_ids = [item["componentId"] for item in component_plans]
    require(len(plan_ids) == len(set(plan_ids)), "componentPlans contains duplicate components")
    require(set(plan_ids) == set(components), "componentPlans must cover every and only inventory component")

    steps = plan["steps"]
    orders = [step["order"] for step in steps]
    require(orders == list(range(1, len(steps) + 1)), "step orders must be contiguous and array-ordered")
    step_by_id = {step["id"]: step for step in steps}
    require(len(step_by_id) == len(steps), "step ids must be unique")
    order_by_id = {step["id"]: step["order"] for step in steps}

    states = {cid: (item["product"], item["version"]) for cid, item in components.items()}
    allowed = {
        (item["componentId"], item["fromProduct"], item["fromVersion"], item["toProduct"], item["toVersion"])
        for item in snapshot["allowedTransitions"]
    }
    final_step: dict[str, int] = {}
    transition_steps: dict[str, list[str]] = {cid: [] for cid in components}
    for step in steps:
        seen_here: set[str] = set()
        for gate in step["gates"]:
            for dependency in gate["satisfiedByStepIds"]:
                require(dependency in order_by_id, f"step {step['id']} references unknown gate step {dependency}")
                require(order_by_id[dependency] < step["order"], f"step {step['id']} has a non-preceding gate dependency")
        for transition in step["componentTransitions"]:
            cid = transition["componentId"]
            require(cid in components, f"transition references unknown component {cid}")
            require(cid not in seen_here, f"step {step['id']} transitions {cid} more than once")
            seen_here.add(cid)
            current = states[cid]
            require(current == (transition["fromProduct"], transition["fromVersion"]), f"non-contiguous transition for {cid}")
            edge = (cid, transition["fromProduct"], transition["fromVersion"], transition["toProduct"], transition["toVersion"])
            require(edge in allowed, f"unsupported transition for {cid}: {edge[2]} -> {edge[4]}")
            states[cid] = (transition["toProduct"], transition["toVersion"])
            final_step[cid] = step["order"]
            transition_steps[cid].append(step["id"])

    plans_by_id = {item["componentId"]: item for item in component_plans}
    for cid, component in components.items():
        item = plans_by_id[cid]
        expected_target = snapshot["componentTargets"][cid]
        require(item["siteId"] == component["siteId"], f"site mismatch for {cid}")
        require(item["product"] == component["product"] and item["fromVersion"] == component["version"], f"source identity mismatch for {cid}")
        require(item["targetProduct"] == expected_target["product"] and item["targetVersion"] == expected_target["version"], f"target mismatch for {cid}")
        require(states[cid] == (expected_target["product"], expected_target["version"]), f"steps do not reach target for {cid}")
        require(transition_steps[cid], f"no migration transition names {cid}")
        gates = {gate["ruleId"]: gate["satisfiedByStepId"] for gate in item["gates"]}
        require(len(gates) == len(item["gates"]), f"duplicate component gate for {cid}")
        require(set(gates) == set(snapshot["requiredGatesByComponent"][cid]), f"component gates mismatch for {cid}")
        for rule_id, satisfying_step in gates.items():
            require(satisfying_step in step_by_id, f"unknown satisfying step for {cid}/{rule_id}")
            require(order_by_id[satisfying_step] <= final_step[cid], f"gate {rule_id} is satisfied after {cid} reaches target")

    for rule in snapshot["orderingRules"]:
        kind = rule["type"]
        if kind == "all-final-before-any-final":
            require(max(final_step[cid] for cid in rule["beforeComponentIds"]) < min(final_step[cid] for cid in rule["afterComponentIds"]), f"ordering rule failed: {rule['id']}")
        elif kind == "action-before-final":
            candidates = [step["order"] for step in steps if step["action"] == rule["action"] and step["siteId"] == rule["siteId"]]
            require(len(candidates) == 1, f"expected one {rule['action']} action for {rule['siteId']}")
            require(candidates[0] < min(final_step[cid] for cid in rule["componentIds"]), f"ordering rule failed: {rule['id']}")
        elif kind == "final-chain":
            chain = [final_step[cid] for cid in rule["componentIds"]]
            require(all(left < right for left, right in zip(chain, chain[1:])), f"ordering rule failed: {rule['id']}")
        elif kind == "same-final-step":
            require(len({final_step[cid] for cid in rule["componentIds"]}) == 1, f"coupling rule failed: {rule['id']}")
        else:
            raise VerificationError(f"unknown pinned ordering rule type: {kind}")

    produced: set[str] = set()
    first_boundary = min(step["order"] for step in steps if step["action"] in ("convert", "import"))
    for step in steps:
        if step["order"] < first_boundary:
            produced.update(step["produces"])
    require(produced.issuperset(expected_fleet["requiredManagementServices"]), "required VCF management and licensing services are not deployed before conversion/import")

    verify_research_record()
    verify_module_generates_artifact(artifact)


if __name__ == "__main__":
    try:
        verify()
    except (VerificationError, KeyError, TypeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print("PASS: SddcSpec first; migration architecture matches the pinned fixture and compatibility authority.")
