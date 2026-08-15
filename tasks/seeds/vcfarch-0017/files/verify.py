#!/usr/bin/env python3
"""Deterministic verifier for vcfarch-0017.

The verifier is intentionally offline. It validates the submitted greenfield
artifact against the pinned installer SddcSpec before performing seed-specific
checks, including the structure of the live-research record.
"""

from __future__ import annotations

import ast
import hashlib
import ipaddress
import json
import math
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import date
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urlsplit


PROTECTED_HASHES = {
    "vcf-installer-openapi.json": "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d",
    "scenario.json": "805d3ec595d51c56a742ff66d772109c26dc572e761e22b1c8f2347c0a21cd10",
    "estate-inventory.json": "93d23f64013b83450823b80604fc7402adc5f29c83ebbbba10e869666f7392f7",
    "compatibility-snapshot.json": "2da67e03aaaf2c61b41db7ce880f1ea45b516e15d79023138aa0204802d61b7b",
    "migration-plan.schema.json": "00bc01c65c894249f9e05d61f67cd58e0462d7cea9608bfd8574b1cd0eed5875",
    "architecture-extension.schema.json": "2f8f648ce47535dcdc7da4689b284487f49fc3136bb4e28940329dd5741a11b4",
    "LICENSE-vcf-api-specs.txt": "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
}


class VerificationError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VerificationError(f"missing required artifact: {path}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(f"invalid JSON in {path}: {exc}") from exc


def pointer(document: Any, ref: str) -> Any:
    if not ref.startswith("#/"):
        raise VerificationError(f"only local schema references are supported: {ref}")
    value = document
    for token in ref[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        value = value[token]
    return value


def type_matches(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True


def schema_errors(
    value: Any,
    schema: Any,
    document: Any,
    path: str = "$",
) -> list[str]:
    """Validate the JSON Schema/OpenAPI keywords used by the pinned documents."""
    if isinstance(schema, bool):
        return [] if schema else [f"{path}: rejected by false schema"]
    if not isinstance(schema, dict):
        return []
    if "$ref" in schema:
        return schema_errors(value, pointer(document, schema["$ref"]), document, path)

    errors: list[str] = []
    if value is None and schema.get("nullable"):
        return errors

    for keyword in ("allOf", "anyOf", "oneOf"):
        if keyword not in schema:
            continue
        results = [schema_errors(value, child, document, path) for child in schema[keyword]]
        valid_count = sum(not result for result in results)
        if keyword == "allOf":
            for result in results:
                errors.extend(result)
        elif keyword == "anyOf" and valid_count == 0:
            errors.append(f"{path}: does not satisfy anyOf")
        elif keyword == "oneOf" and valid_count != 1:
            errors.append(f"{path}: satisfies {valid_count} oneOf branches")
    if "not" in schema and not schema_errors(value, schema["not"], document, path):
        errors.append(f"{path}: matches forbidden schema")

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not in the allowed enum")

    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        matches = any(type_matches(value, item) for item in expected_type)
    elif isinstance(expected_type, str):
        matches = type_matches(value, expected_type)
    else:
        matches = True
    if not matches:
        return errors + [f"{path}: expected {expected_type}, got {type(value).__name__}"]

    if isinstance(value, dict):
        required = schema.get("required", [])
        for name in required:
            if name not in value:
                errors.append(f"{path}: missing required property {name!r}")
        properties = schema.get("properties", {})
        for name, child_value in value.items():
            child_path = f"{path}.{name}"
            if name in properties:
                errors.extend(schema_errors(child_value, properties[name], document, child_path))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: additional property {name!r} is not allowed")
            elif isinstance(schema.get("additionalProperties"), dict):
                errors.extend(
                    schema_errors(child_value, schema["additionalProperties"], document, child_path)
                )
        if "minProperties" in schema and len(value) < schema["minProperties"]:
            errors.append(f"{path}: too few properties")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            errors.append(f"{path}: too many properties")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: too many items")
        if schema.get("uniqueItems"):
            serialized = [json.dumps(item, sort_keys=True) for item in value]
            if len(set(serialized)) != len(serialized):
                errors.append(f"{path}: duplicate array items")
        if isinstance(schema.get("items"), dict):
            for index, child_value in enumerate(value):
                errors.extend(
                    schema_errors(child_value, schema["items"], document, f"{path}[{index}]")
                )

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: string is shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: string is longer than {schema['maxLength']}")
        if "pattern" in schema:
            try:
                if re.search(schema["pattern"], value) is None:
                    errors.append(f"{path}: does not match {schema['pattern']!r}")
            except re.error as exc:
                raise VerificationError(f"invalid protected schema pattern: {exc}") from exc

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above maximum {schema['maximum']}")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: not above exclusive minimum")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            errors.append(f"{path}: not below exclusive maximum")
    return errors


def verify_protected_files(protected: Path) -> None:
    for filename, expected in PROTECTED_HASHES.items():
        path = protected / filename
        require(path.is_file(), f"protected fixture missing: {filename}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        require(digest == expected, f"protected fixture was modified: {filename}")


def ceiling_decimal(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def calculate_host_counts(scenario: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, int]:
    greenfield = scenario["greenfield"]
    capacity = greenfield["capacity"]
    compute = greenfield["hostCompute"]
    rules = snapshot["greenfieldRules"]["storageArchitectureRules"]
    required_storage = Decimal(capacity["usableStorageTiBAfterOneHostFailure"])
    required_memory = Decimal(capacity["memoryTiBAfterOneHostFailure"])
    required_cores = int(capacity["physicalCoresAfterOneHostFailure"])
    ftt = str(capacity["failuresToTolerate"])
    result: dict[str, int] = {}
    for candidate in greenfield["storageCandidates"]:
        architecture = candidate["architecture"]
        rule = rules[architecture]
        require(candidate["media"] == rule["requiredMedia"], f"fixture media mismatch for {architecture}")
        raw = Decimal(candidate["rawCapacityTiBPerHost"])
        factor = Decimal(rule["postFailureUsableCapacityFactor"])
        storage_hosts = ceiling_decimal(required_storage / (raw * factor)) + 1
        core_hosts = math.ceil(required_cores / int(compute["physicalCoresPerHost"])) + 1
        memory_hosts = ceiling_decimal(required_memory / Decimal(compute["memoryTiBPerHost"])) + 1
        ftt_hosts = int(rule["minimumHostsByFtt"][ftt]) + 1
        result[architecture] = max(storage_hosts, core_hosts, memory_hosts, ftt_hosts)
    return result


def verify_greenfield_semantics(
    artifact: dict[str, Any],
    scenario: dict[str, Any],
    snapshot: dict[str, Any],
) -> None:
    greenfield = scenario["greenfield"]
    naming = greenfield["naming"]
    site = greenfield["site"]
    capacity = greenfield["capacity"]
    architecture = artifact["architecture"]
    decision = architecture["storageDecision"]
    counts = calculate_host_counts(scenario, snapshot)
    maximum_hosts = site["maximumClusterHosts"]
    fitting = [name for name, count in counts.items() if count <= maximum_hosts]
    require(fitting == ["ESA"], f"protected scenario should have ESA as its sole fitting option, got {fitting}")
    require(decision["selectedArchitecture"] == fitting[0], "storage selection does not fit the site constraint")
    require(decision["requiredHosts"] == counts[fitting[0]], "selected requiredHosts is miscalculated")
    require(decision["failuresToTolerate"] == capacity["failuresToTolerate"], "incorrect FTT")

    alternatives = decision["alternatives"]
    require(len({item["architecture"] for item in alternatives}) == len(alternatives), "duplicate alternatives")
    alternative_map = {item["architecture"]: item for item in alternatives}
    require(set(alternative_map) == set(counts), "both OSA and ESA alternatives must be recorded")
    for name, expected_count in counts.items():
        require(alternative_map[name]["requiredHosts"] == expected_count, f"wrong {name} host calculation")
        minimum_network = snapshot["greenfieldRules"]["storageArchitectureRules"][name]["minimumUplinkGbps"]
        require(alternative_map[name]["minimumSurvivingNetworkGbps"] == minimum_network, f"wrong {name} network requirement")
        require(alternative_map[name]["fitsSite"] == (expected_count <= maximum_hosts), f"wrong {name} fit result")

    host_count = decision["requiredHosts"]
    expected_hosts = [f"{naming['hostPrefix']}{index:02d}" for index in range(1, host_count + 1)]
    actual_hosts = [item["hostname"] for item in artifact.get("hostSpecs", [])]
    require(actual_hosts == expected_hosts, "hostSpecs do not contain the required named host set")

    assignments = architecture["rackAssignments"]
    require(len(assignments) == host_count, "every host needs one rack assignment")
    require([item["hostname"] for item in assignments] == expected_hosts, "rack assignments must cover hosts once in host order")
    allowed_racks = set(site["rackFailureDomains"])
    require({item["rack"] for item in assignments} == allowed_racks, "all three rack failure domains must be used")
    rack_counts = Counter(item["rack"] for item in assignments)
    require(max(rack_counts.values()) - min(rack_counts.values()) <= 1, "hosts are not evenly spread across racks")

    require(architecture["site"] == {
        "selectedSite": site["selectedSite"],
        "recoverySite": site["recoverySite"],
        "stretchedCluster": False,
    }, "site architecture is incorrect or stretched")

    selected_candidate = next(
        item for item in greenfield["storageCandidates"]
        if item["architecture"] == decision["selectedArchitecture"]
    )
    storage_rule = snapshot["greenfieldRules"]["storageArchitectureRules"][decision["selectedArchitecture"]]
    surviving_hosts = host_count - 1
    expected_storage = (
        Decimal(selected_candidate["rawCapacityTiBPerHost"])
        * Decimal(storage_rule["postFailureUsableCapacityFactor"])
        * surviving_hosts
    )
    expected_memory = Decimal(greenfield["hostCompute"]["memoryTiBPerHost"]) * surviving_hosts
    expected_cores = int(greenfield["hostCompute"]["physicalCoresPerHost"]) * surviving_hosts
    actual_capacity = architecture["capacityAfterOneHostFailure"]
    require(Decimal(actual_capacity["usableStorageTiB"]) == expected_storage, "post-failure storage capacity is incorrect")
    require(Decimal(actual_capacity["memoryTiB"]) == expected_memory, "post-failure memory capacity is incorrect")
    require(actual_capacity["physicalCores"] == expected_cores, "post-failure core capacity is incorrect")

    require(artifact["sddcId"] == naming["sddcId"], "incorrect sddcId")
    require(artifact.get("workflowType") == "VCF", "workflowType must be VCF")
    require(artifact.get("version") == snapshot["targetRelease"], "incorrect SddcSpec target version")
    require(artifact.get("vcfInstanceName") == naming["vcfInstanceName"], "incorrect VCF instance name")
    require(artifact["dnsSpec"] == {
        "subdomain": naming["dnsSubdomain"],
        "nameservers": naming["dnsServers"],
    }, "DNS design does not match the scenario")
    require(artifact.get("ntpServers") == naming["ntpServers"], "NTP design does not match the scenario")
    require(artifact["vcenterSpec"]["vcenterHostname"] == naming["vcenterHostname"], "incorrect vCenter hostname")
    require(artifact["vcenterSpec"].get("version") == "9.1.0.0", "incorrect vCenter version")
    require(artifact["clusterSpec"] == {
        "datacenterName": naming["datacenterName"],
        "clusterName": naming["clusterName"],
    }, "incorrect datacenter or cluster naming")
    require(artifact["sddcManagerSpec"]["hostname"] == naming["sddcManagerHostname"], "incorrect SDDC Manager hostname")
    require(artifact["licenseServerSpec"]["hostname"] == naming["licenseServerHostname"], "incorrect license server hostname")

    nsxt = artifact["nsxtSpec"]
    require([item.get("hostname") for item in nsxt["nsxtManagers"]] == naming["nsxManagerHostnames"], "incorrect NSX manager nodes")
    require(nsxt["vipFqdn"] == naming["nsxVipFqdn"], "incorrect NSX VIP")
    require(nsxt.get("version") == "9.1.0.0", "incorrect NSX version")

    vsan = artifact["datastoreSpec"]["vsanSpec"]
    require(vsan.get("datastoreName") == naming["datastoreName"], "incorrect vSAN datastore name")
    require(vsan.get("failuresToTolerate") == capacity["failuresToTolerate"], "vSAN FTT is incorrect")
    esa_enabled = vsan.get("esaConfig", {}).get("enabled")
    require(esa_enabled is (decision["selectedArchitecture"] == "ESA"), "SddcSpec ESA flag disagrees with the selection")

    combination = architecture["componentVersions"] | {
        "release": artifact["version"],
        "storageArchitecture": decision["selectedArchitecture"],
    }
    require(combination in snapshot["greenfieldRules"]["supportedCombinations"], "unsupported frozen component combination")

    segment_by_type = {item["networkType"]: item for item in artifact["networkSpecs"]}
    required_networks = snapshot["greenfieldRules"]["requiredSddcNetworks"]
    require(len(segment_by_type) == len(artifact["networkSpecs"]), "duplicate network types")
    require(set(segment_by_type) == set(required_networks), "required SDDC networks are missing or extra")
    for expected in greenfield["network"]["segments"]:
        actual = segment_by_type[expected["networkType"]]
        for field in ("vlanId", "subnet", "gateway", "mtu"):
            require(actual.get(field) == expected[field], f"{expected['networkType']} has incorrect {field}")
        require(actual.get("includeIpAddressRanges") == [{
            "startIpAddress": expected["startIpAddress"],
            "endIpAddress": expected["endIpAddress"],
        }], f"{expected['networkType']} has an incorrect IP range")
        if expected["networkType"] in {"VSAN", "VMOTION"}:
            require(actual.get("teamingPolicy") == "loadbalance_loadbased", f"{expected['networkType']} needs load-based teaming")

    fleet_expected = next(item for item in greenfield["network"]["segments"] if item["networkType"] == "FLEET_MANAGEMENT")
    fleet_range = segment_by_type["FLEET_MANAGEMENT"]["includeIpAddressRanges"][0]
    fleet_count = int(ipaddress.ip_address(fleet_range["endIpAddress"])) - int(ipaddress.ip_address(fleet_range["startIpAddress"])) + 1
    require(fleet_count == fleet_expected["reservedAddresses"], "management-services address reservation is incorrect")

    require(len(artifact["dvsSpecs"]) == 1, "the scenario requires one management DVS")
    dvs = artifact["dvsSpecs"][0]
    require(dvs.get("dvsName") == naming["dvsName"], "incorrect DVS name")
    require(dvs.get("mtu") == 9000, "DVS must use MTU 9000")
    require(set(dvs.get("networks", [])) == set(required_networks), "DVS must carry every required network")
    expected_mappings = {(item["id"], f"uplink{index}") for index, item in enumerate(greenfield["network"]["availableUplinks"], 1)}
    actual_mappings = {(item["id"], item["uplink"]) for item in dvs["vmnicsToUplinks"]}
    require(actual_mappings == expected_mappings, "DVS vmnic-to-uplink mapping is incorrect")
    expected_uplinks = [
        {"id": item["id"], "speedGbps": item["speedGbps"], "dvsUplink": f"uplink{index}"}
        for index, item in enumerate(greenfield["network"]["availableUplinks"], 1)
    ]
    require(architecture["uplinks"] == expected_uplinks, "architecture uplink inventory is incorrect")
    require(len(expected_uplinks) >= storage_rule["minimumUplinks"], "not enough uplinks for selected storage")
    require(all(item["speedGbps"] >= storage_rule["minimumUplinkGbps"] for item in expected_uplinks), "uplinks are too slow for selected storage")
    require(greenfield["network"]["uplinkFailureTolerance"] == 1, "protected network failure requirement changed")


def verify_migration(
    plan: dict[str, Any],
    inventory: dict[str, Any],
    snapshot: dict[str, Any],
) -> None:
    rules = snapshot["estateRules"]
    require(plan["estateId"] == inventory["estateId"], "migration estateId mismatch")
    require(plan["sourceRelease"] == inventory["sourceRelease"] == rules["sourceRelease"], "migration source release mismatch")
    require(plan["targetRelease"] == snapshot["targetRelease"], "migration target release mismatch")

    expected_order = rules["orderedComponents"]
    steps = plan["steps"]
    require([step["order"] for step in steps] == list(range(1, len(expected_order) + 1)), "migration step order must be contiguous")
    require([step["componentId"] for step in steps] == expected_order, "migration component order is incompatible")
    inventory_by_id = {item["componentId"]: item for item in inventory["components"]}
    require(set(inventory_by_id) == set(expected_order), "protected inventory does not match snapshot")

    gate_definitions = plan["gates"]
    gate_by_id = {gate["gateId"]: gate for gate in gate_definitions}
    require(len(gate_by_id) == len(gate_definitions), "migration gate IDs must be unique")
    require(set(gate_by_id) == set(rules["gateDefinitions"]), "gate definitions must exactly cover the pinned gates")
    for gate_id, expected in rules["gateDefinitions"].items():
        actual = gate_by_id[gate_id]
        require(actual["kind"] == expected["kind"], f"incorrect gate kind for {gate_id}")
        require(actual["satisfiedByStep"] == expected["satisfiedByStep"], f"incorrect dependency for {gate_id}")
        require(actual["description"] == expected["description"], f"incorrect gate description for {gate_id}")

    used_gates: set[str] = set()
    for step in steps:
        component_id = step["componentId"]
        current = inventory_by_id[component_id]
        require(step["componentName"] == current["componentName"], f"incorrect component name for {component_id}")
        require(step["fromVersion"] == current["version"], f"incorrect source version for {component_id}")
        require(step["targetVersion"] == rules["targetVersions"][component_id], f"incorrect target for {component_id}")
        require(step["action"] == rules["actions"][component_id], f"incorrect action for {component_id}")
        required_gates = rules["requiredGateIds"][component_id]
        require(step["gatedBy"] == required_gates, f"incorrect or misordered gates for {component_id}")
        require(len(set(step["gatedBy"])) == len(step["gatedBy"]), f"duplicate gates on {component_id}")
        for gate_id in step["gatedBy"]:
            require(gate_id in gate_by_id, f"undefined gate {gate_id}")
            satisfied_by = gate_by_id[gate_id]["satisfiedByStep"]
            if satisfied_by is not None:
                require(satisfied_by < step["order"], f"gate {gate_id} is not satisfied by an earlier step")
            used_gates.add(gate_id)
    require(used_gates == set(gate_by_id), "every defined gate must gate a migration step")


def verify_research_sources(root: Path) -> None:
    sources = load_json(root / "artifacts" / "research-sources.json")
    require(isinstance(sources, list), "research-sources.json must contain an array")
    require(len(sources) >= 3, "research must record at least one source for each requested topic")
    expected_fields = ("title", "url", "accessedAt", "informed")
    expected_keys = set(expected_fields)
    seen_urls: set[str] = set()
    for index, source in enumerate(sources):
        require(isinstance(source, dict), f"research source {index + 1} must be an object")
        require(set(source) == expected_keys, f"research source {index + 1} has missing or extra keys")
        for field in expected_fields:
            require(
                isinstance(source[field], str) and source[field].strip() == source[field] and bool(source[field]),
                f"research source {index + 1} has an invalid {field}",
            )
        parsed = urlsplit(source["url"])
        hostname = (parsed.hostname or "").lower()
        require(parsed.scheme == "https", f"research source {index + 1} must use HTTPS")
        require(
            hostname == "broadcom.com" or hostname.endswith(".broadcom.com"),
            f"research source {index + 1} is not a published Broadcom source",
        )
        require(parsed.path not in {"", "/"}, f"research source {index + 1} must identify a specific source")
        require(source["url"] not in seen_urls, "research source URLs must be distinct")
        seen_urls.add(source["url"])
        require(
            re.fullmatch(r"\d{4}-\d{2}-\d{2}", source["accessedAt"]) is not None,
            f"research source {index + 1} accessedAt must use YYYY-MM-DD",
        )
        try:
            date.fromisoformat(source["accessedAt"])
        except ValueError as exc:
            raise VerificationError(
                f"research source {index + 1} accessedAt must be an ISO YYYY-MM-DD date"
            ) from exc


def verify_regeneration(root: Path) -> None:
    expected = {
        filename: (root / "artifacts" / filename).read_bytes()
        for filename in ("greenfield-sddc.json", "migration-plan.json")
    }
    with TemporaryDirectory(prefix="vcfarch-verify-") as temporary:
        sandbox = Path(temporary)
        shutil.copytree(root / "vcf_arch", sandbox / "vcf_arch")
        inputs = sandbox / "files"
        inputs.mkdir()
        for filename in (
            "scenario.json",
            "estate-inventory.json",
            "compatibility-snapshot.json",
        ):
            shutil.copy2(root / "files" / filename, inputs / filename)

        first: dict[str, bytes] | None = None
        for run_number in (1, 2):
            try:
                completed = subprocess.run(
                    [sys.executable, "-B", "-m", "vcf_arch"],
                    cwd=sandbox,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=10,
                    check=False,
                    text=True,
                )
            except subprocess.TimeoutExpired as exc:
                raise VerificationError("python -m vcf_arch timed out") from exc
            require(
                completed.returncode == 0,
                "python -m vcf_arch failed: " + completed.stdout[-1000:],
            )
            generated: dict[str, bytes] = {}
            for filename in expected:
                path = sandbox / "artifacts" / filename
                require(path.is_file(), f"python -m vcf_arch did not produce {filename}")
                generated[filename] = path.read_bytes()
            require(generated == expected, "python -m vcf_arch does not reproduce the submitted artifacts")
            if run_number == 1:
                first = generated
            else:
                require(generated == first, "python -m vcf_arch is nondeterministic")


def verify_stdlib_package(root: Path) -> None:
    package = root / "vcf_arch"
    require((package / "__init__.py").is_file(), "vcf_arch/__init__.py is missing")
    require((package / "__main__.py").is_file(), "vcf_arch/__main__.py is missing")
    python_files = sorted(package.rglob("*.py"))
    require(python_files, "vcf_arch contains no Python source")
    stdlib = set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}
    for path in python_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            raise VerificationError(f"invalid Python in {path}: {exc}") from exc
        for node in ast.walk(tree):
            module: str | None = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".", 1)[0]
                    require(top in stdlib or top == "vcf_arch", f"non-stdlib import {alias.name!r} in {path}")
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                module = node.module or ""
                top = module.split(".", 1)[0]
                require(top in stdlib or top == "vcf_arch", f"non-stdlib import {module!r} in {path}")


def main() -> int:
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd().resolve()
    protected = Path(__file__).resolve().parent
    try:
        # Required first artifact check: validate the submission against the
        # upstream installer document's own SddcSpec before seed semantics.
        installer = load_json(protected / "vcf-installer-openapi.json")
        greenfield = load_json(root / "artifacts" / "greenfield-sddc.json")
        sddc_schema = installer["components"]["schemas"]["SddcSpec"]
        errors = schema_errors(greenfield, sddc_schema, installer)
        require(not errors, "installer SddcSpec validation failed:\n  " + "\n  ".join(errors[:30]))
        print("PASS installer SddcSpec validation")

        verify_protected_files(protected)
        require(installer.get("info", {}).get("version") == "9.1.0.0", "wrong protected installer version")
        scenario = load_json(protected / "scenario.json")
        inventory = load_json(protected / "estate-inventory.json")
        snapshot = load_json(protected / "compatibility-snapshot.json")

        extension_schema = load_json(protected / "architecture-extension.schema.json")
        extension_errors = schema_errors(greenfield.get("architecture"), extension_schema, extension_schema)
        require(not extension_errors, "architecture extension validation failed:\n  " + "\n  ".join(extension_errors[:30]))
        verify_greenfield_semantics(greenfield, scenario, snapshot)
        print("PASS greenfield capacity, storage, site, and network architecture")

        migration = load_json(root / "artifacts" / "migration-plan.json")
        migration_schema = load_json(protected / "migration-plan.schema.json")
        migration_errors = schema_errors(migration, migration_schema, migration_schema)
        require(not migration_errors, "migration plan schema validation failed:\n  " + "\n  ".join(migration_errors[:30]))
        verify_migration(migration, inventory, snapshot)
        print("PASS ordered estate migration plan")

        verify_research_sources(root)
        print("PASS live-research source record structure")

        verify_stdlib_package(root)
        print("PASS standard-library-only vcf_arch package")
        verify_regeneration(root)
        print("PASS deterministic python -m vcf_arch regeneration")
        print("VERIFIED vcfarch-0017")
        return 0
    except (VerificationError, KeyError, TypeError, ValueError, IndexError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
