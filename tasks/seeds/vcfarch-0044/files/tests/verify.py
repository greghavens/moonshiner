#!/usr/bin/env python3
"""Deterministic acceptance verifier for the VCF architecture artifact."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_PATH = ROOT / "output" / "architecture.json"
OPENAPI_PATH = ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
MIGRATION_SCHEMA_PATH = ROOT / "schemas" / "migration-plan.schema.json"
DESIGN_PATH = ROOT / "fixtures" / "design-requirements.json"
ESTATE_PATH = ROOT / "fixtures" / "estate-inventory.json"
SNAPSHOT_PATH = ROOT / "fixtures" / "compatibility-snapshot.json"
MODULE_PATH = ROOT / "VcfArchitecture" / "VcfArchitecture.psd1"
RESEARCH_PATH = ROOT / "research" / "consulted-sources.md"
PINNED_OPENAPI_SHA256 = "a2084a65aab0ac0a5a1625d1a2fdf20b55fc8895ca43fd4389da901d07a4aaef"


class VerificationError(Exception):
    pass


def fail(message: str) -> None:
    raise VerificationError(message)


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        fail(f"missing required file: {path.relative_to(ROOT)}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {path.relative_to(ROOT)}: {exc}")


def json_type_matches(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, True)


def resolve_ref(ref: str, root_schema: dict[str, Any]) -> dict[str, Any]:
    if not ref.startswith("#/"):
        fail(f"unsupported non-local schema reference: {ref}")
    value: Any = root_schema
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, dict) or part not in value:
            fail(f"unresolvable schema reference: {ref}")
        value = value[part]
    if not isinstance(value, dict):
        fail(f"schema reference is not an object: {ref}")
    return value


def validate_json_schema(
    value: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    path: str = "$",
) -> list[str]:
    """Validate the JSON Schema/OpenAPI keywords used by the two pinned schemas."""
    errors: list[str] = []

    if "$ref" in schema:
        return validate_json_schema(value, resolve_ref(schema["$ref"], root_schema), root_schema, path)

    if value is None and schema.get("nullable") is True:
        return errors

    if "allOf" in schema:
        for child in schema["allOf"]:
            errors.extend(validate_json_schema(value, child, root_schema, path))
    if "anyOf" in schema:
        if not any(not validate_json_schema(value, child, root_schema, path) for child in schema["anyOf"]):
            errors.append(f"{path}: does not match anyOf")
    if "oneOf" in schema:
        matches = sum(not validate_json_schema(value, child, root_schema, path) for child in schema["oneOf"])
        if matches != 1:
            errors.append(f"{path}: matches {matches} oneOf branches, expected exactly one")

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {schema['enum']!r}")

    expected_type = schema.get("type")
    if expected_type is not None:
        allowed_types = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(json_type_matches(value, item) for item in allowed_types):
            errors.append(f"{path}: expected type {expected_type!r}, got {type(value).__name__}")
            return errors

    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                errors.append(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        for key, child_value in value.items():
            if key in properties:
                errors.extend(validate_json_schema(child_value, properties[key], root_schema, f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: additional property {key!r} is not allowed")
            elif isinstance(schema.get("additionalProperties"), dict):
                errors.extend(
                    validate_json_schema(
                        child_value,
                        schema["additionalProperties"],
                        root_schema,
                        f"{path}.{key}",
                    )
                )
        if "minProperties" in schema and len(value) < schema["minProperties"]:
            errors.append(f"{path}: has fewer than {schema['minProperties']} properties")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            errors.append(f"{path}: has more than {schema['maxProperties']} properties")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: has fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: has more than {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(encoded) != len(set(encoded)):
                errors.append(f"{path}: items are not unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, child_value in enumerate(value):
                errors.extend(validate_json_schema(child_value, item_schema, root_schema, f"{path}[{index}]"))

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: is shorter than {schema['minLength']} characters")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: is longer than {schema['maxLength']} characters")
        if "pattern" in schema:
            try:
                if re.search(schema["pattern"], value) is None:
                    errors.append(f"{path}: does not match pattern {schema['pattern']!r}")
            except re.error as exc:
                fail(f"invalid pattern in pinned schema at {path}: {exc}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: is below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: exceeds maximum {schema['maximum']}")

    return errors


def assert_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        fail(f"{label}: expected {expected!r}, got {actual!r}")


def assert_true(condition: bool, label: str) -> None:
    if not condition:
        fail(label)


def by_key(items: list[dict[str, Any]], key: str, label: str) -> dict[Any, dict[str, Any]]:
    result: dict[Any, dict[str, Any]] = {}
    for item in items:
        if key not in item:
            fail(f"{label}: item lacks {key!r}")
        if item[key] in result:
            fail(f"{label}: duplicate {key} {item[key]!r}")
        result[item[key]] = item
    return result


def check_research() -> None:
    try:
        text = RESEARCH_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        fail("missing required file: research/consulted-sources.md")

    accessed = re.search(r"Accessed in UTC on (\d{4}-\d{2}-\d{2})\.", text)
    assert_true(accessed is not None, "research must record an ISO UTC access date")
    try:
        date.fromisoformat(accessed.group(1))
    except ValueError:
        fail("research UTC access date is invalid")

    rows: list[list[str]] = []
    for line in text.splitlines():
        if not line.startswith("|") or "https://" not in line:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 4:
            fail("each consulted-source row must have source, URL, fact, and snapshot comparison cells")
        rows.append(cells)

    assert_true(bool(rows), "research must document the requested current Broadcom material")
    urls: list[str] = []
    for index, (source, url, fact, comparison) in enumerate(rows, start=1):
        assert_true(len(source) >= 8, f"research row {index} source title is missing")
        assert_true(len(fact) >= 20, f"research row {index} applied fact is missing")
        assert_true(len(comparison) >= 20, f"research row {index} snapshot comparison is missing")
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        assert_equal(parsed.scheme, "https", f"research row {index} URL scheme")
        assert_true(
            host == "broadcom.com" or host.endswith(".broadcom.com"),
            f"research row {index} is not an authoritative Broadcom URL",
        )
        assert_true(".invalid" not in host and parsed.path, f"research row {index} URL is not reachable-form")
        urls.append(url)
    assert_equal(len(urls), len(set(urls)), "consulted source URLs must be unique")

    findings = " ".join(" ".join(row) for row in rows).lower()
    assert_true("nsx" in findings, "research omits the blocking NSX compatibility fact")
    assert_true(
        re.search(r"back[- ]in[- ]time|newer.{0,80}older|cannot upgrade", findings) is not None,
        "research does not explain why NSX cannot move into the older requested build",
    )
    assert_true("9.0.1" in findings, "research omits the published NSX rebase resolution")
    assert_true("sequence" in findings, "research omits upgrade sequencing material")
    assert_true("interoperab" in findings, "research omits interoperability material")
    assert_true(
        re.search(r"bill[- ]of[- ]materials|component mapping|supported-release mapping", findings) is not None,
        "research omits bill-of-materials or release-to-component mapping material",
    )


def check_greenfield(artifact: dict[str, Any], design: dict[str, Any]) -> None:
    assert_equal(artifact.get("schemaVersion"), "1.0", "artifact schemaVersion")
    assert_equal(artifact.get("designId"), design["designId"], "designId")
    greenfield = artifact.get("greenfieldDesign")
    assert_true(isinstance(greenfield, dict), "greenfieldDesign must be an object")
    assert_equal(greenfield.get("deploymentModel"), "GREENFIELD", "deployment model")
    assert_equal(greenfield.get("targetVersion"), design["targetVersion"], "greenfield target version")

    expected_sites = {
        "primarySite": design["sites"]["primary"],
        "recoverySite": design["sites"]["recovery"],
        "backupReplicationRequired": design["sites"]["backupReplicationRequired"],
        "rpoHours": design["sites"]["rpoHours"],
        "rtoHours": design["sites"]["rtoHours"],
    }
    assert_equal(greenfield.get("siteTopology"), expected_sites, "site topology")

    host_count = design["availability"]["managementHostCount"]
    reserve = design["availability"]["reservedHostFailures"]
    per_host = design["capacity"]["perHost"]
    required = design["capacity"]["requiredUsable"]
    expected_capacity = {
        "hostCount": host_count,
        "reservedHostFailures": reserve,
        "availablePhysicalCores": (host_count - reserve) * per_host["physicalCores"],
        "availableMemoryGiB": (host_count - reserve) * per_host["memoryGiB"],
        "estimatedUsableStorageTiB": round(
            host_count * per_host["rawStorageTiB"] / (design["storage"]["failuresToTolerate"] + 1), 2
        ),
        "requirementsMet": True,
    }
    assert_equal(greenfield.get("capacity"), expected_capacity, "capacity architecture")
    assert_true(
        expected_capacity["availablePhysicalCores"] >= required["physicalCores"],
        "fixture compute requirement is not met",
    )
    assert_true(
        expected_capacity["availableMemoryGiB"] >= required["memoryGiB"],
        "fixture memory requirement is not met",
    )
    assert_true(
        expected_capacity["estimatedUsableStorageTiB"] >= required["storageTiB"],
        "fixture storage requirement is not met",
    )

    expected_availability = {
        "managementClusterHosts": host_count,
        "hostFailuresToTolerate": reserve,
        "nsxManagerNodes": design["availability"]["nsxManagerCount"],
        "vcfOperationsNodes": design["availability"]["vcfOperationsNodeCount"],
        "physicalUplinksPerHost": design["availability"]["physicalUplinksPerHost"],
    }
    assert_equal(greenfield.get("availability"), expected_availability, "availability architecture")

    spec = greenfield["sddcSpec"]
    assert_equal(spec.get("sddcId"), design["sddcId"], "SddcSpec.sddcId")
    assert_equal(spec.get("workflowType"), "VCF", "SddcSpec.workflowType")
    assert_equal(spec.get("version"), design["targetVersion"], "SddcSpec.version")
    assert_equal(spec.get("vcfInstanceName"), design["vcfInstanceName"], "SddcSpec.vcfInstanceName")
    assert_equal([item.get("hostname") for item in spec.get("hostSpecs", [])], design["hosts"], "host inventory")
    for host in spec.get("hostSpecs", []):
        assert_equal(
            host.get("credentials"),
            {"username": "root", "password": design["credentials"]["esxiRootPassword"]},
            f"{host.get('hostname')} credentials",
        )
    assert_equal(spec.get("dnsSpec"), design["dns"], "DNS design")
    assert_equal(spec.get("ntpServers"), design["ntpServers"], "NTP design")
    assert_equal(spec.get("skipEsxThumbprintValidation"), False, "ESXi thumbprint validation")
    assert_equal(spec.get("skipGatewayPingValidation"), False, "gateway validation")

    assert_equal(spec.get("vcenterSpec", {}).get("vcenterHostname"), design["appliances"]["vcenter"], "vCenter host")
    assert_equal(spec.get("vcenterSpec", {}).get("useExistingDeployment"), False, "vCenter deployment mode")
    assert_equal(spec.get("vcenterSpec", {}).get("version"), design["targetVersion"], "vCenter version")
    assert_equal(spec.get("sddcManagerSpec", {}).get("hostname"), design["appliances"]["sddcManager"], "SDDC Manager host")
    assert_equal(spec.get("sddcManagerSpec", {}).get("useExistingDeployment"), False, "SDDC Manager deployment mode")
    assert_equal(spec.get("sddcManagerSpec", {}).get("version"), design["targetVersion"], "SDDC Manager version")
    assert_equal(spec.get("clusterSpec", {}).get("datacenterName"), "CHI01-MGMT-DC", "datacenter name")
    assert_equal(spec.get("clusterSpec", {}).get("clusterName"), "CHI01-MGMT-CLUSTER", "cluster name")

    expected_networks = by_key(design["networks"], "type", "design networks")
    actual_networks = by_key(spec.get("networkSpecs", []), "networkType", "SddcSpec networks")
    assert_equal(set(actual_networks), set(expected_networks), "network types")
    for network_type, expected in expected_networks.items():
        actual = actual_networks[network_type]
        for field in ("vlanId", "subnet", "gateway", "subnetMask", "mtu"):
            assert_equal(actual.get(field), expected[field], f"{network_type} {field}")
        assert_equal(
            actual.get("includeIpAddressRanges"),
            [{"startIpAddress": expected["start"], "endIpAddress": expected["end"]}],
            f"{network_type} IP range",
        )
        network = ipaddress.ip_network(expected["subnet"])
        assert_true(ipaddress.ip_address(expected["start"]) in network, f"{network_type} start is outside subnet")
        assert_true(ipaddress.ip_address(expected["end"]) in network, f"{network_type} end is outside subnet")

    dvs_specs = spec.get("dvsSpecs", [])
    assert_equal(len(dvs_specs), 1, "DVS count")
    dvs = dvs_specs[0]
    assert_equal(dvs.get("dvsName"), design["switch"]["name"], "DVS name")
    assert_equal(dvs.get("mtu"), design["switch"]["mtu"], "DVS MTU")
    assert_equal(set(dvs.get("networks", [])), set(expected_networks), "DVS network membership")
    actual_uplinks = {item["id"]: item["uplink"] for item in dvs.get("vmnicsToUplinks", [])}
    assert_equal(actual_uplinks, design["switch"]["vmnicToUplink"], "DVS uplink mapping")
    nsx_switch = dvs.get("nsxtSwitchConfig", {})
    assert_equal(nsx_switch.get("hostSwitchOperationalMode"), "STANDARD", "NSX host-switch mode")
    assert_equal(
        nsx_switch.get("ipAssignmentType"),
        design["nsxHostOverlay"]["ipAssignmentType"],
        "NSX host-switch IP assignment",
    )
    assert_equal(
        nsx_switch.get("transportZones"),
        [{"name": design["nsxHostOverlay"]["transportZoneName"], "transportType": "OVERLAY"}],
        "NSX transport zone",
    )

    nsx = spec.get("nsxtSpec", {})
    assert_equal(
        [item.get("hostname") for item in nsx.get("nsxtManagers", [])],
        design["appliances"]["nsxManagers"],
        "NSX manager nodes",
    )
    assert_equal(nsx.get("vipFqdn"), design["appliances"]["nsxVip"], "NSX VIP")
    assert_equal(nsx.get("transportVlanId"), design["nsxHostOverlay"]["vlanId"], "NSX transport VLAN")
    assert_equal(nsx.get("useExistingDeployment"), False, "NSX deployment mode")
    assert_equal(nsx.get("version"), design["targetVersion"], "NSX version")
    overlay_subnets = nsx.get("ipAddressPoolSpec", {}).get("subnets", [])
    assert_equal(len(overlay_subnets), 1, "NSX overlay subnet count")
    overlay = overlay_subnets[0]
    assert_equal(overlay.get("cidr"), design["nsxHostOverlay"]["cidr"], "NSX overlay CIDR")
    assert_equal(overlay.get("gateway"), design["nsxHostOverlay"]["gateway"], "NSX overlay gateway")
    assert_equal(
        overlay.get("ipAddressPoolRanges"),
        [{"start": design["nsxHostOverlay"]["start"], "end": design["nsxHostOverlay"]["end"]}],
        "NSX overlay IP range",
    )

    vsan = spec.get("datastoreSpec", {}).get("vsanSpec", {})
    assert_equal(vsan.get("datastoreName"), design["storage"]["datastoreName"], "vSAN datastore")
    assert_equal(vsan.get("esaConfig", {}).get("enabled"), True, "vSAN ESA")
    assert_equal(vsan.get("failuresToTolerate"), design["storage"]["failuresToTolerate"], "vSAN FTT")

    fleet = spec.get("vcfOperationsFleetManagementSpec", {})
    assert_equal(fleet.get("hostname"), design["appliances"]["vcfOperationsFleetManager"], "fleet manager")
    assert_equal(fleet.get("useExistingDeployment"), False, "fleet manager deployment mode")
    assert_equal(fleet.get("version"), design["targetVersion"], "fleet manager version")
    operations = spec.get("vcfOperationsSpec", {})
    assert_equal(operations.get("loadBalancerFqdn"), design["appliances"]["vcfOperationsLoadBalancer"], "Operations load balancer")
    assert_equal(operations.get("useExistingDeployment"), False, "Operations deployment mode")
    assert_equal(operations.get("version"), design["targetVersion"], "Operations version")
    actual_ops_nodes = [{"hostname": item.get("hostname"), "type": item.get("type")} for item in operations.get("nodes", [])]
    assert_equal(actual_ops_nodes, design["appliances"]["vcfOperationsNodes"], "Operations nodes")


def check_migration(artifact: dict[str, Any], estate: dict[str, Any], snapshot: dict[str, Any]) -> None:
    plan = artifact.get("migrationPlan")
    assert_true(isinstance(plan, dict), "migrationPlan must be an object")
    migration_schema = load_json(MIGRATION_SCHEMA_PATH)
    errors = validate_json_schema(plan, migration_schema, migration_schema)
    if errors:
        fail("migration plan schema validation failed:\n  " + "\n  ".join(errors))

    blocking = snapshot["blockingRule"]
    expected_decision = {
        "status": "REBASE_REQUIRED",
        "requestedTargetBundle": estate["requestedTargetBundle"],
        "effectiveTargetBundle": snapshot["effectiveBundle"],
        "blockingComponentId": blocking["componentId"],
        "blockingSourceVersion": blocking["sourceVersion"],
        "reasonCode": blocking["reasonCode"],
        "compatibilityRule": blocking["id"],
    }
    assert_equal(plan.get("estateId"), estate["estateId"], "migration estateId")
    assert_equal(plan.get("decision"), expected_decision, "migration bundle decision")

    inventory = by_key(estate["components"], "id", "estate components")
    rules = by_key(snapshot["migrationRules"], "componentId", "snapshot migration rules")
    steps = by_key(plan["steps"], "componentId", "migration steps")
    assert_equal(set(steps), set(inventory), "planned component coverage")
    assert_equal(set(steps), set(rules), "compatibility rule coverage")
    assert_equal([step["sequence"] for step in plan["steps"]], list(range(1, len(steps) + 1)), "step ordering")

    for component_id, component in inventory.items():
        step = steps[component_id]
        rule = rules[component_id]
        assert_equal(step["component"], component["name"], f"{component_id} source component")
        assert_equal(step["sourceVersion"], component["version"], f"{component_id} source version")
        for field in ("sequence", "targetComponent", "targetVersion", "action", "gates"):
            assert_equal(step[field], rule[field], f"{component_id} {field}")

    nsx_step = steps[blocking["componentId"]]
    assert_true(
        nsx_step["targetVersion"] != blocking["requestedTargetVersion"],
        "blocked NSX version was modeled as a direct upgrade into VCF 9.0.0.0",
    )


def ps_literal(path: Path) -> str:
    return "'" + path.as_posix().replace("'", "''") + "'"


def invoke_module(
    design_path: Path,
    estate_path: Path,
    snapshot_path: Path,
    output_paths: list[Path],
) -> None:
    invocations = []
    for output_path in output_paths:
        invocations.append(
            "New-VcfArchitecture "
            f"-DesignRequirementsPath {ps_literal(design_path)} "
            f"-EstateInventoryPath {ps_literal(estate_path)} "
            f"-CompatibilitySnapshotPath {ps_literal(snapshot_path)} "
            f"-OutputPath {ps_literal(output_path)}"
        )
    command = (
        "$ErrorActionPreference='Stop'; "
        f"Import-Module {ps_literal(MODULE_PATH)} -Force; "
        + "; ".join(invocations)
    )
    result = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        fail(f"PowerShell module execution failed: {detail}")


def run_module_and_compare(expected_artifact: dict[str, Any]) -> None:
    with tempfile.TemporaryDirectory(prefix="vcfarch-") as temp_dir:
        temp = Path(temp_dir)
        generated_once = temp / "architecture-once.json"
        generated_twice = temp / "architecture-twice.json"
        invoke_module(DESIGN_PATH, ESTATE_PATH, SNAPSHOT_PATH, [generated_once, generated_twice])
        assert_equal(load_json(generated_once), expected_artifact, "module-generated architecture")
        assert_equal(load_json(generated_twice), expected_artifact, "second module-generated architecture")
        assert_equal(
            generated_once.read_bytes(),
            generated_twice.read_bytes(),
            "unchanged inputs must produce byte-identical JSON",
        )

        variant_design = json.loads(json.dumps(load_json(DESIGN_PATH)))
        variant_estate = json.loads(json.dumps(load_json(ESTATE_PATH)))
        variant_snapshot = json.loads(json.dumps(load_json(SNAPSHOT_PATH)))
        variant_design["designId"] = "northstar-chi01-vcf90-variant"
        variant_design["sites"]["primary"] = "CHI02"
        variant_design["capacity"]["perHost"]["physicalCores"] += 2
        variant_design["hosts"][-1] = "esx-m01-06-variant"
        variant_design["appliances"]["vcfOperationsFleetManager"] = (
            "ops-fleet-variant.chi01.northstar.example"
        )
        variant_estate["estateId"] = "northstar-legacy-chi01-variant"
        variant_estate["components"][0]["name"] = "VMware Aria Suite Lifecycle Variant"
        variant_estate["components"][0]["version"] = "8.18.0 Patch 1 Variant"
        variant_snapshot["effectiveBundle"] = "VCF 9.0.1.0 Variant"
        variant_snapshot["migrationRules"][0]["targetVersion"] = "8.18.0 Patch 2 Variant"

        variant_design_path = temp / "design-variant.json"
        variant_estate_path = temp / "estate-variant.json"
        variant_snapshot_path = temp / "snapshot-variant.json"
        variant_design_path.write_text(json.dumps(variant_design), encoding="utf-8")
        variant_estate_path.write_text(json.dumps(variant_estate), encoding="utf-8")
        variant_snapshot_path.write_text(json.dumps(variant_snapshot), encoding="utf-8")
        variant_output = temp / "architecture-variant.json"
        invoke_module(
            variant_design_path,
            variant_estate_path,
            variant_snapshot_path,
            [variant_output],
        )
        variant = load_json(variant_output)
        assert_equal(variant.get("designId"), variant_design["designId"], "variant designId")
        assert_equal(
            variant["greenfieldDesign"]["siteTopology"]["primarySite"],
            variant_design["sites"]["primary"],
            "variant primary site",
        )
        assert_equal(
            variant["greenfieldDesign"]["capacity"]["availablePhysicalCores"],
            (variant_design["availability"]["managementHostCount"] - variant_design["availability"]["reservedHostFailures"])
            * variant_design["capacity"]["perHost"]["physicalCores"],
            "variant capacity",
        )
        variant_spec = variant["greenfieldDesign"]["sddcSpec"]
        assert_equal(variant_spec["hostSpecs"][-1]["hostname"], variant_design["hosts"][-1], "variant host")
        assert_equal(
            variant_spec["vcfOperationsFleetManagementSpec"]["hostname"],
            variant_design["appliances"]["vcfOperationsFleetManager"],
            "variant fleet manager",
        )
        variant_plan = variant["migrationPlan"]
        assert_equal(variant_plan["estateId"], variant_estate["estateId"], "variant estateId")
        assert_equal(
            variant_plan["decision"]["effectiveTargetBundle"],
            variant_snapshot["effectiveBundle"],
            "variant effective bundle",
        )
        assert_equal(variant_plan["steps"][0]["component"], variant_estate["components"][0]["name"], "variant component")
        assert_equal(variant_plan["steps"][0]["sourceVersion"], variant_estate["components"][0]["version"], "variant source version")
        assert_equal(
            variant_plan["steps"][0]["targetVersion"],
            variant_snapshot["migrationRules"][0]["targetVersion"],
            "variant target version",
        )


def main() -> int:
    # Required ordering: validate the submitted SddcSpec against the pinned
    # installer's own schema before performing any scenario-specific checks.
    artifact = load_json(ARTIFACT_PATH)
    openapi = load_json(OPENAPI_PATH)
    try:
        sddc_spec = artifact["greenfieldDesign"]["sddcSpec"]
    except (KeyError, TypeError):
        fail("artifact does not contain greenfieldDesign.sddcSpec")
    sddc_schema = openapi["components"]["schemas"]["SddcSpec"]
    schema_errors = validate_json_schema(sddc_spec, sddc_schema, openapi)
    if schema_errors:
        fail("SddcSpec installer-schema validation failed:\n  " + "\n  ".join(schema_errors))
    print("PASS: SddcSpec validates against the pinned VCF Installer 9.0.0.0 schema")

    actual_hash = hashlib.sha256(OPENAPI_PATH.read_bytes()).hexdigest()
    assert_equal(actual_hash, PINNED_OPENAPI_SHA256, "pinned installer specification SHA-256")
    assert_equal(openapi.get("info", {}).get("version"), "9.0.0.0", "installer specification version")

    design = load_json(DESIGN_PATH)
    estate = load_json(ESTATE_PATH)
    snapshot = load_json(SNAPSHOT_PATH)
    check_research()
    check_greenfield(artifact, design)
    check_migration(artifact, estate, snapshot)
    run_module_and_compare(artifact)
    print("PASS: architecture matches design, estate inventory, and pinned compatibility snapshot")
    print("PASS: VMware.Sdk.Vcf-driven module regenerates the checked-in artifact")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
