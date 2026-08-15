#!/usr/bin/env python3
"""Protected, offline acceptance verifier for the VCF architecture seed."""

from __future__ import annotations

import ast
import importlib
import json
import math
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent
OPENAPI = ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
SDDC_ARTIFACT = ROOT / "architecture" / "sddc-spec.json"
MIGRATION_ARTIFACT = ROOT / "architecture" / "migration-plan.json"
RESEARCH_ARTIFACT = ROOT / "architecture" / "research-sources.json"


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VerificationError(f"missing required artifact: {path.relative_to(ROOT)}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(f"invalid JSON in {path.relative_to(ROOT)}: {exc}") from exc


def resolve_pointer(document: Any, pointer: str) -> Any:
    require(pointer.startswith("#/"), f"unsupported non-local schema reference: {pointer}")
    value = document
    for raw_part in pointer[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        require(isinstance(value, dict) and part in value, f"unresolvable schema reference: {pointer}")
        value = value[part]
    return value


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
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def validate_schema(value: Any, schema: Any, document: Any, path: str = "$") -> list[str]:
    """Validate the JSON Schema/OpenAPI keywords used by the protected contracts."""
    if not isinstance(schema, dict):
        return []
    if "$ref" in schema:
        return validate_schema(value, resolve_pointer(document, schema["$ref"]), document, path)

    errors: list[str] = []
    if value is None and schema.get("nullable"):
        return errors

    for child in schema.get("allOf", []):
        errors.extend(validate_schema(value, child, document, path))
    if "anyOf" in schema:
        branches = [validate_schema(value, child, document, path) for child in schema["anyOf"]]
        if not any(not branch for branch in branches):
            errors.append(f"{path}: does not satisfy anyOf")
    if "oneOf" in schema:
        branches = [validate_schema(value, child, document, path) for child in schema["oneOf"]]
        if sum(not branch for branch in branches) != 1:
            errors.append(f"{path}: does not satisfy exactly one oneOf branch")

    expected_type = schema.get("type")
    if expected_type:
        expected_types = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(json_type_matches(value, item) for item in expected_types):
            errors.append(f"{path}: expected type {' or '.join(expected_types)}, got {type(value).__name__}")
            return errors

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value is not in enum")
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: value does not match const")

    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                errors.append(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        for key, child_value in value.items():
            if key in properties:
                errors.extend(validate_schema(child_value, properties[key], document, f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: additional property {key!r} is not allowed")
            elif isinstance(schema.get("additionalProperties"), dict):
                errors.extend(
                    validate_schema(child_value, schema["additionalProperties"], document, f"{path}.{key}")
                )
        if "minProperties" in schema and len(value) < schema["minProperties"]:
            errors.append(f"{path}: has fewer than minProperties")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            errors.append(f"{path}: has more than maxProperties")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: has fewer than minItems")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: has more than maxItems")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(encoded) != len(set(encoded)):
                errors.append(f"{path}: items are not unique")
        if isinstance(schema.get("items"), dict):
            for index, child_value in enumerate(value):
                errors.extend(validate_schema(child_value, schema["items"], document, f"{path}[{index}]"))

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength")
        if "pattern" in schema:
            try:
                matches = re.search(schema["pattern"], value) is not None
            except re.error as exc:
                raise VerificationError(f"invalid protected schema pattern at {path}: {exc}") from exc
            if not matches:
                errors.append(f"{path}: does not match pattern")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above maximum")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: not above exclusiveMinimum")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            errors.append(f"{path}: not below exclusiveMaximum")
    return errors


def require_schema(value: Any, schema: Any, document: Any, label: str) -> None:
    errors = validate_schema(value, schema, document)
    require(not errors, f"{label} schema validation failed: {'; '.join(errors[:12])}")


def index_by(items: list[dict[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        value = item.get(key)
        require(isinstance(value, str), f"{label} entry has no string {key}")
        require(value not in result, f"duplicate {label} {key}: {value}")
        result[value] = item
    return result


def check_greenfield(sddc: dict[str, Any], requirements: dict[str, Any], snapshot: dict[str, Any]) -> None:
    deployment = requirements["deployment"]
    authority = snapshot["greenfield"]
    require(snapshot["targetRelease"] == requirements["targetRelease"], "fixture target releases disagree")
    for key in ("siteCount", "availabilityZones", "topology"):
        require(deployment[key] == authority[key], f"unsupported {key} for pinned target")
    require(deployment["hostCount"] == authority["minimumHostCount"], "design is not at minimum host count")
    require(deployment["hostFailuresToTolerate"] == authority["storage"]["failuresToTolerate"], "FTT mismatch")

    names = requirements["names"]
    versions = authority["componentVersions"]
    require(sddc.get("sddcId") == names["sddcId"], "wrong SDDC id")
    require(sddc.get("vcfInstanceName") == names["vcfInstanceName"], "wrong VCF instance name")
    require(sddc.get("workflowType") == authority["workflowType"], "workflow must be VCF")
    require(sddc.get("version") == versions["sddcSpec"], "wrong SDDC target version")

    expected_hosts = [item["hostname"] for item in requirements["hosts"]]
    host_specs = sddc.get("hostSpecs", [])
    require([item.get("hostname") for item in host_specs] == expected_hosts, "host specs must name the four supplied hosts")
    require(len(host_specs) == authority["minimumHostCount"], "host count is not the pinned minimum")

    selected = {item["hostname"]: item for item in requirements["hosts"] if item["hostname"] in expected_hosts}
    capacity = requirements["capacity"]
    for failed_hostname in expected_hosts:
        survivors = [host for name, host in selected.items() if name != failed_hostname]
        require(
            sum(host["physicalCores"] for host in survivors) >= capacity["requiredPhysicalCoresAfterFailure"],
            f"CPU capacity fails after loss of {failed_hostname}",
        )
        require(
            sum(host["memoryGiB"] for host in survivors) >= capacity["requiredMemoryGiBAfterFailure"],
            f"memory capacity fails after loss of {failed_hostname}",
        )
    require(
        all(host["nicSpeedGbps"] >= authority["minimumNicSpeedGbps"] for host in selected.values()),
        "a selected host is below the pinned NIC speed",
    )
    usable_storage = sum(host["rawStorageTiB"] for host in selected.values()) * authority["storage"]["rawToUsableFactor"]
    require(usable_storage >= capacity["requiredUsableStorageTiB"], "vSAN usable capacity is insufficient")

    cluster = sddc.get("clusterSpec", {})
    require(cluster.get("datacenterName") == names["datacenter"], "wrong datacenter")
    require(cluster.get("clusterName") == names["cluster"], "wrong cluster")
    pools = cluster.get("resourcePoolSpecs", [])
    require({pool.get("type") for pool in pools} == set(authority["resourcePoolTypes"]), "consolidated resource pools are incomplete")

    datastore = sddc.get("datastoreSpec", {}).get("vsanSpec", {})
    require(datastore.get("datastoreName") == names["vsanDatastore"], "wrong vSAN datastore")
    require(datastore.get("esaConfig", {}).get("enabled") is True, "vSAN ESA must be enabled")
    require(datastore.get("failuresToTolerate") == authority["storage"]["failuresToTolerate"], "wrong vSAN FTT")

    expected_networks = index_by(requirements["networks"], "networkType", "requirement network")
    actual_networks = index_by(sddc.get("networkSpecs", []), "networkType", "SDDC network")
    require(set(actual_networks) == set(authority["requiredNetworkTypes"]), "required network types are incomplete")
    for network_type, expected in expected_networks.items():
        actual = actual_networks[network_type]
        for key in ("subnet", "gateway", "vlanId", "mtu"):
            require(actual.get(key) == expected[key], f"{network_type} has wrong {key}")
        ranges = actual.get("includeIpAddressRanges", [])
        require(
            ranges == [{"startIpAddress": expected["startIpAddress"], "endIpAddress": expected["endIpAddress"]}],
            f"{network_type} has wrong IP range",
        )

    dvs_specs = sddc.get("dvsSpecs", [])
    require(len(dvs_specs) == 1, "consolidated design must use one distributed switch")
    dvs = dvs_specs[0]
    require(dvs.get("dvsName") == names["distributedSwitch"], "wrong distributed switch name")
    require(set(dvs.get("networks", [])) == set(authority["requiredNetworkTypes"]), "distributed switch traffic set is incomplete")
    require(dvs.get("mtu") == 9000, "distributed switch MTU must support jumbo traffic")
    require(
        dvs.get("vmnicsToUplinks") == [{"id": "vmnic0", "uplink": "uplink1"}, {"id": "vmnic1", "uplink": "uplink2"}],
        "distributed switch uplink mapping is wrong",
    )

    nsx = sddc.get("nsxtSpec", {})
    require(nsx.get("useExistingDeployment") is False, "greenfield NSX cannot reuse a deployment")
    require(nsx.get("version") == versions["nsx"], "wrong NSX target version")
    require(nsx.get("vipFqdn") == names["nsxVip"], "wrong NSX VIP")
    require([node.get("hostname") for node in nsx.get("nsxtManagers", [])] == names["nsxManagers"], "wrong NSX manager set")
    require(len(nsx.get("nsxtManagers", [])) == authority["nsxManagerCount"], "wrong NSX manager count")
    overlay = requirements["nsxHostOverlay"]
    pool = nsx.get("ipAddressPoolSpec", {})
    require(pool.get("name") == overlay["name"], "wrong NSX overlay pool")
    require(nsx.get("transportVlanId") == overlay["vlanId"], "wrong NSX transport VLAN")
    require(
        pool.get("subnets") == [{"cidr": overlay["cidr"], "gateway": overlay["gateway"], "ipAddressPoolRanges": [{"start": overlay["start"], "end": overlay["end"]}]}],
        "wrong NSX overlay addressing",
    )

    vcenter = sddc.get("vcenterSpec", {})
    require(vcenter.get("vcenterHostname") == names["vcenter"], "wrong vCenter hostname")
    require(vcenter.get("version") == versions["vcenter"], "wrong vCenter target version")
    require(vcenter.get("useExistingDeployment") is False, "greenfield vCenter cannot reuse a deployment")
    require(vcenter.get("rootVcenterPassword") == requirements["passwordPlaceholders"]["vcenterRoot"], "wrong vCenter secret placeholder")

    passwords = requirements["passwordPlaceholders"]
    manager = sddc.get("sddcManagerSpec", {})
    require(manager.get("hostname") == names["sddcManager"], "wrong SDDC Manager hostname")
    require(manager.get("useExistingDeployment") is False, "greenfield SDDC Manager cannot reuse a deployment")
    require(manager.get("version") == versions["sddcSpec"], "wrong SDDC Manager target version")
    require(manager.get("rootPassword") == passwords["sddcManagerRoot"], "wrong SDDC Manager root secret placeholder")
    require(manager.get("sshPassword") == passwords["sddcManagerSsh"], "wrong SDDC Manager SSH secret placeholder")
    require(manager.get("localUserPassword") == passwords["sddcLocalAdmin"], "wrong SDDC Manager local-admin secret placeholder")

    require(nsx.get("rootNsxtManagerPassword") == passwords["nsxRoot"], "wrong NSX root secret placeholder")
    require(nsx.get("nsxtAdminPassword") == passwords["nsxAdmin"], "wrong NSX admin secret placeholder")
    require(nsx.get("nsxtAuditPassword") == passwords["nsxAudit"], "wrong NSX audit secret placeholder")

    operations = sddc.get("vcfOperationsSpec", {})
    require(operations.get("version") == versions["vcfOperations"], "wrong VCF Operations target version")
    require(operations.get("useExistingDeployment") is False, "greenfield VCF Operations cannot reuse a deployment")
    require(operations.get("loadBalancerFqdn") == names["operationsLoadBalancer"], "wrong VCF Operations load balancer")
    require([node.get("hostname") for node in operations.get("nodes", [])] == names["operationsNodes"], "wrong VCF Operations nodes")
    require(len(operations.get("nodes", [])) == authority["operationsNodeCount"], "wrong VCF Operations node count")
    require(operations.get("adminUserPassword") == passwords["operationsAdmin"], "wrong VCF Operations secret placeholder")

    fleet = sddc.get("vcfOperationsFleetManagementSpec", {})
    require(fleet.get("hostname") == names["fleetManager"], "wrong fleet manager hostname")
    require(fleet.get("version") == versions["fleetManagement"], "wrong fleet manager target version")
    require(fleet.get("useExistingDeployment") is False, "greenfield fleet manager cannot reuse a deployment")
    require(fleet.get("rootUserPassword") == passwords["fleetRoot"], "wrong fleet manager root secret placeholder")
    require(fleet.get("adminUserPassword") == passwords["fleetAdmin"], "wrong fleet manager admin secret placeholder")
    require(sddc.get("dnsSpec") == requirements["dns"], "DNS design differs from requirements")
    require(sddc.get("ntpServers") == requirements["ntpServers"], "NTP design differs from requirements")


def check_migration(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    authority = snapshot["migration"]
    require(plan.get("estateId") == inventory["estateId"] == authority["estateId"], "wrong migration estate")
    require(plan.get("targetRelease") == authority["targetRelease"], "wrong migration target release")
    inventory_by_id = index_by(inventory["components"], "id", "inventory component")
    steps = plan.get("steps", [])
    require([step.get("order") for step in steps] == list(range(1, len(steps) + 1)), "migration order must be contiguous")
    require(len(steps) == len(inventory_by_id), "migration must have exactly one step per inventory component")
    require({step.get("componentId") for step in steps} == set(inventory_by_id), "migration omits or invents components")
    expected_steps = authority["steps"]
    require([step.get("componentId") for step in steps] == [step["componentId"] for step in expected_steps], "migration violates pinned upgrade order")
    for actual, expected in zip(steps, expected_steps):
        current = inventory_by_id[expected["componentId"]]
        require(actual.get("component") == current["component"], f"wrong component name for {current['id']}")
        require(actual.get("fromVersion") == current["version"], f"wrong source version for {current['id']}")
        require(
            actual.get("target") == {"component": expected["targetComponent"], "version": expected["targetVersion"]},
            f"wrong target for {current['id']}",
        )
        require(actual.get("gates") == expected["gates"], f"wrong gates for {current['id']}")


def check_stdlib_only() -> None:
    package = ROOT / "vcf_architecture"
    require((package / "__init__.py").is_file(), "vcf_architecture package is missing")
    stdlib = set(sys.stdlib_module_names)
    local_names = {"vcf_architecture"}
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported: list[str] = []
            if isinstance(node, ast.Import):
                imported = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported = [node.module.split(".")[0]]
            for name in imported:
                require(name in stdlib or name in local_names, f"non-stdlib import {name!r} in {path.relative_to(ROOT)}")


def check_research_sources(research: Any) -> None:
    require(isinstance(research, dict), "research-sources.json must contain an object")
    sources = research.get("sources")
    require(isinstance(sources, list) and len(sources) >= 3, "research provenance must list at least three sources")

    urls: set[str] = set()
    searchable: list[str] = []
    for index, source in enumerate(sources, start=1):
        require(isinstance(source, dict), f"research source {index} must be an object")
        for key in ("title", "url", "factUsed"):
            require(
                isinstance(source.get(key), str) and source[key].strip(),
                f"research source {index} has no non-empty {key}",
            )
        require(len(source["title"].strip()) >= 8, f"research source {index} has no meaningful title")
        require(len(source["factUsed"].strip()) >= 20, f"research source {index} has no substantive fact used")

        accessed = source.get("accessedAt", source.get("accessDate"))
        require(isinstance(accessed, str), f"research source {index} has no access date")
        try:
            date.fromisoformat(accessed)
        except ValueError as exc:
            raise VerificationError(f"research source {index} access date is not ISO YYYY-MM-DD") from exc

        parsed = urlsplit(source["url"])
        hostname = (parsed.hostname or "").lower()
        require(
            parsed.scheme == "https" and parsed.path and (hostname == "broadcom.com" or hostname.endswith(".broadcom.com")),
            f"research source {index} is not a reachable-form Broadcom HTTPS page",
        )
        require(not parsed.username and not parsed.password and not parsed.fragment, f"research source {index} has an unsafe URL")
        normalized_url = source["url"].rstrip("/")
        require(normalized_url not in urls, f"duplicate research source URL: {source['url']}")
        urls.add(normalized_url)
        searchable.append(" ".join((source["title"], source["url"], source["factUsed"])).lower())

    combined = " ".join(searchable)
    require("compatib" in combined, "research provenance has no compatibility source")
    require("interoperab" in combined or "interopmatrix" in combined, "research provenance has no interoperability source")
    require("upgrade" in combined and ("vcf 9" in combined or "9.0" in combined), "research provenance has no VCF 9.0 upgrade-path source")


def check_package_reproduction(
    sddc: dict[str, Any],
    plan: dict[str, Any],
    requirements: dict[str, Any],
    inventory: dict[str, Any],
    snapshot: dict[str, Any],
) -> None:
    sys.path.insert(0, str(ROOT))
    try:
        package = importlib.import_module("vcf_architecture")
        require(callable(getattr(package, "build_sddc_spec", None)), "package root does not export build_sddc_spec")
        require(callable(getattr(package, "build_migration_plan", None)), "package root does not export build_migration_plan")
        require(package.build_sddc_spec(requirements, snapshot) == sddc, "build_sddc_spec does not reproduce the artifact")
        require(package.build_migration_plan(inventory, snapshot) == plan, "build_migration_plan does not reproduce the artifact")
    finally:
        sys.path.pop(0)


def main() -> int:
    # The first acceptance check is deliberately the requested OpenAPI schema validation.
    openapi = load_json(OPENAPI)
    sddc = load_json(SDDC_ARTIFACT)
    require_schema(sddc, {"$ref": "#/components/schemas/SddcSpec"}, openapi, "SddcSpec")

    requirements = load_json(ROOT / "fixtures" / "site-requirements.json")
    inventory = load_json(ROOT / "fixtures" / "estate-inventory.json")
    snapshot = load_json(ROOT / "fixtures" / "compatibility-snapshot.json")
    migration_schema = load_json(ROOT / "contracts" / "migration-plan.schema.json")
    plan = load_json(MIGRATION_ARTIFACT)
    research = load_json(RESEARCH_ARTIFACT)
    require_schema(plan, migration_schema, migration_schema, "migration plan")
    check_greenfield(sddc, requirements, snapshot)
    check_migration(plan, inventory, snapshot)
    check_stdlib_only()
    check_research_sources(research)
    check_package_reproduction(sddc, plan, requirements, inventory, snapshot)
    print("PASS: VCF 9.0 architecture and ordered migration plan satisfy the protected contracts")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
