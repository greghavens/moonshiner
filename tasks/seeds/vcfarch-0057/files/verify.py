#!/usr/bin/env python3
"""Deterministic verifier for the VCF architecture artifact."""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent


class VerificationError(Exception):
    pass


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError(f"cannot load {path.relative_to(ROOT)}: {exc}") from exc


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
    raise VerificationError(f"unsupported JSON Schema type {expected!r}")


def resolve_ref(document: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise VerificationError(f"external schema reference is not supported: {ref}")
    node: Any = document
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            raise VerificationError(f"unresolvable schema reference: {ref}")
        node = node[part]
    if not isinstance(node, dict):
        raise VerificationError(f"schema reference does not resolve to an object: {ref}")
    return node


def validate_schema(
    value: Any,
    schema: dict[str, Any],
    document: dict[str, Any],
    path: str = "$",
) -> None:
    if "$ref" in schema:
        validate_schema(value, resolve_ref(document, schema["$ref"]), document, path)
        return
    if value is None and schema.get("nullable") is True:
        return
    if "const" in schema and value != schema["const"]:
        raise VerificationError(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise VerificationError(f"{path}: {value!r} is not an allowed value")
    for child in schema.get("allOf", []):
        validate_schema(value, child, document, path)
    if "anyOf" in schema:
        if not any(schema_matches(value, child, document, path) for child in schema["anyOf"]):
            raise VerificationError(f"{path}: no anyOf branch matched")
    if "oneOf" in schema:
        matches = sum(schema_matches(value, child, document, path) for child in schema["oneOf"])
        if matches != 1:
            raise VerificationError(f"{path}: expected exactly one oneOf branch, got {matches}")
    if "not" in schema and schema_matches(value, schema["not"], document, path):
        raise VerificationError(f"{path}: value matched a forbidden schema")

    expected_type = schema.get("type")
    if expected_type is not None:
        choices = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(type_matches(value, choice) for choice in choices):
            raise VerificationError(f"{path}: expected {expected_type}, got {type(value).__name__}")

    if isinstance(value, dict):
        missing = [key for key in schema.get("required", []) if key not in value]
        if missing:
            raise VerificationError(f"{path}: missing required properties {missing}")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        extras = sorted(set(value) - set(properties))
        if additional is False and extras:
            raise VerificationError(f"{path}: unexpected properties {extras}")
        for key, child in value.items():
            if key in properties:
                validate_schema(child, properties[key], document, f"{path}.{key}")
            elif isinstance(additional, dict):
                validate_schema(child, additional, document, f"{path}.{key}")

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise VerificationError(f"{path}: too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise VerificationError(f"{path}: too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(encoded) != len(set(encoded)):
                raise VerificationError(f"{path}: items are not unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, child in enumerate(value):
                validate_schema(child, item_schema, document, f"{path}[{index}]")

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise VerificationError(f"{path}: string is too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise VerificationError(f"{path}: string is too long")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise VerificationError(f"{path}: string does not match {schema['pattern']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise VerificationError(f"{path}: value is below the minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise VerificationError(f"{path}: value is above the maximum")


def schema_matches(value: Any, schema: dict[str, Any], document: dict[str, Any], path: str) -> bool:
    try:
        validate_schema(value, schema, document, path)
        return True
    except VerificationError:
        return False


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def validate_research_record(record: Any) -> None:
    require(isinstance(record, dict), "research/sources.json must contain an object")
    require(set(record) == {"sources"}, "research record must contain only sources")
    sources = record.get("sources")
    require(isinstance(sources, list) and sources, "research sources must be a non-empty array")
    required_fields = {"title", "publisher", "url", "accessedAt", "decisions"}
    for index, source in enumerate(sources):
        label = f"research source {index + 1}"
        require(isinstance(source, dict), f"{label} must be an object")
        require(set(source) == required_fields, f"{label} fields mismatch")
        for field in ("title", "publisher"):
            require(
                isinstance(source.get(field), str) and source[field].strip() != "",
                f"{label} {field} is required",
            )
        url = source.get("url")
        require(isinstance(url, str), f"{label} URL is required")
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        require(
            parsed.scheme == "https"
            and (hostname == "broadcom.com" or hostname.endswith(".broadcom.com"))
            and bool(parsed.path),
            f"{label} must use an official Broadcom HTTPS URL",
        )
        accessed_at = source.get("accessedAt")
        require(isinstance(accessed_at, str), f"{label} accessedAt date is required")
        try:
            parsed_date = date.fromisoformat(accessed_at)
        except ValueError as exc:
            raise VerificationError(f"{label} accessedAt must use YYYY-MM-DD format") from exc
        require(parsed_date.isoformat() == accessed_at, f"{label} accessedAt must use YYYY-MM-DD format")
        decisions = source.get("decisions")
        require(
            isinstance(decisions, list)
            and decisions
            and all(isinstance(decision, str) and decision.strip() != "" for decision in decisions),
            f"{label} decisions must be a non-empty array of non-empty strings",
        )


def index_by(items: list[dict[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        value = item.get(key)
        require(isinstance(value, str), f"{label}: missing string {key}")
        require(value not in result, f"{label}: duplicate {key} {value}")
        result[value] = item
    return result


def ceil_headroom(value: int, percent: int) -> int:
    return math.ceil(value * (100 + percent) / 100)


def semantic_checks(
    artifact: dict[str, Any],
    scenario: dict[str, Any],
    estate: dict[str, Any],
    snapshot: dict[str, Any],
) -> None:
    require(set(artifact) == {"schemaVersion", "greenfield", "existingEstate"}, "artifact top-level shape mismatch")
    require(artifact["schemaVersion"] == 1, "artifact schemaVersion must be 1")

    greenfield = artifact["greenfield"]
    require(
        set(greenfield) == {"sddcSpec", "topology", "capacity", "billOfMaterials"},
        "greenfield shape mismatch",
    )
    sddc = greenfield["sddcSpec"]
    require(sddc.get("sddcId") == scenario["sddcId"], "SddcSpec sddcId mismatch")
    require(sddc.get("workflowType") == "VCF", "SddcSpec workflowType must be VCF")
    require(sddc.get("version") == scenario["targetRelease"] == snapshot["targetRelease"], "SddcSpec version mismatch")
    require(sddc.get("vcfInstanceName") == scenario["vcfInstanceName"], "VCF instance name mismatch")
    require(sddc.get("managementPoolName") == scenario["managementPoolName"], "management pool mismatch")
    require(sddc.get("skipEsxThumbprintValidation") is False, "ESXi thumbprint validation must not be skipped")
    require(sddc.get("skipGatewayPingValidation") is False, "gateway validation must not be skipped")
    require(sddc.get("ceipEnabled") is False, "CEIP choice mismatch")

    infra = scenario["infrastructure"]
    vcenter = sddc["vcenterSpec"]
    require(vcenter.get("vcenterHostname") == infra["vcenterHostname"], "vCenter hostname mismatch")
    require(vcenter.get("useExistingDeployment") is False, "greenfield vCenter cannot be marked existing")
    manager = sddc["sddcManagerSpec"]
    require(manager.get("hostname") == infra["sddcManagerHostname"], "SDDC Manager hostname mismatch")
    require(manager.get("useExistingDeployment") is False, "greenfield SDDC Manager cannot be marked existing")
    nsx = sddc["nsxtSpec"]
    require(nsx.get("vipFqdn") == infra["nsxVipHostname"], "NSX VIP mismatch")
    require(nsx.get("useExistingDeployment") is False, "greenfield NSX cannot be marked existing")
    require(
        [entry.get("hostname") for entry in nsx.get("nsxtManagers", [])] == infra["nsxManagerHostnames"],
        "NSX manager inventory mismatch",
    )
    require(sddc["dnsSpec"].get("subdomain") == scenario["domainName"], "DNS subdomain mismatch")
    require(sddc["dnsSpec"].get("nameservers") == infra["dnsServers"], "DNS servers mismatch")
    require(sddc.get("ntpServers") == infra["ntpServers"], "NTP servers mismatch")

    expected_hosts = {host["hostname"] for host in scenario["hosts"]}
    actual_hosts = {host.get("hostname") for host in sddc.get("hostSpecs", [])}
    require(actual_hosts == expected_hosts, "SddcSpec must contain every and only inventoried data host")
    require(scenario["witness"]["hostname"] not in actual_hosts, "witness must not be an SddcSpec data host")

    expected_networks = index_by(scenario["networks"], "networkType", "scenario networks")
    actual_networks = index_by(sddc.get("networkSpecs", []), "networkType", "SddcSpec networks")
    require(set(actual_networks) == set(expected_networks), "SddcSpec network set mismatch")
    for network_type, expected in expected_networks.items():
        actual = actual_networks[network_type]
        require(actual.get("vlanId") == expected["vlanId"], f"{network_type} VLAN mismatch")
        require(actual.get("subnet") == expected["subnet"], f"{network_type} subnet mismatch")
        require(actual.get("gateway") == expected["gateway"], f"{network_type} gateway mismatch")
        require(actual.get("mtu") == expected["mtu"], f"{network_type} MTU mismatch")
        require(
            actual.get("includeIpAddressRanges") == [{
                "startIpAddress": expected["startIpAddress"],
                "endIpAddress": expected["endIpAddress"],
            }],
            f"{network_type} address range mismatch",
        )
    require(len(sddc.get("dvsSpecs", [])) == 1, "exactly one management DVS is required")
    dvs = sddc["dvsSpecs"][0]
    require(dvs.get("dvsName") == scenario["distributedSwitch"]["name"], "DVS name mismatch")
    require(dvs.get("mtu") == scenario["distributedSwitch"]["mtu"], "DVS MTU mismatch")
    require(dvs.get("vmnicsToUplinks") == scenario["distributedSwitch"]["uplinks"], "DVS uplinks mismatch")
    require(set(dvs.get("networks", [])) == set(expected_networks), "DVS network attachment mismatch")

    cluster = sddc["clusterSpec"]
    require(cluster.get("datacenterName") == scenario["cluster"]["datacenterName"], "datacenter name mismatch")
    require(cluster.get("clusterName") == scenario["cluster"]["clusterName"], "cluster name mismatch")
    pinned_greenfield = snapshot["greenfield"]
    vsan = sddc["datastoreSpec"]["vsanSpec"]
    require(vsan.get("datastoreName") == scenario["cluster"]["datastoreName"], "datastore name mismatch")
    require(vsan.get("esaConfig", {}).get("enabled") == pinned_greenfield["esaEnabled"], "vSAN ESA mismatch")
    require(vsan.get("failuresToTolerate") == pinned_greenfield["failuresToTolerate"], "vSAN FTT mismatch")

    topology = greenfield["topology"]
    availability = scenario["availability"]
    data_sites = [site["id"] for site in scenario["sites"] if site["role"] == "data"]
    witness_sites = [site["id"] for site in scenario["sites"] if site["role"] == "witness"]
    require(topology.get("mode") == availability["topology"] == pinned_greenfield["topology"], "topology mode mismatch")
    require(topology.get("dataSites") == data_sites, "data-site order mismatch")
    require(topology.get("preferredSite") == availability["preferredSite"], "preferred site mismatch")
    require(topology.get("dataSiteFailuresToTolerate") == availability["dataSiteFailuresToTolerate"], "site FTT mismatch")
    require(len(data_sites) == pinned_greenfield["dataSiteCount"], "data-site count mismatch")
    require(len(witness_sites) == 1, "exactly one witness site is required")
    require(topology.get("interSiteRttMs") == availability["measuredInterSiteRttMs"], "inter-site RTT mismatch")
    require(topology.get("witnessRttMs") == availability["measuredWitnessRttMs"], "witness RTT mismatch")
    require(topology["interSiteRttMs"] <= pinned_greenfield["maximumInterSiteRttMs"], "inter-site RTT exceeds snapshot")
    require(topology["witnessRttMs"] <= pinned_greenfield["maximumWitnessRttMs"], "witness RTT exceeds snapshot")
    require(topology.get("storagePolicy") == pinned_greenfield["storagePolicy"], "storage policy mismatch")

    fault_domains = index_by(topology.get("faultDomains", []), "site", "fault domains")
    require(set(fault_domains) == set(data_sites), "fault domains must match the two data sites")
    for site in data_sites:
        hosts = [host["hostname"] for host in scenario["hosts"] if host["site"] == site]
        require(fault_domains[site].get("hosts") == hosts, f"host placement mismatch for {site}")
        require(len(hosts) >= pinned_greenfield["minimumHostsPerDataSite"], f"too few hosts in {site}")
    require(len(expected_hosts) >= pinned_greenfield["minimumTotalDataHosts"], "too few total data hosts")

    witness = topology["witness"]
    fixture_witness = scenario["witness"]
    require(witness.get("hostname") == fixture_witness["hostname"], "witness hostname mismatch")
    require(witness.get("site") == fixture_witness["site"] == witness_sites[0], "witness site mismatch")
    require(witness["site"] not in data_sites, "witness site must be independent")
    require(witness.get("dedicated") is True, "witness must be dedicated")
    require(witness.get("memberOfDataCluster") is False, "witness cannot be a data-cluster member")
    bom = index_by(snapshot["billOfMaterials"], "id", "snapshot BOM")
    require(witness.get("version") == bom["vsan-witness-esa"]["version"], "witness version mismatch")
    require(witness.get("build") == bom["vsan-witness-esa"]["build"], "witness build mismatch")

    required = scenario["requiredAfterDataSiteFailure"]
    headroom = required["headroomPercent"]
    required_with_headroom = {
        "physicalCores": ceil_headroom(required["physicalCores"], headroom),
        "memoryGiB": ceil_headroom(required["memoryGiB"], headroom),
        "mirroredStorageGiB": ceil_headroom(required["mirroredStorageGiB"], headroom),
    }
    capacity = greenfield["capacity"]
    require(capacity.get("headroomPercent") == headroom, "headroom percentage mismatch")
    require(capacity.get("requiredAfterDataSiteFailure") == {
        "physicalCores": required["physicalCores"],
        "memoryGiB": required["memoryGiB"],
        "mirroredStorageGiB": required["mirroredStorageGiB"],
    }, "base capacity requirement mismatch")
    require(capacity.get("requiredWithHeadroom") == required_with_headroom, "headroom calculation mismatch")
    per_site = index_by(capacity.get("availableAfterDataSiteFailure", []), "site", "site capacity")
    require(set(per_site) == set(data_sites), "site capacity entries mismatch")
    for site in data_sites:
        hosts = [host for host in scenario["hosts"] if host["site"] == site]
        expected = {
            "site": site,
            "physicalCores": sum(host["physicalCores"] for host in hosts),
            "memoryGiB": sum(host["memoryGiB"] for host in hosts),
            "mirroredStorageGiB": sum(host["rawStorageGiB"] for host in hosts),
        }
        require(per_site[site] == expected, f"capacity calculation mismatch for {site}")
        for key in ("physicalCores", "memoryGiB", "mirroredStorageGiB"):
            require(expected[key] >= required_with_headroom[key], f"{site} lacks {key} capacity")
    require(capacity.get("meetsDataSiteFailureRequirement") is True, "site-failure capacity must pass")
    require(greenfield["billOfMaterials"] == snapshot["billOfMaterials"], "greenfield BOM differs from snapshot")

    plan = artifact["existingEstate"]["migrationPlan"]
    require(plan["estateId"] == estate["estateId"], "migration estateId mismatch")
    require(plan["sourceRelease"] == estate["sourceRelease"], "migration source release mismatch")
    require(plan["convergenceRelease"] == estate["convergenceRelease"], "migration convergence release mismatch")
    require(plan["targetRelease"] == estate["targetRelease"] == snapshot["targetRelease"], "migration target mismatch")
    inventory = index_by(estate["components"], "id", "estate inventory")
    paths = index_by(snapshot["migrationPaths"], "componentId", "snapshot migration paths")
    steps = index_by(plan["steps"], "componentId", "migration steps")
    require(set(steps) == set(inventory), "migration plan must name every and only inventoried component")
    require(set(paths) == set(inventory), "snapshot paths must cover every inventoried component")
    require([step["order"] for step in plan["steps"]] == list(range(1, len(plan["steps"]) + 1)), "step order must be strict and contiguous")
    for component_id, component in inventory.items():
        path = paths[component_id]
        expected = {
            "order": path["order"],
            "componentId": component_id,
            "name": component["name"],
            "currentVersion": component["version"],
            "targetVersion": path["to"],
            "method": path["method"],
            "gates": path["gates"],
        }
        if "convergence" in path:
            expected["convergence"] = path["convergence"]
        require(steps[component_id] == expected, f"migration step for {component_id} differs from pinned path")

    nsx_step = steps["nsx"]
    require(nsx_step["convergence"]["disposition"] == "skip-newer-current", "newer NSX must skip the older bundle")
    current_build = int(nsx_step["currentVersion"].rsplit("-", 1)[1])
    bundle_build = int(nsx_step["convergence"]["bundleTargetVersion"].rsplit("-", 1)[1])
    require(current_build > bundle_build, "NSX fixture no longer represents a back-in-time transition")


def main() -> int:
    try:
        artifact = load_json(ROOT / "architecture.json")
        openapi = load_json(ROOT / "specifications/vcf-installer/vcf-installer-openapi.json")
        try:
            sddc_spec = artifact["greenfield"]["sddcSpec"]
            sddc_schema = openapi["components"]["schemas"]["SddcSpec"]
        except (KeyError, TypeError) as exc:
            raise VerificationError(f"cannot locate greenfield SddcSpec: {exc}") from exc

        # This must remain the first validation/check performed on the design.
        validate_schema(sddc_spec, sddc_schema, openapi, "$.greenfield.sddcSpec")
        print("installer SddcSpec schema: ok")

        migration_schema = load_json(ROOT / "schemas/migration-plan.schema.json")
        try:
            migration_plan = artifact["existingEstate"]["migrationPlan"]
        except (KeyError, TypeError) as exc:
            raise VerificationError(f"cannot locate migration plan: {exc}") from exc
        validate_schema(migration_plan, migration_schema, migration_schema, "$.existingEstate.migrationPlan")
        print("migration plan schema: ok")

        scenario = load_json(ROOT / "fixtures/greenfield.json")
        estate = load_json(ROOT / "fixtures/estate.json")
        snapshot = load_json(ROOT / "compatibility/vcf-9.0.0-snapshot.json")
        spec_bytes = (ROOT / "specifications/vcf-installer/vcf-installer-openapi.json").read_bytes()
        require(
            hashlib.sha256(spec_bytes).hexdigest() == snapshot["installerSpecification"]["sha256"],
            "installer specification hash differs from pinned tag",
        )
        semantic_checks(artifact, scenario, estate, snapshot)
        print("architecture fixture and compatibility checks: ok")

        research = load_json(ROOT / "research/sources.json")
        validate_research_record(research)
        print("research source record: ok")
    except (KeyError, TypeError, ValueError, VerificationError) as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        return 1

    result = subprocess.run(["go", "test", "-race", "./..."], cwd=ROOT, check=False)
    if result.returncode != 0:
        return result.returncode
    print("go test -race ./...: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
