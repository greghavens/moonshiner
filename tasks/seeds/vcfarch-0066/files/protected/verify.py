#!/usr/bin/env python3
"""Deterministic verifier for the VCF brownfield architecture artifact."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class VerificationError(Exception):
    pass


PINNED_INPUT_HASHES = {
    "estate/inventory.json": "6a4dad7f0ad997053e87c3bb11c5a954be9a0bb30305f63cd14e5c126d482b04",
    "compatibility/compatibility-snapshot.json": "d00fc3a242a0ab6fd2d41aab72dc953150bf72e2dcbb2929be0251786a806f14",
    "schemas/migration-plan.schema.json": "425919925b2cbb9f87dac453edcecf31a1dce8b85d91e6bb486858f2957b1b80",
    "specifications/vcf-installer/vcf-installer-openapi.json": "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d",
}


def fail(message: str) -> None:
    raise VerificationError(message)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"missing required file: {path}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {path}: {exc}")


def verify_research(root: Path) -> None:
    record = read_json(root / "research/consulted-sources.json")
    if not isinstance(record, dict) or not isinstance(record.get("sources"), list):
        fail("research record must be an object with a sources array")
    if not record["sources"]:
        fail("research record must contain at least one consulted source")
    required_fields = ("title", "url", "accessedOn", "claim")
    for index, source in enumerate(record["sources"], start=1):
        if not isinstance(source, dict):
            fail(f"research source {index} must be an object")
        for field in required_fields:
            value = source.get(field)
            if not isinstance(value, str) or not value.strip():
                fail(f"research source {index} has no non-empty {field}")
        parsed = urlparse(source["url"])
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (
            hostname == "broadcom.com" or hostname.endswith(".broadcom.com")
        ):
            fail(f"research source {index} is not an absolute Broadcom HTTPS URL")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", source["accessedOn"]) is None:
            fail(f"research source {index} accessedOn must use YYYY-MM-DD form")
        try:
            date.fromisoformat(source["accessedOn"])
        except ValueError:
            fail(f"research source {index} accessedOn is not an ISO calendar date")


def invoke_planner(
    root: Path, inventory_path: Path, snapshot_path: Path, output_path: Path
) -> dict[str, Any]:
    manifest_path = root / "VcfBrownfieldPlanner/VcfBrownfieldPlanner.psd1"
    module_path = root / "VcfBrownfieldPlanner/VcfBrownfieldPlanner.psm1"
    if not manifest_path.is_file() or not module_path.is_file():
        fail("missing VcfBrownfieldPlanner module manifest or root module")
    script = r"""
param(
    [string]$ModuleManifest,
    [string]$InventoryPath,
    [string]$SnapshotPath,
    [string]$OutputPath
)
$ErrorActionPreference = 'Stop'
$null = Test-ModuleManifest -Path $ModuleManifest -ErrorAction Stop
Import-Module -Name $ModuleManifest -Force -ErrorAction Stop
$command = Get-Command -Name 'New-VcfBrownfieldMigrationPlan' -CommandType Function -ErrorAction Stop
$exported = @(Get-Command -Module $command.ModuleName -CommandType Function | ForEach-Object Name)
if ($exported.Count -ne 1 -or $exported[0] -ne 'New-VcfBrownfieldMigrationPlan') {
    throw 'The module must export exactly New-VcfBrownfieldMigrationPlan.'
}
foreach ($parameterName in @('InventoryPath', 'CompatibilitySnapshotPath', 'OutputPath')) {
    if (-not $command.Parameters.ContainsKey($parameterName)) {
        throw "Missing required command parameter: $parameterName"
    }
}
$null = New-VcfBrownfieldMigrationPlan `
    -InventoryPath $InventoryPath `
    -CompatibilitySnapshotPath $SnapshotPath `
    -OutputPath $OutputPath
if (-not (Test-Path -LiteralPath $OutputPath -PathType Leaf)) {
    throw 'The planner did not create its requested output file.'
}
"""
    with tempfile.TemporaryDirectory(prefix="vcf-planner-runner-") as temp_dir:
        script_path = Path(temp_dir) / "invoke.ps1"
        script_path.write_text(script, encoding="utf-8")
        try:
            result = subprocess.run(
                [
                    "pwsh",
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-File",
                    str(script_path),
                    str(manifest_path),
                    str(inventory_path),
                    str(snapshot_path),
                    str(output_path),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except FileNotFoundError:
            fail("PowerShell is required to exercise VcfBrownfieldPlanner")
        except subprocess.TimeoutExpired:
            fail("VcfBrownfieldPlanner invocation timed out")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        fail(f"VcfBrownfieldPlanner invocation failed: {detail[:1200]}")
    generated = read_json(output_path)
    if not isinstance(generated, dict):
        fail("VcfBrownfieldPlanner output must be a JSON object")
    return generated


def validate_with_powershell(instance: Any, schema: Any, label: str) -> None:
    """Use PowerShell's JSON Schema engine without any network resolution."""
    script = r"""
param([string]$InstancePath, [string]$SchemaPath)
$ErrorActionPreference = 'Stop'
try {
    $instance = Get-Content -LiteralPath $InstancePath -Raw
    $schema = Get-Content -LiteralPath $SchemaPath -Raw
    $valid = Test-Json -Json $instance -Schema $schema -ErrorAction Stop
    if (-not $valid) { exit 2 }
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 2
}
"""
    with tempfile.TemporaryDirectory(prefix="vcf-plan-schema-") as temp_dir:
        temp = Path(temp_dir)
        instance_path = temp / "instance.json"
        schema_path = temp / "schema.json"
        script_path = temp / "validate.ps1"
        instance_path.write_text(json.dumps(instance), encoding="utf-8")
        schema_path.write_text(json.dumps(schema), encoding="utf-8")
        script_path.write_text(script, encoding="utf-8")
        try:
            result = subprocess.run(
                [
                    "pwsh",
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-File",
                    str(script_path),
                    str(instance_path),
                    str(schema_path),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except FileNotFoundError:
            fail("PowerShell is required for JSON Schema validation")
        except subprocess.TimeoutExpired:
            fail(f"{label} schema validation timed out")
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            fail(f"{label} schema validation failed: {detail[:1200]}")


def as_map(items: list[dict[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        value = item[key]
        if value in result:
            fail(f"duplicate {label}: {value}")
        result[value] = item
    return result


def verify_semantics(
    root: Path,
    plan: dict[str, Any],
    inventory: dict[str, Any],
    snapshot: dict[str, Any],
) -> None:
    if plan["estateId"] != inventory["estateId"]:
        fail("estateId does not match inventory")
    if plan["sourceRelease"] != inventory["sourceRelease"]:
        fail("sourceRelease does not match inventory")
    if plan["targetRelease"] != inventory["targetRelease"]:
        fail("targetRelease does not match inventory")

    routes = [
        route
        for route in snapshot["releaseRoutes"]
        if route["from"] == inventory["sourceRelease"]
        and route["to"] == inventory["targetRelease"]
        and route["supported"] is True
    ]
    if len(routes) != 1:
        fail("pinned snapshot has no unique supported release route")
    if plan["releasePath"] != routes[0]["hops"]:
        fail("releasePath is not the exact supported pinned route")

    installer_path = root / "specifications/vcf-installer/vcf-installer-openapi.json"
    actual_hash = hashlib.sha256(installer_path.read_bytes()).hexdigest()
    if actual_hash != snapshot["installerSpec"]["sha256"]:
        fail("pinned installer specification hash does not match the snapshot")

    expected_gates = as_map(snapshot["gates"], "id", "snapshot gate ID")
    actual_gates = as_map(plan["gates"], "id", "plan gate ID")
    if actual_gates != expected_gates:
        fail("plan gates must exactly match the pinned technical gates")

    all_input_components = inventory["components"] + snapshot["targetOnlyComponents"]
    expected_inputs = as_map(all_input_components, "id", "input component ID")
    actual_components = as_map(plan["components"], "id", "plan component ID")
    if set(actual_components) != set(expected_inputs):
        missing = sorted(set(expected_inputs) - set(actual_components))
        extra = sorted(set(actual_components) - set(expected_inputs))
        fail(f"component coverage differs from fixture/snapshot; missing={missing}, extra={extra}")

    transitions = as_map(snapshot["componentTransitions"], "type", "transition type")
    for component_id, source in expected_inputs.items():
        actual = actual_components[component_id]
        transition = transitions.get(source["type"])
        if transition is None:
            fail(f"no pinned transition for {component_id} ({source['type']})")
        expected = {
            "id": source["id"],
            "type": source["type"],
            "name": source["name"],
            "siteId": source["siteId"],
            "currentVersion": source["currentVersion"],
            "targetVersion": transition["to"],
            "gateIds": transition["requiredGateIds"],
        }
        if actual != expected:
            fail(f"component entry does not match its pinned transition: {component_id}")

    expected_by_stage: dict[int, list[str]] = defaultdict(list)
    for source in all_input_components:
        expected_by_stage[transitions[source["type"]]["stage"]].append(source["id"])
    for transition in snapshot["componentTransitions"]:
        if "componentOrder" in transition:
            stage = transition["stage"]
            if set(transition["componentOrder"]) != set(expected_by_stage[stage]):
                fail(f"pinned componentOrder does not cover stage {stage}")
            expected_by_stage[stage] = transition["componentOrder"]

    ordered_transitions = sorted(snapshot["componentTransitions"], key=lambda item: item["stage"])
    expected_stages = [transition["stage"] for transition in ordered_transitions]
    if expected_stages != list(range(1, len(ordered_transitions) + 1)):
        fail("snapshot stages are not contiguous")
    if len(plan["steps"]) != len(ordered_transitions):
        fail("plan must contain exactly one ordered step per pinned stage")

    covered: list[str] = []
    prior_step_id: str | None = None
    witness_step_order: int | None = None
    host_step_order: int | None = None
    vcenter_step_order: int | None = None
    for order, (step, transition) in enumerate(zip(plan["steps"], ordered_transitions), start=1):
        expected_step_id = f"STEP-{order:02d}"
        expected_depends = [] if prior_step_id is None else [prior_step_id]
        expected_step = {
            "order": order,
            "id": expected_step_id,
            "action": transition["action"],
            "executionMode": transition["executionMode"],
            "componentIds": expected_by_stage[transition["stage"]],
            "fromVersion": transition["from"],
            "toVersion": transition["to"],
            "gateIds": transition["requiredGateIds"],
            "dependsOn": expected_depends,
        }
        if step != expected_step:
            fail(f"step {expected_step_id} does not match the pinned transition and ordering")
        covered.extend(step["componentIds"])
        prior_step_id = expected_step_id
        if transition["type"] == "VSAN_WITNESS":
            witness_step_order = order
        elif transition["type"] == "ESXI_HOST":
            host_step_order = order
        elif transition["type"] == "VCENTER":
            vcenter_step_order = order
    if len(covered) != len(set(covered)) or set(covered) != set(expected_inputs):
        fail("migration steps must cover every component exactly once")
    if not (
        vcenter_step_order is not None
        and witness_step_order is not None
        and host_step_order is not None
        and vcenter_step_order < witness_step_order < host_step_order
    ):
        fail("dedicated witness must be sequenced after vCenter and before data hosts")

    rules = snapshot["topologyRules"]
    topology = plan["topology"]
    if topology["managementDomainId"] != inventory["managementDomain"]["id"]:
        fail("topology management domain does not match inventory")
    if topology["stretched"] is not True:
        fail("management domain must remain stretched")
    for field in ("preferredSiteId", "secondarySiteId", "witnessSiteId"):
        if topology[field] != rules[field]:
            fail(f"topology {field} does not match pinned placement")
    if len({topology["preferredSiteId"], topology["secondarySiteId"], topology["witnessSiteId"]}) != 3:
        fail("preferred, secondary, and witness sites must be distinct")

    host_ids_by_site: dict[str, list[str]] = defaultdict(list)
    for component in inventory["components"]:
        if component["type"] == "ESXI_HOST":
            host_ids_by_site[component["siteId"]].append(component["id"])
    witness = inventory["witness"]
    sites = as_map(inventory["sites"], "id", "inventory site ID")
    preferred_fault_domain_id = sites[rules["preferredSiteId"]]["faultDomainId"]
    secondary_fault_domain_id = sites[rules["secondarySiteId"]]["faultDomainId"]
    expected_fault_domains = {
        preferred_fault_domain_id: {
            "id": preferred_fault_domain_id,
            "siteId": rules["preferredSiteId"],
            "role": "PREFERRED_DATA",
            "componentIds": host_ids_by_site[rules["preferredSiteId"]],
        },
        secondary_fault_domain_id: {
            "id": secondary_fault_domain_id,
            "siteId": rules["secondarySiteId"],
            "role": "SECONDARY_DATA",
            "componentIds": host_ids_by_site[rules["secondarySiteId"]],
        },
        rules["witnessFaultDomainId"]: {
            "id": rules["witnessFaultDomainId"],
            "siteId": rules["witnessSiteId"],
            "role": "WITNESS",
            "componentIds": [witness["componentId"]],
        },
    }
    actual_fault_domains = as_map(topology["faultDomains"], "id", "fault domain ID")
    if actual_fault_domains != expected_fault_domains:
        fail("fault-domain membership or roles do not match the estate topology")
    expected_witness = {
        "componentId": witness["componentId"],
        "siteId": rules["witnessSiteId"],
        "faultDomainId": rules["witnessFaultDomainId"],
        "dedicated": True,
        "shared": False,
        "placement": "THIRD_SITE_OUTSIDE_DATA_FAULT_DOMAINS",
    }
    if topology["witness"] != expected_witness:
        fail("witness placement does not match the dedicated third-site design")

    spec = plan["targetSddcSpec"]
    management = inventory["managementDomain"]
    if spec.get("sddcId") != management["id"] or spec.get("version") != inventory["targetRelease"]:
        fail("targetSddcSpec identity or version does not match the estate target")
    if spec.get("workflowType") != "VCF":
        fail("targetSddcSpec workflowType must be VCF")
    vcenter = spec.get("vcenterSpec", {})
    if (
        vcenter.get("vcenterHostname") != management["vcenterFqdn"]
        or vcenter.get("useExistingDeployment") is not True
        or vcenter.get("version") != transitions["VCENTER"]["to"]
    ):
        fail("targetSddcSpec must preserve and target the existing vCenter")
    nsx = spec.get("nsxtSpec", {})
    expected_nsx_hosts = sorted(
        component["fqdn"]
        for component in inventory["components"]
        if component["type"] == "NSX_MANAGER"
    )
    actual_nsx_hosts = sorted(item.get("hostname") for item in nsx.get("nsxtManagers", []))
    if (
        actual_nsx_hosts != expected_nsx_hosts
        or nsx.get("vipFqdn") != management["nsxVipFqdn"]
        or nsx.get("useExistingDeployment") is not True
        or nsx.get("version") != transitions["NSX_MANAGER"]["to"]
    ):
        fail("targetSddcSpec must preserve and target the existing NSX cluster")
    expected_hostnames = sorted(
        component["fqdn"]
        for component in inventory["components"]
        if component["type"] == "ESXI_HOST"
    )
    actual_hostnames = sorted(item.get("hostname") for item in spec.get("hostSpecs", []))
    if actual_hostnames != expected_hostnames:
        fail("targetSddcSpec hostSpecs do not cover all data hosts")
    if spec.get("dnsSpec") != inventory["dns"]:
        fail("targetSddcSpec DNS differs from inventory")
    if spec.get("ntpServers") != inventory["ntpServers"]:
        fail("targetSddcSpec NTP servers differ from inventory")
    expected_networks = {
        item["networkType"]: item for item in inventory["networks"]
    }
    actual_networks = {
        item.get("networkType"): {
            key: item.get(key) for key in ("networkType", "vlanId", "subnet", "gateway", "mtu")
        }
        for item in spec.get("networkSpecs", [])
    }
    if actual_networks != expected_networks:
        fail("targetSddcSpec networks differ from inventory")
    sddc_manager = spec.get("sddcManagerSpec", {})
    if (
        sddc_manager.get("hostname") != management["sddcManagerFqdn"]
        or sddc_manager.get("useExistingDeployment") is not True
        or sddc_manager.get("version") != transitions["SDDC_MANAGER"]["to"]
    ):
        fail("targetSddcSpec must preserve and target the existing SDDC Manager")
    if spec.get("clusterSpec") != {
        "datacenterName": management["datacenterName"],
        "clusterName": management["clusterName"],
    }:
        fail("targetSddcSpec cluster identity differs from inventory")
    if spec.get("datastoreSpec") != {"existingDatastoreName": management["existingDatastoreName"]}:
        fail("targetSddcSpec must retain the existing stretched datastore")


def main() -> int:
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd().resolve()
    artifact_path = root / "output/migration-plan.json"
    installer_path = root / "specifications/vcf-installer/vcf-installer-openapi.json"

    # Establish that validation uses the protected grading authority rather than
    # inputs altered by a solution.
    for relative_path, expected_hash in PINNED_INPUT_HASHES.items():
        pinned_path = root / relative_path
        try:
            actual_hash = hashlib.sha256(pinned_path.read_bytes()).hexdigest()
        except FileNotFoundError:
            fail(f"missing pinned verifier input: {relative_path}")
        if actual_hash != expected_hash:
            fail(f"pinned verifier input was modified: {relative_path}")

    # The required first substantive check is the embedded target design against
    # the installer specification's own SddcSpec schema.
    plan = read_json(artifact_path)
    if not isinstance(plan, dict) or not isinstance(plan.get("targetSddcSpec"), dict):
        fail("artifact must contain an object-valued targetSddcSpec")
    installer = read_json(installer_path)
    try:
        installer_root_schema = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "$ref": "#/components/schemas/SddcSpec",
            "components": installer["components"],
        }
    except (KeyError, TypeError):
        fail("pinned installer specification does not contain components.schemas.SddcSpec")
    validate_with_powershell(
        plan["targetSddcSpec"], installer_root_schema, "installer SddcSpec"
    )

    # Only after SddcSpec succeeds may the fixed plan schema and pinned semantics run.
    migration_schema = read_json(root / "schemas/migration-plan.schema.json")
    validate_with_powershell(plan, migration_schema, "migration plan")
    inventory = read_json(root / "estate/inventory.json")
    snapshot = read_json(root / "compatibility/compatibility-snapshot.json")
    verify_semantics(root, plan, inventory, snapshot)

    verify_research(root)
    with tempfile.TemporaryDirectory(prefix="vcf-planner-output-") as temp_dir:
        temp = Path(temp_dir)
        generated = invoke_planner(
            root,
            root / "estate/inventory.json",
            root / "compatibility/compatibility-snapshot.json",
            temp / "migration-plan.json",
        )
        if generated != plan:
            fail("the module does not reproduce output/migration-plan.json from the supplied inputs")

        varied_inventory = json.loads(json.dumps(inventory))
        varied_snapshot = json.loads(json.dumps(snapshot))
        varied_inventory["estateId"] = "input-variation-estate"
        varied_inventory["dns"] = {
            "subdomain": "variation.example.net",
            "nameservers": ["192.0.2.53"],
        }
        varied_host = next(
            component
            for component in varied_inventory["components"]
            if component["type"] == "ESXI_HOST"
        )
        varied_host["fqdn"] = "variation-host.example.net"
        varied_snapshot["gates"][0]["condition"] = "INPUT_VARIATION_GATE_CONDITION"
        varied_nsx_transition = next(
            transition
            for transition in varied_snapshot["componentTransitions"]
            if transition["type"] == "NSX_MANAGER"
        )
        varied_nsx_transition["to"] = "9.1.0.0-99999999"
        varied_inventory_path = temp / "varied-inventory.json"
        varied_snapshot_path = temp / "varied-snapshot.json"
        varied_inventory_path.write_text(json.dumps(varied_inventory), encoding="utf-8")
        varied_snapshot_path.write_text(json.dumps(varied_snapshot), encoding="utf-8")
        varied = invoke_planner(
            root,
            varied_inventory_path,
            varied_snapshot_path,
            temp / "varied-plan.json",
        )
        if (
            varied.get("estateId") != varied_inventory["estateId"]
            or varied.get("targetSddcSpec", {}).get("vcfInstanceName")
            != varied_inventory["estateId"]
            or varied.get("targetSddcSpec", {}).get("dnsSpec") != varied_inventory["dns"]
            or "variation-host.example.net"
            not in {
                host.get("hostname")
                for host in varied.get("targetSddcSpec", {}).get("hostSpecs", [])
            }
            or varied.get("gates", [{}])[0].get("condition")
            != "INPUT_VARIATION_GATE_CONDITION"
            or varied.get("targetSddcSpec", {}).get("nsxtSpec", {}).get("version")
            != "9.1.0.0-99999999"
        ):
            fail("the module does not derive its plan from both supplied input files")

    print(
        "PASS: artifact, research record, and input-driven PowerShell planner match "
        "the pinned VCF architecture authority"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
