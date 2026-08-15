#!/usr/bin/env python3
"""Deterministic offline verifier for vcfarch-0037."""

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
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
OPENAPI = ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
OPENAPI_SHA256 = "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d"
PROTECTED_SHA256 = {
    ROOT / "TestMain.java": "58d16b398ae36f1a7c4ca6ed432da09fbc9147b5b6b6f37838ed0b60541334e0",
    ROOT / "fixtures" / "estate-inventory.json":
        "27b95a1808c0043f1fb4cd0abf444c3f9a869583f6617d0421de4ede71938f88",
    ROOT / "fixtures" / "compatibility-snapshot.json":
        "696625be8440962c8f47116fceb83f974fbab943f7799cbb95d5cba9885d80e3",
    ROOT / "migration-plan-schema.json":
        "2a7f3850a2626fb88e821b73565e34066b50d2946baf465f68ca3ec9736e62eb",
}


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def json_pointer(document: Any, pointer: str) -> Any:
    require(pointer.startswith("#/"), f"unsupported schema reference: {pointer}")
    value = document
    for token in pointer[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        value = value[token]
    return value


def type_matches(value: Any, kind: str) -> bool:
    if kind == "object":
        return isinstance(value, dict)
    if kind == "array":
        return isinstance(value, list)
    if kind == "string":
        return isinstance(value, str)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "null":
        return value is None
    return True


def validate_json_schema(value: Any, schema: Any, document: Any, path: str = "$") -> None:
    """Validate the JSON Schema keywords used by the pinned OpenAPI document."""
    if isinstance(schema, bool):
        require(schema, f"{path}: rejected by false schema")
        return
    require(isinstance(schema, dict), f"{path}: invalid schema node")

    if "$ref" in schema:
        validate_json_schema(value, json_pointer(document, schema["$ref"]), document, path)
        return

    if value is None and schema.get("nullable") is True:
        return

    for sub_schema in schema.get("allOf", []):
        validate_json_schema(value, sub_schema, document, path)

    if "anyOf" in schema:
        matches = 0
        for sub_schema in schema["anyOf"]:
            try:
                validate_json_schema(value, sub_schema, document, path)
                matches += 1
            except VerificationError:
                pass
        require(matches >= 1, f"{path}: does not match any anyOf branch")

    if "oneOf" in schema:
        matches = 0
        for sub_schema in schema["oneOf"]:
            try:
                validate_json_schema(value, sub_schema, document, path)
                matches += 1
            except VerificationError:
                pass
        require(matches == 1, f"{path}: expected exactly one oneOf match, got {matches}")

    if "not" in schema:
        rejected = False
        try:
            validate_json_schema(value, schema["not"], document, path)
        except VerificationError:
            rejected = True
        require(rejected, f"{path}: matches forbidden schema")

    if "enum" in schema:
        require(value in schema["enum"], f"{path}: {value!r} is not in enum")

    expected_type = schema.get("type")
    if expected_type:
        kinds = expected_type if isinstance(expected_type, list) else [expected_type]
        require(any(type_matches(value, kind) for kind in kinds),
                f"{path}: expected {expected_type}, got {type(value).__name__}")

    if isinstance(value, dict):
        for key in schema.get("required", []):
            require(key in value, f"{path}: missing required property {key!r}")
        if "minProperties" in schema:
            require(len(value) >= schema["minProperties"], f"{path}: too few properties")
        if "maxProperties" in schema:
            require(len(value) <= schema["maxProperties"], f"{path}: too many properties")

        properties = schema.get("properties", {})
        pattern_properties = schema.get("patternProperties", {})
        for key, item in value.items():
            matched = False
            if key in properties:
                validate_json_schema(item, properties[key], document, f"{path}.{key}")
                matched = True
            for pattern, sub_schema in pattern_properties.items():
                if re.search(pattern, key):
                    validate_json_schema(item, sub_schema, document, f"{path}.{key}")
                    matched = True
            if not matched and "additionalProperties" in schema:
                additional = schema["additionalProperties"]
                require(additional is not False, f"{path}: unexpected property {key!r}")
                if isinstance(additional, dict):
                    validate_json_schema(item, additional, document, f"{path}.{key}")

    if isinstance(value, list):
        if "minItems" in schema:
            require(len(value) >= schema["minItems"], f"{path}: too few items")
        if "maxItems" in schema:
            require(len(value) <= schema["maxItems"], f"{path}: too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            require(len(encoded) == len(set(encoded)), f"{path}: duplicate array items")
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                validate_json_schema(item, schema["items"], document, f"{path}[{index}]")

    if isinstance(value, str):
        if "minLength" in schema:
            require(len(value) >= schema["minLength"], f"{path}: string is too short")
        if "maxLength" in schema:
            require(len(value) <= schema["maxLength"], f"{path}: string is too long")
        if "pattern" in schema:
            require(re.search(schema["pattern"], value) is not None,
                    f"{path}: string does not match {schema['pattern']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema:
            require(value >= schema["minimum"], f"{path}: below minimum")
        if "maximum" in schema:
            require(value <= schema["maximum"], f"{path}: above maximum")
        if "exclusiveMinimum" in schema and not isinstance(schema["exclusiveMinimum"], bool):
            require(value > schema["exclusiveMinimum"], f"{path}: below exclusive minimum")
        if "exclusiveMaximum" in schema and not isinstance(schema["exclusiveMaximum"], bool):
            require(value < schema["exclusiveMaximum"], f"{path}: above exclusive maximum")


def run_client(inventory_path: Path | None = None,
               snapshot_path: Path | None = None) -> dict[str, Any]:
    inventory_path = inventory_path or ROOT / "fixtures" / "estate-inventory.json"
    snapshot_path = snapshot_path or ROOT / "fixtures" / "compatibility-snapshot.json"
    with tempfile.TemporaryDirectory(prefix="vcfarch-0037-") as classes:
        compile_result = subprocess.run(
            ["javac", "-encoding", "UTF-8", "-d", classes,
             str(ROOT / "ArchitectureClient.java"), str(ROOT / "TestMain.java")],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        require(compile_result.returncode == 0,
                "Java compilation failed:\n" + compile_result.stderr)
        result = subprocess.run(
            ["java", "-cp", classes, "TestMain", str(inventory_path), str(snapshot_path)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        require(result.returncode == 0, "TestMain failed:\n" + result.stderr)
    try:
        artifact = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise VerificationError(f"TestMain stdout is not one JSON document: {exc}") from exc
    require(isinstance(artifact, dict), "artifact root must be an object")
    return artifact


def find_network(spec: dict[str, Any], network_type: str) -> dict[str, Any]:
    matches = [n for n in spec["networkSpecs"] if n.get("networkType") == network_type]
    require(len(matches) == 1, f"expected one {network_type} network")
    return matches[0]


def close(actual: Any, expected: float, label: str) -> None:
    require(isinstance(actual, (int, float)) and not isinstance(actual, bool),
            f"{label} must be numeric")
    require(math.isclose(float(actual), expected, rel_tol=0, abs_tol=0.02),
            f"{label}: expected {expected:.3f}, got {actual}")


def verify_fixture_derivation() -> None:
    """Check semantic derivation with valid, isolated variants of the supplied inputs."""
    inventory = json.loads(
        (ROOT / "fixtures" / "estate-inventory.json").read_text(encoding="utf-8"))
    snapshot = json.loads(
        (ROOT / "fixtures" / "compatibility-snapshot.json").read_text(encoding="utf-8"))

    inventory["estateId"] = "aus01-vcf-derived"
    inventory["components"][0]["version"] = "8.18.9"
    snapshot["storageProfiles"]["VSAN_OSA_HYBRID"]["rawTiBPerHost"] = 60.0
    snapshot["storageProfiles"]["VSAN_ESA"]["rawTiBPerHost"] = 120.0
    snapshot["targetBom"]["VCF Operations"]["version"] = "9.1.0.1"
    snapshot["targetBom"]["Cloud proxy"]["version"] = "9.1.0.1"
    snapshot["upgradeCompatibility"]["productTransitions"][0]["prerequisiteFacts"][2] = (
        "derived-product-interoperability-precheck-supported")

    with tempfile.TemporaryDirectory(prefix="vcfarch-0037-inputs-") as directory:
        fixture_dir = Path(directory)
        inventory_path = fixture_dir / "inventory.json"
        snapshot_path = fixture_dir / "snapshot.json"
        inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
        snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
        artifact = run_client(inventory_path, snapshot_path)

    decision = artifact.get("decision")
    migration = artifact.get("migrationPlan")
    require(isinstance(decision, dict) and isinstance(migration, dict),
            "client must derive decision and migrationPlan from its Path inputs")
    profiles = decision.get("profiles")
    require(isinstance(profiles, list), "derived decision.profiles must be an array")
    by_name = {item.get("name"): item for item in profiles if isinstance(item, dict)}
    policy = snapshot["capacityPolicy"]
    for name, raw_tib in (("VSAN_OSA_HYBRID", 60.0), ("VSAN_ESA", 120.0)):
        usable = raw_tib * policy["dataFraction"] * (1.0 - policy["reserveFraction"])
        close(by_name.get(name, {}).get("rawTiBPerHost"), raw_tib,
              f"derived {name}.rawTiBPerHost")
        close(by_name.get(name, {}).get("calculatedUsableTiBPerHost"), usable,
              f"derived {name}.calculatedUsableTiBPerHost")
        require(by_name.get(name, {}).get("requiredHosts") ==
                max(policy["minimumHosts"], math.ceil(240.0 / usable)),
                f"derived {name}.requiredHosts is wrong")

    require(migration.get("estateId") == "aus01-vcf-derived",
            "migrationPlan.estateId must be read from the inventory")
    first_step = migration.get("steps", [{}])[0]
    require(first_step.get("currentVersion") == "8.18.9",
            "migration currentVersion must be read from the inventory")
    require(first_step.get("targetVersion") == "9.1.0.1",
            "migration targetVersion must be derived from targetBom")
    require(first_step.get("gates", [])[-1:] == [
        "derived-product-interoperability-precheck-supported"
    ], "migration gates must be derived from prerequisiteFacts")


def main() -> int:
    # Produce and parse the artifact, then make SddcSpec validation the first artifact check.
    for protected_path, expected_hash in PROTECTED_SHA256.items():
        require(hashlib.sha256(protected_path.read_bytes()).hexdigest() == expected_hash,
                f"protected seed input has changed: {protected_path.name}")
    require(hashlib.sha256(OPENAPI.read_bytes()).hexdigest() == OPENAPI_SHA256,
            "pinned installer OpenAPI file has changed")
    openapi = json.loads(OPENAPI.read_text(encoding="utf-8"))
    require(openapi.get("info", {}).get("version") == "9.1.0.0",
            "installer OpenAPI is not version 9.1.0.0")
    artifact = run_client()
    require(set(artifact) == {"sddcSpec", "decision", "migrationPlan", "research"},
            "artifact root must contain exactly sddcSpec, decision, migrationPlan, and research")
    require(isinstance(artifact.get("sddcSpec"), dict), "artifact.sddcSpec must be an object")
    validate_json_schema(
        artifact["sddcSpec"],
        openapi["components"]["schemas"]["SddcSpec"],
        openapi,
        "$.sddcSpec",
    )
    print("PASS: SddcSpec validates against the pinned VCF Installer 9.1 schema")

    # All remaining structural and scenario checks intentionally happen after OpenAPI validation.
    require("decision" in artifact and "migrationPlan" in artifact,
            "artifact must contain decision and migrationPlan")
    decision = artifact["decision"]
    migration = artifact["migrationPlan"]
    require(isinstance(decision, dict), "decision must be an object")
    require(isinstance(migration, dict), "migrationPlan must be an object")

    migration_schema = json.loads((ROOT / "migration-plan-schema.json").read_text(encoding="utf-8"))
    validate_json_schema(migration, migration_schema, migration_schema, "$.migrationPlan")

    inventory = json.loads((ROOT / "fixtures" / "estate-inventory.json").read_text(encoding="utf-8"))
    snapshot = json.loads((ROOT / "fixtures" / "compatibility-snapshot.json").read_text(encoding="utf-8"))

    # Greenfield storage and network decision, derived from the pinned compatibility snapshot.
    policy = snapshot["capacityPolicy"]
    required_tib = 240.0
    expected_profiles: dict[str, dict[str, Any]] = {}
    for name, profile in snapshot["storageProfiles"].items():
        usable_per_host = (profile["rawTiBPerHost"] * policy["dataFraction"]
                           * (1.0 - policy["reserveFraction"]))
        required_hosts = max(policy["minimumHosts"], math.ceil(required_tib / usable_per_host))
        expected_profiles[name] = {
            "profile": profile,
            "usablePerHost": usable_per_host,
            "requiredHosts": required_hosts,
        }

    require(decision.get("selectedArchitecture") == "VSAN_ESA", "ESA must be selected")
    require(decision.get("siteTopology") == "SINGLE_SITE", "site topology must be SINGLE_SITE")
    close(decision.get("requiredUsableTiB"), 240.0, "requiredUsableTiB")
    close(decision.get("reservePercent"), 30.0, "reservePercent")
    require(decision.get("failuresToTolerate") == 2, "decision FTT must be 2")
    require(decision.get("raid") == "RAID_6_4_PLUS_2", "decision RAID must be 4+2")
    close(decision.get("interHostRttMs"), 0.4, "interHostRttMs")
    require(decision["interHostRttMs"] <= snapshot["siteAndNetworkLimits"]["singleSiteMaximumRttMs"],
            "single-site RTT exceeds the pinned limit")

    profiles = decision.get("profiles")
    require(isinstance(profiles, list) and len(profiles) == 2,
            "decision.profiles must contain the two candidates")
    by_name = {profile.get("name"): profile for profile in profiles if isinstance(profile, dict)}
    require(set(by_name) == set(expected_profiles), "decision.profiles names do not match snapshot")
    for name, expected in expected_profiles.items():
        actual = by_name[name]
        source = expected["profile"]
        require(actual.get("supported") is source["supported"], f"{name}: support mismatch")
        close(actual.get("rawTiBPerHost"), source["rawTiBPerHost"], f"{name}.rawTiBPerHost")
        close(actual.get("calculatedUsableTiBPerHost"), expected["usablePerHost"],
              f"{name}.calculatedUsableTiBPerHost")
        require(actual.get("requiredHosts") == expected["requiredHosts"],
                f"{name}: wrong required host count")
        network = source["network"]
        require(actual.get("minimumNetworkGbps") == network["minimumGbps"],
                f"{name}: wrong minimum network")
        require(actual.get("recommendedNetworkGbps") == network["recommendedGbps"],
                f"{name}: wrong recommended network")
        require(actual.get("dedicatedMinimum") is network["dedicatedMinimum"],
                f"{name}: wrong dedicated-network requirement")

    provisioned = decision.get("provisioned")
    require(isinstance(provisioned, dict), "decision.provisioned must be an object")
    require(provisioned.get("hostCount") == expected_profiles["VSAN_ESA"]["requiredHosts"] == 6,
            "provisioned ESA host count must be 6")
    close(provisioned.get("calculatedUsableTiB"),
          6 * expected_profiles["VSAN_ESA"]["usablePerHost"],
          "provisioned.calculatedUsableTiB")
    require(provisioned.get("physicalUplinksPerHost") == 2, "two uplinks are required")
    require(provisioned.get("uplinkGbps") == 25, "uplinks must be 25 GbE")
    require(provisioned.get("mtu") == 9000, "uplink MTU must be 9000")

    # Deployable SddcSpec scenario semantics (schema validity was already checked first).
    spec = artifact["sddcSpec"]
    require(spec.get("sddcId") == "dal01-m01", "wrong SDDC ID")
    require(spec.get("workflowType") == "VCF", "workflowType must be VCF")
    require(spec.get("version") == "9.1.0.0", "wrong SDDC version")
    require(spec.get("dnsSpec") == {
        "subdomain": "corp.example.com",
        "nameservers": ["10.40.0.10", "10.40.0.11"],
    }, "DNS design does not match the requirements")
    require(spec.get("ntpServers") == ["10.40.0.12", "10.40.0.13"], "wrong NTP servers")
    require(spec.get("vcenterSpec", {}).get("vcenterHostname") ==
            "dal01-vc01.corp.example.com", "wrong vCenter FQDN")
    require(spec.get("sddcManagerSpec", {}).get("hostname") == "dal01-sddc01",
            "wrong SDDC Manager hostname")
    expected_hosts = [f"dal01-esx{i:02d}" for i in range(1, 7)]
    require([host.get("hostname") for host in spec.get("hostSpecs", [])] == expected_hosts,
            "host inventory must be dal01-esx01 through dal01-esx06")
    vsan = spec.get("datastoreSpec", {}).get("vsanSpec", {})
    require(vsan.get("esaConfig", {}).get("enabled") is True, "SddcSpec must enable ESA")
    require(vsan.get("failuresToTolerate") == 2, "SddcSpec vSAN FTT must be 2")
    expected_networks = {
        "MANAGEMENT": (100, "10.40.0.0/24", "10.40.0.1", "10.40.0.20", "10.40.0.79"),
        "VSAN": (110, "10.40.10.0/24", "10.40.10.1", "10.40.10.20", "10.40.10.79"),
        "VMOTION": (120, "10.40.20.0/24", "10.40.20.1", "10.40.20.20", "10.40.20.79"),
    }
    for network_type, (vlan, subnet, gateway, start, end) in expected_networks.items():
        network = find_network(spec, network_type)
        require(network.get("vlanId") == vlan and network.get("subnet") == subnet
                and network.get("gateway") == gateway and network.get("mtu") == 9000,
                f"{network_type} network settings are wrong")
        require(network.get("includeIpAddressRanges") == [{"startIpAddress": start,
                                                            "endIpAddress": end}],
                f"{network_type} IP pool is wrong")
    require(len(spec.get("dvsSpecs", [])) == 1, "exactly one distributed switch is required")
    dvs = spec["dvsSpecs"][0]
    require(dvs.get("mtu") == 9000, "distributed switch MTU must be 9000")
    require(set(dvs.get("networks", [])) == set(expected_networks),
            "distributed switch must carry all three networks")
    require(dvs.get("vmnicsToUplinks") == [
        {"id": "vmnic0", "uplink": "uplink1"},
        {"id": "vmnic1", "uplink": "uplink2"},
    ], "distributed switch must map the redundant uplinks")
    nsx = spec.get("nsxtSpec", {})
    require(nsx.get("vipFqdn") == "dal01-nsx.corp.example.com", "wrong NSX VIP")
    require([manager.get("hostname") for manager in nsx.get("nsxtManagers", [])] == [
        "dal01-nsx01.corp.example.com",
        "dal01-nsx02.corp.example.com",
        "dal01-nsx03.corp.example.com",
    ], "wrong NSX manager topology")

    # Research provenance: real Broadcom HTTPS pages from every requested source family.
    research = artifact["research"]
    require(isinstance(research, list) and len(research) >= 3,
            "research must contain at least one page from each requested source family")
    allowed_hosts = {
        "compatibilityguide.broadcom.com",
        "interopmatrix.broadcom.com",
        "techdocs.broadcom.com",
    }
    research_hosts: set[str] = set()
    research_urls: set[str] = set()
    snapshot_date = date.fromisoformat(snapshot["snapshotDate"])
    for index, entry in enumerate(research):
        require(isinstance(entry, dict), f"research[{index}] must be an object")
        require(set(entry) == {"title", "url", "retrievedAtUtc", "fact"},
                f"research[{index}] has the wrong fields")
        for field in ("title", "url", "retrievedAtUtc", "fact"):
            require(isinstance(entry[field], str) and entry[field].strip(),
                    f"research[{index}].{field} must be a non-empty string")
        parsed = urlparse(entry["url"])
        require(parsed.scheme == "https" and parsed.hostname in allowed_hosts
                and parsed.username is None and parsed.password is None,
                f"research[{index}].url must be a Broadcom-published HTTPS page")
        require(entry["url"] not in research_urls, "research URLs must be unique")
        research_urls.add(entry["url"])
        research_hosts.add(parsed.hostname)
        try:
            retrieved = date.fromisoformat(entry["retrievedAtUtc"])
        except ValueError as exc:
            raise VerificationError(
                f"research[{index}].retrievedAtUtc must use YYYY-MM-DD") from exc
        require(entry["retrievedAtUtc"] == retrieved.isoformat(),
                f"research[{index}].retrievedAtUtc must use YYYY-MM-DD")
        require(retrieved >= snapshot_date,
                f"research[{index}] predates the supplied compatibility snapshot")
    require(research_hosts == allowed_hosts,
            "research must cover Compatibility Guide, Product Interoperability Matrix, and TechDocs")

    # Brownfield plan: exact fixture coverage, versions, targets, order, and pinned gates.
    require(migration["estateId"] == inventory["estateId"], "wrong migration estate ID")
    require(migration["sourcePlatformVersion"] == inventory["platformVersion"],
            "wrong migration source version")
    require(migration["targetPlatformVersion"] == snapshot["targetPlatformVersion"],
            "wrong migration target version")
    require(inventory["platformVersion"].startswith("5.2."),
            "fixture is not on the pinned 5.2.x source branch")
    inventory_by_id = {component["id"]: component for component in inventory["components"]}
    inventory_by_name = {component["name"]: component for component in inventory["components"]}
    compatibility = snapshot["upgradeCompatibility"]
    require(compatibility["sourceBranch"] == "5.2.x", "snapshot source branch mismatch")
    transitions = sorted(compatibility["productTransitions"], key=lambda item: item["sequence"])
    require(len(migration["steps"]) == len(inventory_by_id) == len(transitions),
            "migration must cover every inventory component once")
    require([step["order"] for step in migration["steps"]] == list(range(1, len(transitions) + 1)),
            "migration order values must be contiguous")
    require(len({step["componentId"] for step in migration["steps"]}) == len(inventory_by_id),
            "migration repeats a component")
    for actual, transition in zip(migration["steps"], transitions):
        current = inventory_by_id[actual["componentId"]]
        require(current is inventory_by_name[transition["sourceProduct"]],
                "migration component sequence differs from the pinned upgrade order")
        require(re.search(transition["sourceVersionPattern"], current["version"]) is not None,
                f"{actual['componentId']}: source version is outside the compatible range")
        require(actual["component"] == current["name"], "migration component name mismatch")
        require(actual["currentVersion"] == current["version"], "migration current version mismatch")
        target_versions = {
            snapshot["targetBom"][member]["version"]
            for member in transition["targetBomMembers"]
        }
        require(len(target_versions) == 1, "transition BOM members do not share one version")
        require(actual["targetComponent"] == transition["targetProduct"],
                f"{actual['componentId']}: wrong target product")
        require(actual["targetVersion"] == next(iter(target_versions)),
                f"{actual['componentId']}: wrong target version")
        expected_action = {"in-place-upgrade": "UPGRADE"}[transition["transitionMode"]]
        require(actual["action"] == expected_action,
                f"{actual['componentId']}: wrong migration action")
        require(actual["gates"] == transition["prerequisiteFacts"],
                f"{actual['componentId']}: wrong prerequisite gates")

    verify_fixture_derivation()
    print("PASS: architecture decision, installer inputs, migration plan, and input derivation are correct")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (VerificationError, KeyError, TypeError, IndexError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
