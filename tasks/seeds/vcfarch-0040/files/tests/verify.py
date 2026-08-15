#!/usr/bin/env python3
"""Deterministic protected verifier for the VCF architecture artifacts.

The verifier checks the research record's requested structure without fixing
source selection or attempting nondeterministic network access.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
ARTIFACT = BUILD / "architecture.json"
RESEARCH_RECORD = BUILD / "research-consulted.json"


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant {value}")
            ),
        )
    except (OSError, ValueError) as exc:
        raise VerificationError(f"cannot read valid JSON from {path.relative_to(ROOT)}: {exc}") from exc


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
    raise VerificationError(f"unsupported JSON Schema type in protected verifier: {expected}")


def resolve_pointer(document: Any, pointer: str) -> Any:
    require(pointer.startswith("#/"), f"only local JSON pointers are supported: {pointer}")
    current = document
    for raw in pointer[2:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        require(isinstance(current, dict) and token in current, f"unresolved schema pointer {pointer}")
        current = current[token]
    return current


def validate_schema(value: Any, schema: dict[str, Any], root_schema: dict[str, Any], path: str = "$") -> None:
    """Validate the JSON Schema/OpenAPI keywords exercised by the pinned schemas."""
    if "$ref" in schema:
        validate_schema(value, resolve_pointer(root_schema, schema["$ref"]), root_schema, path)
        return

    for branch in schema.get("allOf", []):
        validate_schema(value, branch, root_schema, path)

    if "const" in schema:
        require(value == schema["const"], f"{path}: expected constant {schema['const']!r}, got {value!r}")
    if "enum" in schema:
        require(value in schema["enum"], f"{path}: {value!r} is not in {schema['enum']!r}")

    expected_type = schema.get("type")
    if expected_type is not None:
        if isinstance(expected_type, list):
            valid_type = any(json_type_matches(value, item) for item in expected_type)
        else:
            valid_type = json_type_matches(value, expected_type)
        require(valid_type, f"{path}: expected type {expected_type}, got {type(value).__name__}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [name for name in required if name not in value]
        require(not missing, f"{path}: missing required properties {missing}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extras = sorted(set(value) - set(properties))
            require(not extras, f"{path}: unexpected properties {extras}")
        for name, child in properties.items():
            if name in value:
                validate_schema(value[name], child, root_schema, f"{path}.{name}")

    if isinstance(value, list):
        if "minItems" in schema:
            require(len(value) >= schema["minItems"], f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema:
            require(len(value) <= schema["maxItems"], f"{path}: more than {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            require(len(encoded) == len(set(encoded)), f"{path}: items are not unique")
        if "items" in schema:
            for index, item in enumerate(value):
                validate_schema(item, schema["items"], root_schema, f"{path}[{index}]")

    if isinstance(value, str):
        if "minLength" in schema:
            require(len(value) >= schema["minLength"], f"{path}: string is too short")
        if "maxLength" in schema:
            require(len(value) <= schema["maxLength"], f"{path}: string is too long")
        if "pattern" in schema:
            require(re.search(schema["pattern"], value) is not None, f"{path}: value does not match {schema['pattern']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema:
            require(value >= schema["minimum"], f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema:
            require(value <= schema["maximum"], f"{path}: above maximum {schema['maximum']}")
        if "exclusiveMinimum" in schema:
            require(value > schema["exclusiveMinimum"], f"{path}: not above exclusive minimum")
        if "exclusiveMaximum" in schema:
            require(value < schema["exclusiveMaximum"], f"{path}: not below exclusive maximum")


def compile_and_run_client() -> None:
    BUILD.mkdir(exist_ok=True)
    for output in (ARTIFACT, RESEARCH_RECORD):
        if output.exists():
            output.unlink()

    with tempfile.TemporaryDirectory(prefix="vcfarch-classes-") as classes:
        compile_result = subprocess.run(
            ["javac", "-encoding", "UTF-8", "-d", classes, "Main.java", "TestMain.java"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
        )
        require(compile_result.returncode == 0, f"javac failed:\n{compile_result.stdout}")
        run_result = subprocess.run(
            ["java", "-cp", classes, "TestMain"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
        )
        require(run_result.returncode == 0, f"TestMain failed:\n{run_result.stdout}")
    require(ARTIFACT.is_file(), "TestMain did not produce build/architecture.json")
    require(RESEARCH_RECORD.is_file(), "TestMain did not produce build/research-consulted.json")


def verify_research_record(record: Any) -> None:
    require(isinstance(record, list), "research record must be a JSON array")
    require(record, "research record must name at least one consulted source")
    required_fields = ("title", "url", "retrievedAt", "supports")
    for index, entry in enumerate(record):
        require(isinstance(entry, dict), f"research record entry {index} must be an object")
        for field in required_fields:
            require(isinstance(entry.get(field), str) and entry[field].strip(),
                    f"research record entry {index}.{field} must be a non-empty string")
        parsed_url = urlparse(entry["url"])
        require(parsed_url.scheme in {"http", "https"} and parsed_url.hostname,
                f"research record entry {index}.url must be an absolute HTTP(S) URL")
        hostname = parsed_url.hostname.lower()
        require(hostname not in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
                and not hostname.endswith(".invalid")
                and "fixtures/" not in entry["url"].lower(),
                f"research record entry {index}.url must identify a real published source")


def capacity(physical_cores: int, memory_gib: int, raw_vsan_tb: float) -> dict[str, Any]:
    return {
        "physicalCores": physical_cores,
        "memoryGiB": memory_gib,
        "rawVsanTb": raw_vsan_tb,
    }


def require_capacity(actual: dict[str, Any], expected: dict[str, Any], label: str) -> None:
    require(actual["physicalCores"] == expected["physicalCores"], f"{label}: physical core total is wrong")
    require(actual["memoryGiB"] == expected["memoryGiB"], f"{label}: memory total is wrong")
    require(math.isclose(actual["rawVsanTb"], expected["rawVsanTb"], rel_tol=0, abs_tol=1e-9),
            f"{label}: raw vSAN total is wrong")


def enforce_host_count_for_ftt(hostnames: list[str], placements: list[dict[str, Any]], ftt: int) -> None:
    """Fail when data-host count/placement cannot support the stated host FTT."""
    minimum_cluster_hosts = 2 * ftt + 1
    require(len(hostnames) >= minimum_cluster_hosts,
            f"host count {len(hostnames)} contradicts failuresToTolerate={ftt}; need at least {minimum_cluster_hosts}")
    counts: dict[str, int] = {}
    for placement in placements:
        counts[placement["siteId"]] = counts.get(placement["siteId"], 0) + 1
    for site_id in ("CHI-A", "CHI-B"):
        require(counts.get(site_id, 0) >= ftt + 1,
                f"{site_id} host count contradicts failuresToTolerate={ftt}; need at least {ftt + 1} per data site")


def self_test_ftt_guard() -> None:
    contradictory_hosts = ["h1", "h2", "h3", "h4"]
    contradictory_placements = [
        {"siteId": "CHI-A"}, {"siteId": "CHI-A"},
        {"siteId": "CHI-B"}, {"siteId": "CHI-B"},
    ]
    try:
        enforce_host_count_for_ftt(contradictory_hosts, contradictory_placements, 2)
    except VerificationError:
        return
    raise VerificationError("protected FTT guard accepted a contradictory host count")


def verify_greenfield(artifact: dict[str, Any], inputs: dict[str, Any], snapshot: dict[str, Any]) -> None:
    greenfield = artifact["greenfield"]
    requirements = greenfield["requirements"]
    expected_requirements = {
        "dataHostCount": 8,
        "hostsPerDataSite": 4,
        "failuresToTolerate": 2,
        "surviveEitherDataSiteLoss": True,
        "minimumSurvivingCores": 220,
        "minimumSurvivingMemoryGiB": 3072,
        "minimumSurvivingRawVsanTb": 45.0,
        "dataSiteRttMs": 3,
        "maximumWitnessRttMs": 12,
    }
    require(requirements == expected_requirements, "greenfield requirements do not reproduce the stated design contract")
    require(greenfield["designId"] == inputs["designId"], "wrong designId")
    require(greenfield["supportedCombinationId"] == snapshot["supportedCombinationId"], "wrong compatibility combination")

    sites = greenfield["sites"]
    site_by_id = {site["siteId"]: site for site in sites}
    require(len(sites) == len(site_by_id) == 3 and set(site_by_id) == {"CHI-A", "CHI-B", "QCY-W"},
            "sites must contain exactly the two data sites and one witness site")
    require(site_by_id["CHI-A"]["role"] == "DATA" and site_by_id["CHI-B"]["role"] == "DATA", "CHI sites must be data sites")
    require(site_by_id["QCY-W"]["role"] == "WITNESS_ONLY", "QCY-W must be witness-only")
    fault_domains = {site["faultDomainId"] for site in site_by_id.values()}
    require(len(fault_domains) == 3, "all three sites must use independent fault domains")

    expected_hosts = [host for site_id in inputs["dataSites"] for host in inputs["hostnames"][site_id]]
    placements = greenfield["hostPlacements"]
    placement_by_host = {item["hostname"]: item for item in placements}
    require(len(placement_by_host) == len(placements), "duplicate data-host placement")
    require(set(placement_by_host) == set(expected_hosts), "host placements do not exactly match the supplied data hosts")
    for site_id in inputs["dataSites"]:
        for hostname in inputs["hostnames"][site_id]:
            placement = placement_by_host[hostname]
            require(placement["siteId"] == site_id, f"{hostname} is at the wrong site")
            require(placement["faultDomainId"] == site_by_id[site_id]["faultDomainId"], f"{hostname} is in the wrong fault domain")

    sddc = greenfield["sddcSpec"]
    sddc_hosts = [host["hostname"] for host in sddc.get("hostSpecs", [])]
    require(len(sddc_hosts) == len(set(sddc_hosts)), "duplicate SddcSpec host")
    ftt = sddc.get("datastoreSpec", {}).get("vsanSpec", {}).get("failuresToTolerate")
    require(ftt == requirements["failuresToTolerate"], "SddcSpec FTT differs from stated FTT")
    enforce_host_count_for_ftt(sddc_hosts, placements, ftt)
    require(set(sddc_hosts) == set(expected_hosts), "SddcSpec hostSpecs must be exactly the eight data hosts")

    witness = greenfield["witness"]
    require(witness["hostname"] == inputs["witnessFqdn"], "wrong witness hostname")
    require(witness["siteId"] == "QCY-W", "witness must be placed at QCY-W")
    require(witness["faultDomainId"] == site_by_id["QCY-W"]["faultDomainId"], "witness fault domain is wrong")
    require(witness["hostname"] not in sddc_hosts and witness["hostname"] not in placement_by_host,
            "witness must not be counted as a data host")

    per_host = greenfield["perHostCapacity"]
    require_capacity(per_host, capacity(64, 1024, 15.36), "per-host capacity")
    require_capacity(greenfield["derivedCapacity"]["total"], capacity(512, 8192, 122.88), "total capacity")
    require_capacity(greenfield["derivedCapacity"]["afterEitherDataSiteLoss"], capacity(256, 4096, 61.44), "surviving-site capacity")
    surviving = greenfield["derivedCapacity"]["afterEitherDataSiteLoss"]
    require(surviving["physicalCores"] >= requirements["minimumSurvivingCores"], "surviving cores miss requirement")
    require(surviving["memoryGiB"] >= requirements["minimumSurvivingMemoryGiB"], "surviving memory misses requirement")
    require(surviving["rawVsanTb"] >= requirements["minimumSurvivingRawVsanTb"], "surviving raw vSAN misses requirement")

    component_targets = greenfield["componentTargets"]
    target_by_component = {item["component"]: item["version"] for item in component_targets}
    expected_targets = {item["component"]: item["version"] for item in snapshot["greenfieldBom"]}
    require(len(target_by_component) == len(component_targets), "componentTargets contains a duplicate component")
    require(target_by_component == expected_targets, "componentTargets must contain exactly the pinned greenfield BOM")
    require(sddc["sddcId"] == inputs["sddcId"], "wrong SddcSpec sddcId")
    require(sddc.get("workflowType") == "VCF", "workflowType must be VCF")
    require(sddc.get("version") == snapshot["targetVcfRelease"], "wrong SddcSpec VCF version")
    require(sddc.get("vcfInstanceName") == inputs["vcfInstanceName"], "wrong VCF instance name")
    require(sddc.get("managementPoolName") == inputs["networkPoolName"], "wrong management pool")
    require(sddc["dnsSpec"] == {"subdomain": inputs["dnsDomain"], "nameservers": inputs["dnsServers"]}, "wrong DNS design")
    require(len(sddc.get("ntpServers", [])) == len(set(sddc.get("ntpServers", [])))
            and set(sddc.get("ntpServers", [])) == set(inputs["ntpServers"]), "wrong NTP design")

    actual_networks = {
        item["networkType"]: {"vlanId": item["vlanId"], "subnet": item["subnet"], "gateway": item["gateway"]}
        for item in sddc["networkSpecs"]
    }
    expected_networks = {
        item["networkType"]: {"vlanId": item["vlanId"], "subnet": item["subnet"], "gateway": item["gateway"]}
        for item in inputs["networks"]
    }
    require(actual_networks == expected_networks and len(sddc["networkSpecs"]) == len(expected_networks), "network design differs from design_inputs.json")
    matching_dvs = [item for item in sddc.get("dvsSpecs", []) if item.get("dvsName") == inputs["dvsName"]]
    require(len(matching_dvs) == 1, "design must contain the supplied management VDS exactly once")
    dvs = matching_dvs[0]
    if dvs.get("networks"):
        require(set(dvs["networks"]) == set(expected_networks), "VDS does not carry all required networks")

    require(sddc["vcenterSpec"].get("vcenterHostname") == inputs["vcenterFqdn"], "wrong vCenter hostname")
    require(sddc["vcenterSpec"].get("version") == "9.1.0.0.25370922", "wrong vCenter build")
    require(sddc["sddcManagerSpec"].get("hostname") == inputs["sddcManagerHostname"], "wrong SDDC Manager hostname")
    require(sddc["sddcManagerSpec"].get("version") == "9.1.0.0", "wrong SDDC Manager version")
    actual_nsx_managers = [item.get("hostname") for item in sddc["nsxtSpec"]["nsxtManagers"]]
    require(len(actual_nsx_managers) == len(set(actual_nsx_managers))
            and set(actual_nsx_managers) == set(inputs["nsxManagerHostnames"]), "wrong NSX managers")
    require(sddc["nsxtSpec"].get("vipFqdn") == inputs["nsxVipFqdn"], "wrong NSX VIP")
    require(sddc["nsxtSpec"].get("version") == "9.1.0.0.25318225", "wrong NSX build")

    def check_new_deployment(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for name, child in value.items():
                child_path = f"{path}.{name}"
                if name == "useExistingDeployment":
                    require(child is False, f"{child_path} must be false")
                check_new_deployment(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                check_new_deployment(child, f"{path}[{index}]")

    check_new_deployment(sddc, "$.greenfield.sddcSpec")


def verify_migration(artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    migration = artifact["existingEstateMigration"]
    require(migration["estateId"] == inventory["estateId"], "migration estateId differs from inventory")
    require(migration["targetRelease"] == snapshot["targetVcfRelease"], "migration target release differs from snapshot")

    inventory_by_id = {item["componentId"]: item for item in inventory["components"]}
    rules_by_id = {item["componentId"]: item for item in snapshot["migrationRules"]}
    steps = migration["steps"]
    step_by_id = {item["componentId"]: item for item in steps}
    require(len(inventory_by_id) == len(inventory["components"]), "duplicate component in estate inventory")
    require(len(step_by_id) == len(steps), "migration names a component more than once")
    require(set(step_by_id) == set(inventory_by_id) == set(rules_by_id), "migration must name every and only inventory component")

    previous_order = -1
    for step in steps:
        component_id = step["componentId"]
        item = inventory_by_id[component_id]
        rule = rules_by_id[component_id]
        require(step["order"] > previous_order, "migration steps are not strictly ordered")
        previous_order = step["order"]
        require(step["componentName"] == item["componentName"], f"wrong name for {component_id}")
        require(step["currentVersion"] == item["version"], f"wrong current version for {component_id}")
        require(step["targetVersion"] == rule["targetVersion"], f"wrong target for {component_id}")
        require(step["order"] == rule["order"], f"wrong order for {component_id}")
        require(step["gates"] == rule["gates"], f"wrong gates for {component_id}")


def main() -> int:
    try:
        self_test_ftt_guard()
        compile_and_run_client()
        artifact = load_json(ARTIFACT)
        research_record = load_json(RESEARCH_RECORD)
        artifact_schema = load_json(ROOT / "fixtures" / "architecture_artifact.schema.json")
        inputs = load_json(ROOT / "fixtures" / "design_inputs.json")
        inventory = load_json(ROOT / "fixtures" / "estate_inventory.json")
        snapshot = load_json(ROOT / "fixtures" / "compatibility_snapshot.json")
        openapi = load_json(ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json")

        validate_schema(artifact, artifact_schema, artifact_schema)
        validate_schema(
            artifact["greenfield"]["sddcSpec"],
            {"$ref": "#/components/schemas/SddcSpec"},
            openapi,
            "$.greenfield.sddcSpec",
        )
        verify_greenfield(artifact, inputs, snapshot)
        verify_migration(artifact, inventory, snapshot)
        verify_research_record(research_record)
    except (VerificationError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print("PASS: research record, architecture schema, VCF 9.1 SddcSpec, placement, FTT/capacity, BOM, and migration plan")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
