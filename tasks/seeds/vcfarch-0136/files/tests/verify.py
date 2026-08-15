#!/usr/bin/env python3
"""Deterministic acceptance checks for the mixed-estate VCF architecture."""

from __future__ import annotations

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
ARCHITECTURE = ROOT / "artifacts" / "architecture.json"
RESEARCH = ROOT / "artifacts" / "research-sources.json"
ESTATE = ROOT / "fixtures" / "estate.json"
SNAPSHOT = ROOT / "compatibility" / "vcf-9.1-snapshot.json"
OPENAPI = ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
PLAN_SCHEMA = ROOT / "schemas" / "migration-plan.schema.json"
MODULE_DIR = ROOT / "VcfMixedEstateArchitecture"
MANIFEST = MODULE_DIR / "VcfMixedEstateArchitecture.psd1"
MODULE = MODULE_DIR / "VcfMixedEstateArchitecture.psm1"


class ContractError(AssertionError):
    pass


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ContractError(f"missing required file: {path.relative_to(ROOT)}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"invalid UTF-8 JSON in {path.relative_to(ROOT)}: {exc}") from exc


def pointer(document: Any, reference: str) -> Any:
    if not reference.startswith("#/"):
        raise ContractError(f"only local JSON references are supported: {reference}")
    current = document
    for raw in reference[2:].split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        try:
            current = current[key]
        except (KeyError, TypeError) as exc:
            raise ContractError(f"unresolvable schema reference: {reference}") from exc
    return current


def type_matches(value: Any, expected: str) -> bool:
    return {
        "object": lambda: isinstance(value, dict),
        "array": lambda: isinstance(value, list),
        "string": lambda: isinstance(value, str),
        "integer": lambda: isinstance(value, int) and not isinstance(value, bool),
        "number": lambda: isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": lambda: isinstance(value, bool),
        "null": lambda: value is None,
    }.get(expected, lambda: True)()


def validate_schema(value: Any, schema: dict[str, Any], root: dict[str, Any], path: str) -> None:
    """Validate the JSON Schema/OpenAPI keywords used by the bundled contracts."""
    if "$ref" in schema:
        validate_schema(value, pointer(root, schema["$ref"]), root, path)
        return
    if value is None and schema.get("nullable") is True:
        return
    if "const" in schema and value != schema["const"]:
        raise ContractError(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise ContractError(f"{path}: {value!r} is not one of {schema['enum']!r}")
    expected = schema.get("type")
    if expected is not None:
        choices = expected if isinstance(expected, list) else [expected]
        if not any(type_matches(value, item) for item in choices):
            raise ContractError(f"{path}: expected schema type {expected!r}")
    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                raise ContractError(f"{path}: missing required property {name!r}")
        properties = schema.get("properties", {})
        for name, child in value.items():
            if name in properties:
                validate_schema(child, properties[name], root, f"{path}.{name}")
            elif schema.get("additionalProperties") is False:
                raise ContractError(f"{path}: additional property {name!r} is not allowed")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_schema(child, schema["additionalProperties"], root, f"{path}.{name}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise ContractError(f"{path}: fewer than minItems")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ContractError(f"{path}: more than maxItems")
        if schema.get("uniqueItems"):
            normalized = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(normalized) != len(set(normalized)):
                raise ContractError(f"{path}: array items are not unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, child in enumerate(value):
                validate_schema(child, item_schema, root, f"{path}[{index}]")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise ContractError(f"{path}: shorter than minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ContractError(f"{path}: longer than maxLength")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise ContractError(f"{path}: does not match pattern {schema['pattern']!r}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ContractError(f"{path}: below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ContractError(f"{path}: above maximum")


def validate_sddc_first() -> tuple[dict[str, Any], dict[str, Any]]:
    """The installer specification check is intentionally the first acceptance check."""
    architecture = load_json(ARCHITECTURE)
    try:
        sddc_spec = architecture["greenfield"]["sddcSpec"]
    except (KeyError, TypeError) as exc:
        raise ContractError("architecture.greenfield.sddcSpec is required") from exc
    openapi = load_json(OPENAPI)
    try:
        schema = openapi["components"]["schemas"]["SddcSpec"]
    except (KeyError, TypeError) as exc:
        raise ContractError("bundled OpenAPI document has no SddcSpec schema") from exc
    validate_schema(sddc_spec, schema, openapi, "$.greenfield.sddcSpec")
    return architecture, sddc_spec


def forbidden_gate(component: dict[str, Any], target: str, snapshot: dict[str, Any]) -> str | None:
    for rule in snapshot["forbiddenInPlaceTransitions"]:
        if (rule["componentType"], rule["fromVersion"], rule["toVersion"]) == (
            component["type"], component["version"], target
        ):
            return rule["gateId"]
    return None


def check_research_record() -> None:
    record = load_json(RESEARCH)
    if not isinstance(record, dict) or not {"consultedAt", "sources"} <= set(record):
        raise ContractError("research-sources.json must contain consultedAt and sources")
    consulted_at = record["consultedAt"]
    if not isinstance(consulted_at, str) or not consulted_at:
        raise ContractError("research consultedAt must be a non-empty timestamp")
    try:
        parsed_time = datetime.fromisoformat(consulted_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError("research consultedAt must be an ISO-8601 timestamp") from exc
    if parsed_time.tzinfo is None:
        raise ContractError("research consultedAt timestamp must include a timezone")

    sources = record["sources"]
    if not isinstance(sources, list) or not sources:
        raise ContractError("research sources must be a non-empty array")
    required = {"publisher", "title", "url", "usedFor"}
    for index, source in enumerate(sources):
        path = f"research sources[{index}]"
        if not isinstance(source, dict) or not required <= set(source):
            raise ContractError(f"{path} must contain publisher, title, url, and usedFor")
        for field in ("publisher", "title", "url"):
            if not isinstance(source[field], str) or not source[field].strip():
                raise ContractError(f"{path}.{field} must be a non-empty string")
        used_for = source["usedFor"]
        if not isinstance(used_for, list) or not used_for or not all(
            isinstance(item, str) and item.strip() for item in used_for
        ):
            raise ContractError(f"{path}.usedFor must be a non-empty string array")
        parsed_url = urlparse(source["url"])
        hostname = (parsed_url.hostname or "").lower()
        if parsed_url.scheme != "https" or not (
            hostname == "broadcom.com" or hostname.endswith(".broadcom.com")
        ):
            raise ContractError(f"{path}.url must be an HTTPS Broadcom URL")


def check_sddc_content(
    architecture: dict[str, Any], sddc: dict[str, Any], estate: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    greenfield = estate["greenfield"]
    required = {
        "sddcId": greenfield["sddcId"],
        "workflowType": "VCF",
        "version": snapshot["targetVcfVersion"],
        "vcfInstanceName": greenfield["vcfInstanceName"],
    }
    for key, expected in required.items():
        if sddc.get(key) != expected:
            raise ContractError(f"SddcSpec {key} must be {expected!r}")
    if sddc.get("vcenterSpec", {}).get("version") != snapshot["targetVersions"]["VCENTER"]:
        raise ContractError("SddcSpec vCenter target does not match the compatibility snapshot")
    if sddc.get("nsxtSpec", {}).get("version") != snapshot["targetVersions"]["NSX_T_MANAGER"]:
        raise ContractError("SddcSpec NSX target does not match the compatibility snapshot")
    if sddc.get("sddcManagerSpec", {}).get("version") != snapshot["targetVersions"]["SDDC_MANAGER_VCF"]:
        raise ContractError("SddcSpec SDDC Manager target does not match the compatibility snapshot")

    expected_hosts_by_site = greenfield["dataHostsBySite"]
    expected_hosts = {host for hosts in expected_hosts_by_site.values() for host in hosts}
    actual_hosts = {host.get("hostname") for host in sddc.get("hostSpecs", [])}
    if actual_hosts != expected_hosts or len(sddc.get("hostSpecs", [])) != len(expected_hosts):
        raise ContractError("SddcSpec must name every target management host exactly once")

    expected_networks = {
        (item["networkType"], item["vlanId"], item["subnet"], item["gateway"], item["mtu"])
        for item in greenfield["networks"]
    }
    actual_networks = {
        (item.get("networkType"), item.get("vlanId"), item.get("subnet"), item.get("gateway"), item.get("mtu"))
        for item in sddc.get("networkSpecs", [])
    }
    if actual_networks != expected_networks:
        raise ContractError("SddcSpec networkSpecs do not match the estate fixture")

    topology = architecture.get("greenfield", {}).get("topology")
    if not isinstance(topology, dict):
        raise ContractError("greenfield.topology is required")
    domain = topology.get("managementDomain", {})
    if domain.get("stretched") is not True:
        raise ContractError("the target management domain must be stretched")
    if domain.get("dataSites") != estate["managementDomain"]["dataSites"]:
        raise ContractError("the topology must preserve the two ordered data sites")
    if domain.get("dataHostsBySite") != expected_hosts_by_site:
        raise ContractError("the topology must map every target host to its data site")
    if domain.get("dataHostVersion") != snapshot["targetVersions"]["ESX_HOST"]:
        raise ContractError("the topology data-host target version is incorrect")

    sites = {item["id"]: item for item in estate["sites"]}
    witness = topology.get("witness", {})
    witness_site = witness.get("site")
    if witness.get("componentId") != snapshot["witness"]["newComponentId"]:
        raise ContractError("the topology must name the dedicated target witness")
    if witness.get("version") != snapshot["witness"]["targetVersion"]:
        raise ContractError("the target witness version is incorrect")
    if witness_site not in sites or sites[witness_site]["role"] != snapshot["witness"]["requiredSiteRole"]:
        raise ContractError("the target witness must be in the independent witness site")
    if witness_site in domain.get("dataSites", []):
        raise ContractError("the target witness cannot be in either data site")
    if witness.get("dedicated") is not True or witness.get("memberOfDataCluster") is not False:
        raise ContractError("the target witness must be dedicated and outside the data cluster")
    legacy = topology.get("legacySharedWitness", {})
    if legacy != {
        "componentId": estate["legacyCluster"]["sharedWitnessId"],
        "site": witness_site,
        "servesCluster": estate["legacyCluster"]["id"],
        "disposition": snapshot["witness"]["legacySharedWitnessDisposition"],
    }:
        raise ContractError("the old shared witness must remain with the retained legacy cluster")


def check_plan(architecture: dict[str, Any], estate: dict[str, Any], snapshot: dict[str, Any]) -> None:
    plan = architecture.get("migrationPlan")
    if not isinstance(plan, dict):
        raise ContractError("architecture.migrationPlan is required")
    schema = load_json(PLAN_SCHEMA)
    validate_schema(plan, schema, schema, "$.migrationPlan")
    if plan["sourceEstateId"] != estate["estateId"]:
        raise ContractError("migrationPlan sourceEstateId is incorrect")
    if plan["targetVcfVersion"] != snapshot["targetVcfVersion"]:
        raise ContractError("migrationPlan targetVcfVersion is incorrect")
    if plan["strategy"] != snapshot["route"]["strategy"]:
        raise ContractError("migrationPlan must route through parallel greenfield")

    steps = plan["steps"]
    orders = [step["order"] for step in steps]
    if orders != sorted(set(orders)):
        raise ContractError("migration steps must have unique, strictly increasing order values")
    if [step["phase"] for step in steps] != snapshot["route"]["phaseOrder"]:
        raise ContractError("migration phases are not in the pinned dependency order")

    transitions = [item for step in steps for item in step["transitions"]]
    by_id: dict[str, dict[str, Any]] = {}
    for transition in transitions:
        component_id = transition["componentId"]
        if component_id in by_id:
            raise ContractError(f"component {component_id!r} appears more than once in the plan")
        by_id[component_id] = transition
    components = {item["id"]: item for item in estate["components"]}
    if set(by_id) != set(components):
        missing = sorted(set(components) - set(by_id))
        extra = sorted(set(by_id) - set(components))
        raise ContractError(f"migration component coverage differs (missing={missing}, extra={extra})")

    replacement_gate = snapshot["route"]["replacementGateId"]
    retention_gate = snapshot["route"]["retentionGateId"]
    for component_id, component in components.items():
        transition = by_id[component_id]
        if transition["componentType"] != component["type"]:
            raise ContractError(f"{component_id}: component type changed")
        if transition["currentVersion"] != component["version"]:
            raise ContractError(f"{component_id}: current version is not the inventory version")
        gates = set(transition["gates"])
        if component["migrationScope"] == "retain":
            if transition["targetVersion"] != component["version"] or transition["disposition"] != "retain":
                raise ContractError(f"{component_id}: retained component target/disposition is incorrect")
            if gates != {retention_gate}:
                raise ContractError(f"{component_id}: legacy-retention compatibility gates are incorrect")
        else:
            target = snapshot["targetVersions"][component["type"]]
            if transition["targetVersion"] != target or transition["disposition"] != "parallel-replace":
                raise ContractError(f"{component_id}: replacement target/disposition is incorrect")
            blocked = forbidden_gate(component, target, snapshot)
            expected_gates = {replacement_gate}
            if blocked:
                expected_gates.add(blocked)
            if gates != expected_gates:
                raise ContractError(f"{component_id}: replacement compatibility gates are incorrect")

    allowed_gates = {
        replacement_gate,
        retention_gate,
        snapshot["witness"]["placementGateId"],
        *(rule["gateId"] for rule in snapshot["forbiddenInPlaceTransitions"]),
    }
    used_step_gates = {gate for step in steps for gate in step["gates"]}
    if not used_step_gates <= allowed_gates:
        raise ContractError("migration steps contain gates not derived from the compatibility snapshot")

    additions = [(step["phase"], item) for step in steps for item in step["additions"]]
    if len(additions) != 1:
        raise ContractError("the plan must contain exactly one new dedicated witness")
    phase, addition = additions[0]
    witness_rule = snapshot["witness"]
    if phase != "place-dedicated-witness":
        raise ContractError("the dedicated witness must be added in the witness-placement phase")
    witness_site = architecture["greenfield"]["topology"]["witness"]["site"]
    expected_addition = {
        "componentId": witness_rule["newComponentId"],
        "componentType": "VSAN_WITNESS",
        "currentVersion": "not-deployed",
        "targetVersion": witness_rule["targetVersion"],
        "site": witness_site,
        "gates": [witness_rule["placementGateId"]],
    }
    if addition != expected_addition:
        raise ContractError("the dedicated-witness addition does not match the pinned placement rule")


def check_module_and_reproduction(architecture: dict[str, Any]) -> None:
    if not MANIFEST.is_file() or not MODULE.is_file():
        raise ContractError("PowerShell module manifest and implementation are required")
    source = MODULE.read_text(encoding="utf-8")
    required_sdk_builders = (
        "Initialize-VcfInstallerSddcSpec",
        "Initialize-VcfInstallerSddcHostSpec",
        "Initialize-VcfInstallerSddcVcenterSpec",
        "Initialize-VcfInstallerSddcNetworkSpec",
        "Initialize-VcfInstallerDnsSpec",
    )
    for name in required_sdk_builders:
        if not re.search(rf"(?i)\b{re.escape(name)}\b", source):
            raise ContractError(f"module must construct the SddcSpec with {name}")
    forbidden_calls = ("Invoke-WebRequest", "Invoke-RestMethod", "Invoke-VcfInstallerDeploySddc")
    for name in forbidden_calls:
        if re.search(rf"(?i)\b{re.escape(name)}\b", source):
            raise ContractError(f"architecture generation must not make external or appliance calls ({name})")
    vendored = [
        path for path in MODULE_DIR.rglob("*")
        if path.is_file() and path.suffix.lower() in {".dll", ".nupkg"}
    ]
    if vendored:
        raise ContractError("VMware SDK binaries must not be vendored")

    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temp_name:
        generated = Path(temp_name) / "architecture.json"
        script = """
$ErrorActionPreference = 'Stop'
$manifest = Test-ModuleManifest -Path $env:VCFARCH_MANIFEST
$requirement = @($manifest.RequiredModules | Where-Object Name -eq 'VMware.Sdk.Vcf.Installer')
if ($requirement.Count -ne 1) { throw 'manifest must require VMware.Sdk.Vcf.Installer exactly once' }
Import-Module $env:VCFARCH_MANIFEST -Force
$builders = @(
    'Initialize-VcfInstallerSddcSpec',
    'Initialize-VcfInstallerSddcHostSpec',
    'Initialize-VcfInstallerSddcVcenterSpec',
    'Initialize-VcfInstallerSddcNetworkSpec',
    'Initialize-VcfInstallerDnsSpec'
)
$global:VcfArchBuilderCalls = @{}
$breakpoints = foreach ($builder in $builders) {
    $action = { $global:VcfArchBuilderCalls[$builder] = 1 + $global:VcfArchBuilderCalls[$builder] }.GetNewClosure()
    Set-PSBreakpoint -Command $builder -Action $action
}
$command = Get-Command New-VcfMixedEstateArchitecture -ErrorAction Stop
foreach ($name in 'EstatePath','CompatibilityPath','OutputPath') {
    if (-not $command.Parameters.ContainsKey($name)) { throw "missing command parameter $name" }
}
New-VcfMixedEstateArchitecture `
    -EstatePath $env:VCFARCH_ESTATE `
    -CompatibilityPath $env:VCFARCH_SNAPSHOT `
    -OutputPath $env:VCFARCH_OUTPUT
Remove-PSBreakpoint -Breakpoint $breakpoints
foreach ($builder in $builders) {
    if (-not $global:VcfArchBuilderCalls.ContainsKey($builder)) {
        throw "architecture generation did not execute $builder"
    }
}
"""
        module_env = os.environ.copy()
        module_env.update({
            "VCFARCH_MANIFEST": str(MANIFEST),
            "VCFARCH_ESTATE": str(ESTATE),
            "VCFARCH_SNAPSHOT": str(SNAPSHOT),
            "VCFARCH_OUTPUT": str(generated),
        })
        proc = subprocess.run(
            [
                "pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script,
            ],
            cwd=ROOT,
            env=module_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=90,
            check=False,
        )
        if proc.returncode != 0:
            raise ContractError(f"PowerShell module execution failed:\n{proc.stdout}")
        reproduced = load_json(generated)
        if reproduced != architecture:
            raise ContractError("module output does not reproduce artifacts/architecture.json")


def main() -> int:
    try:
        # Do not move any acceptance check ahead of this installer-schema validation.
        architecture, sddc_spec = validate_sddc_first()
        print("PASS installer SddcSpec schema (checked first)")

        estate = load_json(ESTATE)
        snapshot = load_json(SNAPSHOT)
        check_research_record()
        print("PASS live-source research record")
        check_sddc_content(architecture, sddc_spec, estate, snapshot)
        print("PASS greenfield stretched-domain topology")
        check_plan(architecture, estate, snapshot)
        print("PASS pinned mixed-estate migration plan")
        check_module_and_reproduction(architecture)
        print("PASS VMware.Sdk.Vcf module reproduction")
        return 0
    except (ContractError, KeyError, TypeError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
