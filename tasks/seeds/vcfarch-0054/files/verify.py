#!/usr/bin/env python3
"""Deterministic acceptance verifier for the VCF architecture artifact."""

from __future__ import annotations

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
    raise VerificationError(f"unsupported schema type {expected!r}")


def resolve_ref(document: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise VerificationError(f"external schema reference is not allowed: {ref}")
    node: Any = document
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            raise VerificationError(f"unresolvable schema reference: {ref}")
        node = node[part]
    if not isinstance(node, dict):
        raise VerificationError(f"schema reference is not an object: {ref}")
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
    if "const" in schema and value != schema["const"]:
        raise VerificationError(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise VerificationError(f"{path}: {value!r} is not an allowed value")
    if "allOf" in schema:
        for part in schema["allOf"]:
            validate_schema(value, part, document, path)
    if "oneOf" in schema:
        successes = 0
        for part in schema["oneOf"]:
            try:
                validate_schema(value, part, document, path)
                successes += 1
            except VerificationError:
                pass
        if successes != 1:
            raise VerificationError(f"{path}: expected exactly one matching schema")

    expected_type = schema.get("type")
    if expected_type is not None:
        types = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(json_type_matches(value, item) for item in types):
            raise VerificationError(f"{path}: expected type {expected_type}, got {type(value).__name__}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [name for name in required if name not in value]
        if missing:
            raise VerificationError(f"{path}: missing required properties {missing}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extras = sorted(set(value) - set(properties))
            if extras:
                raise VerificationError(f"{path}: unexpected properties {extras}")
        for name, child in value.items():
            if name in properties:
                validate_schema(child, properties[name], document, f"{path}.{name}")

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise VerificationError(f"{path}: too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise VerificationError(f"{path}: too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(encoded) != len(set(encoded)):
                raise VerificationError(f"{path}: items are not unique")
        if "items" in schema:
            for index, child in enumerate(value):
                validate_schema(child, schema["items"], document, f"{path}[{index}]")

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise VerificationError(f"{path}: string is too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise VerificationError(f"{path}: string is too long")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise VerificationError(f"{path}: string does not match {schema['pattern']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise VerificationError(f"{path}: number is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise VerificationError(f"{path}: number is above maximum")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def indexed(items: list[dict[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        value = item.get(key)
        require(isinstance(value, str), f"{label}: missing string key {key}")
        require(value not in result, f"{label}: duplicate {key} {value}")
        result[value] = item
    return result


def validate_research_record(record: Any) -> None:
    require(isinstance(record, dict), "research/sources.json must contain an object")
    sources = record.get("sources")
    require(isinstance(sources, list) and sources, "research sources must be a non-empty array")
    seen_urls: set[str] = set()
    for index, source in enumerate(sources):
        label = f"research source {index + 1}"
        require(isinstance(source, dict), f"{label} must be an object")
        require(isinstance(source.get("title"), str) and source["title"].strip() != "", f"{label} title is required")
        url = source.get("url")
        require(isinstance(url, str), f"{label} URL is required")
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        require(
            parsed.scheme == "https" and (hostname == "broadcom.com" or hostname.endswith(".broadcom.com")),
            f"{label} must use an official Broadcom HTTPS URL",
        )
        require(url not in seen_urls, f"duplicate research URL {url}")
        seen_urls.add(url)
        consulted_at = source.get("consultedAt")
        require(isinstance(consulted_at, str), f"{label} consultedAt date is required")
        try:
            parsed_date = date.fromisoformat(consulted_at)
        except ValueError as exc:
            raise VerificationError(f"{label} consultedAt must use YYYY-MM-DD format") from exc
        require(parsed_date.isoformat() == consulted_at, f"{label} consultedAt must use YYYY-MM-DD format")
        facts = source.get("factsUsed")
        require(
            isinstance(facts, list) and facts and all(isinstance(fact, str) and fact.strip() != "" for fact in facts),
            f"{label} factsUsed must be a non-empty array of non-empty strings",
        )


def semantic_checks(
    artifact: dict[str, Any],
    scenario: dict[str, Any],
    estate: dict[str, Any],
    snapshot: dict[str, Any],
) -> None:
    require(artifact.get("schemaVersion") == 1, "artifact schemaVersion must be 1")
    greenfield = artifact["greenfield"]
    sddc = greenfield["sddcSpec"]
    topology = greenfield.get("topology")
    capacity = greenfield.get("capacity")
    require(isinstance(topology, dict), "greenfield.topology must be an object")
    require(isinstance(capacity, dict), "greenfield.capacity must be an object")

    require(sddc.get("sddcId") == scenario["sddcId"], "SddcSpec sddcId mismatch")
    require(sddc.get("workflowType") == "VCF", "SddcSpec workflowType must be VCF")
    require(sddc.get("version") == snapshot["targetRelease"], "SddcSpec version mismatch")
    require(sddc.get("vcfInstanceName") == scenario["vcfInstanceName"], "VCF instance name mismatch")
    require(sddc.get("managementPoolName") == scenario["managementPoolName"], "management pool mismatch")
    require(sddc.get("skipEsxThumbprintValidation") is False, "ESXi thumbprint validation must not be skipped")
    require(sddc.get("skipGatewayPingValidation") is False, "gateway validation must not be skipped")

    infra = scenario["infrastructure"]
    require(sddc["vcenterSpec"].get("vcenterHostname") == infra["vcenterHostname"], "vCenter hostname mismatch")
    require(sddc.get("sddcManagerSpec", {}).get("hostname") == infra["sddcManagerHostname"], "SDDC Manager hostname mismatch")
    nsx = sddc.get("nsxtSpec", {})
    require(nsx.get("vipFqdn") == infra["nsxVipHostname"], "NSX VIP mismatch")
    require(
        [item.get("hostname") for item in nsx.get("nsxtManagers", [])] == infra["nsxManagerHostnames"],
        "NSX manager list mismatch",
    )
    require(sddc["dnsSpec"].get("subdomain") == scenario["domainName"], "DNS subdomain mismatch")
    require(sddc["dnsSpec"].get("nameservers") == infra["dnsServers"], "DNS servers mismatch")
    require(sddc.get("ntpServers") == infra["ntpServers"], "NTP servers mismatch")

    expected_hosts = {item["hostname"] for item in scenario["hosts"]}
    actual_hosts = {item.get("hostname") for item in sddc.get("hostSpecs", [])}
    require(actual_hosts == expected_hosts, "SddcSpec must contain every and only data host")
    witness = scenario["witness"]
    require(witness["hostname"] not in actual_hosts, "witness must not be in SddcSpec.hostSpecs")

    expected_networks = indexed(scenario["networks"], "type", "scenario networks")
    actual_networks = indexed(sddc.get("networkSpecs", []), "networkType", "SddcSpec networks")
    require(set(actual_networks) == set(expected_networks), "SddcSpec network types mismatch")
    for network_type, expected in expected_networks.items():
        actual = actual_networks[network_type]
        for source_key, artifact_key in (
            ("vlanId", "vlanId"),
            ("subnet", "subnet"),
            ("gateway", "gateway"),
            ("mtu", "mtu"),
        ):
            require(actual.get(artifact_key) == expected[source_key], f"{network_type} {artifact_key} mismatch")
        require(
            actual.get("includeIpAddressRanges") == [
                {"startIpAddress": expected["start"], "endIpAddress": expected["end"]}
            ],
            f"{network_type} IP range mismatch",
        )
    dvs_networks = sddc.get("dvsSpecs", [{}])[0].get("networks", [])
    require(set(dvs_networks) == set(expected_networks), "DVS network attachment mismatch")

    vsan = sddc.get("datastoreSpec", {}).get("vsanSpec", {})
    pinned_greenfield = snapshot["greenfield"]
    require(vsan.get("failuresToTolerate") == pinned_greenfield["failuresToTolerate"], "vSAN FTT mismatch")
    require(vsan.get("esaConfig", {}).get("enabled") == pinned_greenfield["esaEnabled"], "vSAN ESA setting mismatch")

    data_sites = [site["id"] for site in scenario["sites"] if site["role"] == "data"]
    witness_sites = [site["id"] for site in scenario["sites"] if site["role"] == "witness"]
    require(len(data_sites) == pinned_greenfield["dataSiteCount"], "scenario data-site count mismatch")
    require(len(witness_sites) == 1, "scenario must have one witness site")
    require(topology.get("mode") == "stretched-management-domain", "topology mode mismatch")
    require(topology.get("dataSites") == data_sites, "topology data sites mismatch")
    preferred = next(site["id"] for site in scenario["sites"] if site.get("preferred"))
    require(topology.get("preferredSite") == preferred, "preferred site mismatch")

    fault_domains = indexed(topology.get("faultDomains", []), "site", "fault domains")
    require(set(fault_domains) == set(data_sites), "fault domains must be the two data sites")
    for site in data_sites:
        expected = [host["hostname"] for host in scenario["hosts"] if host["site"] == site]
        require(fault_domains[site].get("hosts") == expected, f"host placement mismatch for {site}")
        require(len(expected) >= pinned_greenfield["minimumHostsPerDataSite"], f"too few hosts in {site}")
    require(len(expected_hosts) >= pinned_greenfield["minimumStretchedHosts"], "too few stretched-cluster hosts")

    topo_witness = topology.get("witness", {})
    require(topo_witness.get("hostname") == witness["hostname"], "witness hostname mismatch")
    require(topo_witness.get("site") == witness["site"] == witness_sites[0], "witness site mismatch")
    require(topo_witness.get("site") not in data_sites, "witness must be in an independent site")
    require(topo_witness.get("dedicated") is True, "witness must be dedicated")
    require(topo_witness.get("memberOfDataCluster") is False, "witness cannot be a data-cluster member")
    bom = indexed(snapshot["billOfMaterials"], "id", "snapshot BOM")
    witness_bom = bom["vsan-witness-esa"]
    require(topo_witness.get("version") == witness_bom["version"], "witness version mismatch")
    require(topo_witness.get("build") == witness_bom["build"], "witness build mismatch")
    vm_policy = topology.get("managementVmPolicy", {})
    require(vm_policy.get("siteAffinity") == "balanced", "management VM site affinity must be balanced")
    require(vm_policy.get("restartOnSurvivingSite") is True, "management VMs must restart on surviving site")

    headroom = scenario["requiredAfterSiteFailure"]["headroomPercent"]
    required = scenario["requiredAfterSiteFailure"]
    required_with_headroom = {
        "cores": math.ceil(required["cores"] * (100 + headroom) / 100),
        "memoryGiB": math.ceil(required["memoryGiB"] * (100 + headroom) / 100),
        "mirroredStorageGiB": math.ceil(required["mirroredStorageGiB"] * (100 + headroom) / 100),
    }
    require(capacity.get("headroomPercent") == headroom, "capacity headroom mismatch")
    require(capacity.get("requiredWithHeadroom") == required_with_headroom, "headroom calculation mismatch")
    per_site = indexed(capacity.get("perSite", []), "site", "capacity perSite")
    require(set(per_site) == set(data_sites), "capacity must cover both data sites")
    for site in data_sites:
        hosts = [host for host in scenario["hosts"] if host["site"] == site]
        expected_capacity = {
            "site": site,
            "cores": sum(host["cores"] for host in hosts),
            "memoryGiB": sum(host["memoryGiB"] for host in hosts),
            "rawStorageGiB": sum(host["rawStorageGiB"] for host in hosts),
        }
        require(per_site[site] == expected_capacity, f"capacity calculation mismatch for {site}")
        require(per_site[site]["cores"] >= required_with_headroom["cores"], f"{site} lacks core capacity")
        require(per_site[site]["memoryGiB"] >= required_with_headroom["memoryGiB"], f"{site} lacks memory capacity")
        require(
            per_site[site]["rawStorageGiB"] >= required_with_headroom["mirroredStorageGiB"],
            f"{site} lacks mirrored-storage capacity",
        )
    require(capacity.get("survivesDataSiteLoss") is True, "capacity assessment must confirm site-loss survival")
    require(greenfield.get("billOfMaterials") == snapshot["billOfMaterials"], "greenfield BOM differs from snapshot")

    plan = artifact["existingEstate"]["migrationPlan"]
    require(plan.get("estateId") == estate["estateId"], "migration estateId mismatch")
    require(plan.get("sourceRelease") == estate["sourceRelease"], "migration source release mismatch")
    require(plan.get("targetRelease") == estate["targetRelease"] == snapshot["targetRelease"], "migration target mismatch")
    inventory = indexed(estate["components"], "id", "estate inventory")
    paths = indexed(snapshot["upgradePaths"], "componentId", "snapshot upgrade paths")
    steps = indexed(plan["steps"], "componentId", "migration steps")
    require(set(steps) == set(inventory), "migration plan must name every and only inventoried component")
    require(set(paths) == set(inventory), "snapshot paths must cover the complete inventory")
    require([step.get("order") for step in plan["steps"]] == list(range(1, len(plan["steps"]) + 1)), "migration order must be strict and contiguous")
    for component_id, component in inventory.items():
        step = steps[component_id]
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
        require(step == expected, f"migration step for {component_id} differs from pinned path")


def main() -> int:
    try:
        artifact = load_json(ROOT / "architecture.json")
        openapi = load_json(ROOT / "specifications/vcf-installer/vcf-installer-openapi.json")
        try:
            sddc_spec = artifact["greenfield"]["sddcSpec"]
            sddc_schema = openapi["components"]["schemas"]["SddcSpec"]
        except (KeyError, TypeError) as exc:
            raise VerificationError(f"cannot locate greenfield SddcSpec: {exc}") from exc

        # This is intentionally the first validation performed on the design.
        validate_schema(sddc_spec, sddc_schema, openapi, "$.greenfield.sddcSpec")
        print("installer SddcSpec schema: ok")

        migration_schema = load_json(ROOT / "schemas/migration-plan.schema.json")
        try:
            migration_plan = artifact["existingEstate"]["migrationPlan"]
        except (KeyError, TypeError) as exc:
            raise VerificationError(f"cannot locate existing-estate migration plan: {exc}") from exc
        validate_schema(migration_plan, migration_schema, migration_schema, "$.existingEstate.migrationPlan")
        print("migration plan schema: ok")

        scenario = load_json(ROOT / "fixtures/scenario.json")
        estate = load_json(ROOT / "fixtures/estate.json")
        snapshot = load_json(ROOT / "compatibility/vcf-9.0.0-snapshot.json")
        semantic_checks(artifact, scenario, estate, snapshot)
        print("architecture fixture and snapshot checks: ok")

        research = load_json(ROOT / "research/sources.json")
        validate_research_record(research)
        print("research source record: ok")
    except VerificationError as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        return 1

    result = subprocess.run(["go", "test", "-race", "./..."], cwd=ROOT, check=False)
    if result.returncode != 0:
        return result.returncode
    print("go test -race ./...: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
