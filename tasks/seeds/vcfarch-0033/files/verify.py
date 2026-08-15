#!/usr/bin/env python3
"""Deterministic offline verifier for the VCF architecture artifact."""

from __future__ import annotations

import hashlib
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


ROOT = Path(__file__).resolve().parent
SPEC_PATH = ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
INVENTORY_PATH = ROOT / "fixtures" / "estate-inventory.json"
SNAPSHOT_PATH = ROOT / "fixtures" / "compatibility-snapshot.json"
MIGRATION_SCHEMA_PATH = ROOT / "fixtures" / "migration-plan-schema.json"

# Filled with the immutable fixture digests after authoring.
PROTECTED_DIGESTS = {
    "TestMain.java": "cefe0d280b36c600882b86232f0382e153e105dae020c8f0036f58d223fa271f",
    "specifications/vcf-installer/PROVENANCE.md": "e4efb52d18766e6b4058f2def8b5d13dc6a05c1d120aa73abb3f58fb312097f5",
    "specifications/vcf-installer/vcf-installer-openapi.json": "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d",
    "fixtures/estate-inventory.json": "f52979469237f324926767512722c2d995534132b87194e4c7f08abf148dac48",
    "fixtures/compatibility-snapshot.json": "9d329930559b80e1bd8a36c51b768a6bcf338105598ea1b08945179ce1769881",
    "fixtures/migration-plan-schema.json": "b1ddda5026b1fa74051729330cd003c8f34f1cb702c778ffaf38dc32e07d5ee9",
}


class VerificationError(Exception):
    pass


def fail(message: str) -> None:
    raise VerificationError(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot load {path.relative_to(ROOT)}: {exc}")


def resolve_pointer(document: Any, pointer: str) -> Any:
    if not pointer.startswith("#/"):
        fail(f"only local schema references are supported: {pointer}")
    current = document
    for raw_part in pointer[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        try:
            current = current[part]
        except (KeyError, TypeError):
            fail(f"unresolvable schema reference: {pointer}")
    return current


def json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) == float(right)
    return left == right


def type_matches(instance: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(instance, dict)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "string":
        return isinstance(instance, str)
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "null":
        return instance is None
    return True


def validate_schema(instance: Any, schema: Any, root_schema: Any, path: str = "$") -> None:
    """Validate the JSON Schema/OpenAPI keywords used by the vendored contract."""
    if schema is True:
        return
    if schema is False:
        fail(f"{path}: value is forbidden by schema")
    if not isinstance(schema, dict):
        fail(f"{path}: malformed schema node")

    if "$ref" in schema:
        validate_schema(instance, resolve_pointer(root_schema, schema["$ref"]), root_schema, path)
        siblings = {key: value for key, value in schema.items() if key != "$ref"}
        if siblings:
            validate_schema(instance, siblings, root_schema, path)
        return

    if instance is None and schema.get("nullable") is True:
        return

    if "allOf" in schema:
        for index, branch in enumerate(schema["allOf"]):
            validate_schema(instance, branch, root_schema, f"{path}.allOf[{index}]")

    if "anyOf" in schema:
        matches = 0
        for branch in schema["anyOf"]:
            try:
                validate_schema(instance, branch, root_schema, path)
                matches += 1
            except VerificationError:
                pass
        if matches == 0:
            fail(f"{path}: does not match anyOf")

    if "oneOf" in schema:
        matches = 0
        for branch in schema["oneOf"]:
            try:
                validate_schema(instance, branch, root_schema, path)
                matches += 1
            except VerificationError:
                pass
        if matches != 1:
            fail(f"{path}: must match exactly one oneOf branch, matched {matches}")

    if "not" in schema:
        try:
            validate_schema(instance, schema["not"], root_schema, path)
        except VerificationError:
            pass
        else:
            fail(f"{path}: matches forbidden schema")

    if "const" in schema and not json_equal(instance, schema["const"]):
        fail(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and not any(json_equal(instance, item) for item in schema["enum"]):
        fail(f"{path}: {instance!r} is not in the allowed enum")

    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        if not any(type_matches(instance, item) for item in expected_type):
            fail(f"{path}: expected one of types {expected_type}")
    elif isinstance(expected_type, str) and not type_matches(instance, expected_type):
        fail(f"{path}: expected type {expected_type}, got {type(instance).__name__}")

    if isinstance(instance, dict):
        for required in schema.get("required", []):
            if required not in instance:
                fail(f"{path}: missing required property {required!r}")
        properties = schema.get("properties", {})
        for key, value in instance.items():
            if key in properties:
                validate_schema(value, properties[key], root_schema, f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                fail(f"{path}: unexpected property {key!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_schema(value, schema["additionalProperties"], root_schema, f"{path}.{key}")
        property_count = len(instance)
        if "minProperties" in schema and property_count < schema["minProperties"]:
            fail(f"{path}: too few properties")
        if "maxProperties" in schema and property_count > schema["maxProperties"]:
            fail(f"{path}: too many properties")

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            fail(f"{path}: too few array items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            fail(f"{path}: too many array items")
        if schema.get("uniqueItems"):
            canonical = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(canonical) != len(set(canonical)):
                fail(f"{path}: array items must be unique")
        items_schema = schema.get("items")
        if isinstance(items_schema, dict):
            for index, item in enumerate(instance):
                validate_schema(item, items_schema, root_schema, f"{path}[{index}]")
        elif isinstance(items_schema, list):
            for index, item_schema in enumerate(items_schema[: len(instance)]):
                validate_schema(instance[index], item_schema, root_schema, f"{path}[{index}]")

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            fail(f"{path}: string is shorter than minLength")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            fail(f"{path}: string is longer than maxLength")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], instance) is not None
            except re.error as exc:
                fail(f"{path}: invalid schema pattern: {exc}")
            if not matched:
                fail(f"{path}: string does not match pattern {schema['pattern']!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            fail(f"{path}: number is below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            fail(f"{path}: number is above maximum")
        exclusive_minimum = schema.get("exclusiveMinimum")
        if isinstance(exclusive_minimum, (int, float)) and not isinstance(exclusive_minimum, bool):
            if instance <= exclusive_minimum:
                fail(f"{path}: number is not above exclusiveMinimum")
        elif exclusive_minimum is True and "minimum" in schema and instance <= schema["minimum"]:
            fail(f"{path}: number is not above exclusive minimum")
        exclusive_maximum = schema.get("exclusiveMaximum")
        if isinstance(exclusive_maximum, (int, float)) and not isinstance(exclusive_maximum, bool):
            if instance >= exclusive_maximum:
                fail(f"{path}: number is not below exclusiveMaximum")
        elif exclusive_maximum is True and "maximum" in schema and instance >= schema["maximum"]:
            fail(f"{path}: number is not below exclusive maximum")


def require_equal(actual: Any, expected: Any, label: str) -> None:
    if not json_equal(actual, expected):
        fail(f"{label}: expected {expected!r}, got {actual!r}")


def require_close(actual: Any, expected: float, label: str) -> None:
    if not isinstance(actual, (int, float)) or isinstance(actual, bool):
        fail(f"{label}: expected a number")
    if not math.isclose(float(actual), expected, rel_tol=1e-9, abs_tol=1e-9):
        fail(f"{label}: expected {expected}, got {actual}")


def check_protected_files() -> None:
    for relative, expected in PROTECTED_DIGESTS.items():
        data = (ROOT / relative).read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            fail(f"protected file changed: {relative}")


def compile_and_run() -> Any:
    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as build_dir:
        compile_result = subprocess.run(
            ["javac", "-encoding", "UTF-8", "-d", build_dir, "ArchitectureClient.java", "TestMain.java"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        if compile_result.returncode != 0:
            fail(f"Java compilation failed:\n{compile_result.stderr.strip()}")
        run_result = subprocess.run(
            ["java", "-cp", build_dir, "TestMain"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        if run_result.returncode != 0:
            fail(f"TestMain failed:\n{run_result.stderr.strip()}")
        try:
            return json.loads(run_result.stdout)
        except json.JSONDecodeError as exc:
            fail(f"TestMain output is not one JSON artifact: {exc}")


def check_sddc_content(artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    sddc = artifact["sddcSpec"]
    requirements = inventory["requirements"]
    greenfield = snapshot["greenfield"]

    require_equal(artifact.get("artifactVersion"), "1.0", "artifactVersion")
    require_equal(artifact.get("compatibilitySnapshotId"), snapshot["snapshotId"], "compatibilitySnapshotId")
    require_equal(sddc.get("workflowType"), requirements["workflowType"], "sddcSpec.workflowType")
    require_equal(sddc.get("version"), requirements["targetVcfVersion"], "sddcSpec.version")
    require_equal(sddc.get("sddcId"), "dfw02-wld01", "sddcSpec.sddcId")
    require_equal(sddc.get("skipEsxThumbprintValidation"), False, "sddcSpec.skipEsxThumbprintValidation")
    require_equal(sddc.get("skipGatewayPingValidation"), False, "sddcSpec.skipGatewayPingValidation")

    expected_hosts = [host["hostname"] for host in requirements["hosts"]]
    actual_hosts = [host.get("hostname") for host in sddc.get("hostSpecs", [])]
    require_equal(actual_hosts, expected_hosts, "sddcSpec.hostSpecs host order")
    require_equal(len(actual_hosts), greenfield["minimumHostsForThisDesign"], "sddcSpec.hostSpecs count")

    require_equal(sddc.get("dnsSpec"), requirements["dns"], "sddcSpec.dnsSpec")
    require_equal(sddc.get("ntpServers"), requirements["ntpServers"], "sddcSpec.ntpServers")

    actual_networks = sddc.get("networkSpecs")
    if not isinstance(actual_networks, list):
        fail("sddcSpec.networkSpecs must be an array")
    by_type = {network.get("networkType"): network for network in actual_networks}
    if len(by_type) != len(actual_networks):
        fail("sddcSpec.networkSpecs has duplicate networkType values")
    for expected in requirements["networks"]:
        actual = by_type.get(expected["networkType"])
        if actual is None:
            fail(f"sddcSpec.networkSpecs missing {expected['networkType']}")
        require_equal(actual.get("vlanId"), expected["vlanId"], f"{expected['networkType']} vlanId")
        require_equal(actual.get("subnet"), expected["cidr"], f"{expected['networkType']} subnet")
        require_equal(actual.get("gateway"), expected["gateway"], f"{expected['networkType']} gateway")
        require_equal(actual.get("mtu"), expected["mtu"], f"{expected['networkType']} mtu")
        require_equal(
            actual.get("includeIpAddressRanges"),
            [{"startIpAddress": expected["startIp"], "endIpAddress": expected["endIp"]}],
            f"{expected['networkType']} IP range",
        )
    require_equal(set(by_type), {network["networkType"] for network in requirements["networks"]}, "network types")

    require_equal(sddc.get("vcenterSpec", {}).get("version"), greenfield["vcenterVersion"], "vCenter target")
    require_equal(sddc.get("vcenterSpec", {}).get("useExistingDeployment"), False, "greenfield vCenter")
    require_equal(sddc.get("nsxtSpec", {}).get("version"), greenfield["nsxVersion"], "NSX target")
    require_equal(sddc.get("nsxtSpec", {}).get("useExistingDeployment"), False, "greenfield NSX")
    require_equal(sddc.get("datastoreSpec", {}).get("vsanSpec", {}).get("esaConfig", {}).get("enabled"), True, "vSAN ESA")
    require_equal(sddc.get("datastoreSpec", {}).get("vsanSpec", {}).get("failuresToTolerate"), 1, "vSAN FTT")
    require_equal(sddc.get("vcfOperationsSpec", {}).get("version"), greenfield["vcfOperationsVersion"], "VCF Operations target")
    require_equal(sddc.get("vcfOperationsSpec", {}).get("useExistingDeployment"), True, "reuse VCF Operations")
    require_equal(sddc.get("licenseServerSpec", {}).get("version"), greenfield["licenseServerVersion"], "License Server target")
    require_equal(sddc.get("licenseServerSpec", {}).get("useExistingDeployment"), True, "reuse License Server")
    require_equal(
        sddc.get("vcfOperationsSpec", {}).get("nodes", [{}])[0].get("hostname"),
        inventory["fleet"]["existingFleetServices"]["vcfOperationsFqdn"],
        "existing VCF Operations FQDN",
    )
    require_equal(
        sddc.get("licenseServerSpec", {}).get("hostname"),
        inventory["fleet"]["existingFleetServices"]["licenseServerFqdn"],
        "existing License Server FQDN",
    )

    dvs_specs = sddc.get("dvsSpecs")
    if not isinstance(dvs_specs, list) or not dvs_specs:
        fail("sddcSpec.dvsSpecs must define redundant uplinks")
    for index, dvs in enumerate(dvs_specs):
        mappings = dvs.get("vmnicsToUplinks", [])
        ids = {mapping.get("id") for mapping in mappings}
        uplinks = {mapping.get("uplink") for mapping in mappings}
        if not {"vmnic0", "vmnic1"}.issubset(ids) or not {"uplink1", "uplink2"}.issubset(uplinks):
            fail(f"sddcSpec.dvsSpecs[{index}] lacks dual independent uplinks")


def check_capacity_and_placement(artifact: dict[str, Any], inventory: dict[str, Any]) -> None:
    requirements = inventory["requirements"]
    host_count = len(requirements["hosts"])
    survivors = host_count - requirements["hostFailuresToTolerate"]
    shape = requirements["hostShape"]
    headroom_factor = 1.0 - requirements["capacityHeadroomPercent"] / 100.0
    raid_efficiency = requirements["raid1Efficiency"]

    expected = {
        "installed": {
            "physicalCores": host_count * shape["physicalCores"],
            "memoryGiB": host_count * shape["memoryGiB"],
            "rawStorageTb": host_count * shape["rawNvmeTb"],
        },
        "afterOneHostFailure": {
            "physicalCores": survivors * shape["physicalCores"],
            "memoryGiB": survivors * shape["memoryGiB"],
            "rawStorageTb": survivors * shape["rawNvmeTb"],
        },
        "afterStoragePolicy": {
            "usableStorageTb": survivors * shape["rawNvmeTb"] * raid_efficiency,
        },
        "afterHeadroom": {
            "physicalCores": survivors * shape["physicalCores"] * headroom_factor,
            "memoryGiB": survivors * shape["memoryGiB"] * headroom_factor,
            "usableStorageTb": survivors * shape["rawNvmeTb"] * raid_efficiency * headroom_factor,
        },
    }
    capacity = artifact.get("capacityPlan")
    if not isinstance(capacity, dict):
        fail("capacityPlan must be an object")
    require_equal(capacity.get("hostCount"), host_count, "capacityPlan.hostCount")
    require_equal(capacity.get("hostShape"), shape, "capacityPlan.hostShape")
    for section, values in expected.items():
        actual_section = capacity.get(section, {})
        for key, value in values.items():
            require_close(actual_section.get(key), float(value), f"capacityPlan.{section}.{key}")
    require_equal(capacity.get("demand"), requirements["workloadDemand"], "capacityPlan.demand")
    require_equal(capacity.get("headroomPercent"), requirements["capacityHeadroomPercent"], "capacityPlan.headroomPercent")
    require_equal(capacity.get("meetsDemand"), True, "capacityPlan.meetsDemand")
    adjusted = expected["afterHeadroom"]
    demand = requirements["workloadDemand"]
    if not (
        adjusted["physicalCores"] >= demand["physicalCoreEquivalent"]
        and adjusted["memoryGiB"] >= demand["memoryGiB"]
        and adjusted["usableStorageTb"] >= demand["usableStorageTb"]
    ):
        fail("fixture itself does not satisfy the capacity target")

    placement = artifact.get("sitePlacement", {})
    require_equal(placement.get("fleetManagementSite"), inventory["fleet"]["primarySite"], "fleet management site")
    require_equal(placement.get("workloadSite"), inventory["fleet"]["newWorkloadSite"], "workload site")
    require_equal(placement.get("managementDomainChange"), requirements["managementDomainChange"], "management isolation")
    require_equal(placement.get("isolationBoundary"), "SEPARATE_VCF_INSTANCE", "isolation boundary")
    require_equal(placement.get("fleetServicesMode"), "REUSE_EXISTING", "fleet services mode")

    availability = artifact.get("availabilityPlan", {})
    require_equal(availability.get("hostFailuresToTolerate"), requirements["hostFailuresToTolerate"], "availability FTT")
    require_equal(availability.get("storagePolicy"), requirements["storagePolicy"], "availability storage policy")
    require_equal(availability.get("networkRedundancy"), {"topOfRackSwitches": 2, "uplinksPerHost": 2}, "network redundancy")
    require_equal(availability.get("meetsRequirement"), True, "availability meetsRequirement")
    actual_domains = {
        item.get("name"): item.get("hosts")
        for item in availability.get("faultDomains", [])
        if isinstance(item, dict)
    }
    expected_domains: dict[str, list[str]] = {}
    for host in requirements["hosts"]:
        expected_domains.setdefault(host["faultDomain"], []).append(host["hostname"])
    require_equal(actual_domains, expected_domains, "rack fault domains")


def check_migration(artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    plan = artifact["migrationPlan"]
    require_equal(plan.get("schemaVersion"), "1.0", "migrationPlan.schemaVersion")
    require_equal(plan.get("sourceSite"), inventory["fleet"]["newWorkloadSite"], "migrationPlan.sourceSite")
    require_equal(plan.get("targetRelease"), snapshot["targetRelease"], "migrationPlan.targetRelease")

    inventory_by_id = {component["id"]: component for component in inventory["components"]}
    rules = snapshot["migrationRules"]
    steps = plan.get("steps", [])
    require_equal(len(steps), len(inventory_by_id), "migration step count")
    require_equal(len(rules), len(inventory_by_id), "pinned rule count")
    if [step.get("sequence") for step in steps] != sorted(step.get("sequence") for step in steps):
        fail("migrationPlan.steps are not strictly ordered")
    if len({step.get("sequence") for step in steps}) != len(steps):
        fail("migrationPlan.steps sequence values are not unique")
    if len({step.get("componentId") for step in steps}) != len(steps):
        fail("migrationPlan contains duplicate components")

    for step, rule in zip(steps, rules):
        component = inventory_by_id.get(rule["componentId"])
        if component is None:
            fail(f"snapshot rule references missing inventory component {rule['componentId']}")
        require_equal(step.get("sequence"), rule["sequence"], f"{rule['componentId']} sequence")
        require_equal(step.get("componentId"), component["id"], f"{rule['componentId']} id")
        require_equal(step.get("componentName"), component["name"], f"{rule['componentId']} name")
        require_equal(step.get("currentVersion"), component["version"], f"{rule['componentId']} current version")
        require_equal(step.get("targetVersion"), rule["targetVersion"], f"{rule['componentId']} target version")
        require_equal(step.get("action"), rule["action"], f"{rule['componentId']} action")
        require_equal(step.get("gates"), rule["requiredGates"], f"{rule['componentId']} gates")
        if component["protected"]:
            require_equal(step.get("action"), "RETAIN", f"{rule['componentId']} protected action")
            require_equal(step.get("targetVersion"), component["version"], f"{rule['componentId']} protected version")


def check_research(artifact: dict[str, Any]) -> None:
    sources = artifact.get("researchConsulted")
    if not isinstance(sources, list) or not sources:
        fail("researchConsulted must contain at least one Broadcom source")

    locators: list[str] = []
    evidence: list[str] = []
    for index, source in enumerate(sources):
        path = f"researchConsulted[{index}]"
        if not isinstance(source, dict):
            fail(f"{path} must be an object")

        title = source.get("sourceTitle")
        locator = source.get("sourceLocator")
        consulted_at = source.get("consultedAt")
        finding = source.get("finding")
        if not isinstance(title, str) or len(title.strip()) < 8:
            fail(f"{path}.sourceTitle must be a substantive title")
        if not isinstance(locator, str):
            fail(f"{path}.sourceLocator must be an HTTPS Broadcom/VMware URL")
        parsed = urlsplit(locator)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if (
            parsed.scheme.lower() != "https"
            or not hostname
            or not (
                hostname == "broadcom.com"
                or hostname.endswith(".broadcom.com")
                or hostname == "vmware.com"
                or hostname.endswith(".vmware.com")
            )
            or parsed.username is not None
            or parsed.password is not None
        ):
            fail(f"{path}.sourceLocator must be an HTTPS Broadcom/VMware URL")
        if not isinstance(consulted_at, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", consulted_at) is None:
            fail(f"{path}.consultedAt must be an ISO calendar date")
        try:
            date.fromisoformat(consulted_at)
        except ValueError:
            fail(f"{path}.consultedAt must be an ISO calendar date")
        if not isinstance(finding, str) or len(finding.strip()) < 20:
            fail(f"{path}.finding must summarize what the source established")

        locators.append(locator)
        evidence.extend((title.lower(), finding.lower()))

    if len(locators) != len(set(locators)):
        fail("researchConsulted sourceLocator values must be unique")
    combined = " ".join(evidence)
    compatibility_terms = (
        "compatib",
        "interoperab",
        "matrix",
        "supported",
        "bill of materials",
        "build number",
        "version",
    )
    upgrade_terms = (
        "upgrade",
        "update path",
        "migration",
        "lifecycle",
        "sequence",
        "converge",
        "import",
        "back-in-time",
    )
    if not any(term in combined for term in compatibility_terms):
        fail("researchConsulted does not cover compatibility or interoperability material")
    if not any(term in combined for term in upgrade_terms):
        fail("researchConsulted does not cover upgrade-path material")


def verify() -> None:
    artifact = compile_and_run()
    spec_document = load_json(SPEC_PATH)

    # This is deliberately the first artifact check. Compatibility, inventory,
    # migration-schema, and protected-file checks happen only after SddcSpec has
    # been validated with the installer specification's own schema.
    if not isinstance(artifact, dict) or "sddcSpec" not in artifact:
        fail("installer SddcSpec schema validation failed: artifact has no sddcSpec")
    validate_schema(
        artifact["sddcSpec"],
        spec_document["components"]["schemas"]["SddcSpec"],
        spec_document,
        "$.sddcSpec",
    )

    check_protected_files()
    inventory = load_json(INVENTORY_PATH)
    snapshot = load_json(SNAPSHOT_PATH)
    migration_schema = load_json(MIGRATION_SCHEMA_PATH)
    if "migrationPlan" not in artifact:
        fail("artifact has no migrationPlan")
    validate_schema(artifact["migrationPlan"], migration_schema, migration_schema, "$.migrationPlan")

    required_sections = {
        "artifactVersion",
        "compatibilitySnapshotId",
        "sddcSpec",
        "sitePlacement",
        "capacityPlan",
        "availabilityPlan",
        "migrationPlan",
        "researchConsulted",
    }
    missing = sorted(required_sections - artifact.keys())
    if missing:
        fail(f"artifact missing required sections: {', '.join(missing)}")

    check_sddc_content(artifact, inventory, snapshot)
    check_capacity_and_placement(artifact, inventory)
    check_migration(artifact, inventory, snapshot)
    check_research(artifact)


def main() -> int:
    try:
        verify()
    except (VerificationError, OSError, subprocess.SubprocessError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: VCF 9.1 architecture artifact is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
