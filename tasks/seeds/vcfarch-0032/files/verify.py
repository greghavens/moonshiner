#!/usr/bin/env python3
"""Deterministic verifier for the VCF architecture seed.

Live research is intentionally outside this verifier. Compatibility decisions are
checked only against the fixture and the dated snapshot bundled with the seed.
"""

from __future__ import annotations

import ipaddress
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
OPENAPI = ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
GREENFIELD_FIXTURE = ROOT / "fixtures" / "greenfield-requirements.json"
ESTATE_FIXTURE = ROOT / "fixtures" / "existing-estate.json"
SNAPSHOT_FILE = ROOT / "compatibility" / "compatibility-snapshot.json"
MIGRATION_SCHEMA = ROOT / "schemas" / "migration-plan.schema.json"
RESEARCH_FILE = ROOT / "research-sources.md"
PROTECTED_SHA256 = {
    "TestMain.java": "0b0b4be0f2bee83431d1394ecb3c028a2db851d6ff9a80a78a70bcc718e9de58",
    "fixtures/greenfield-requirements.json": "ee1cf89aa2be546883d5d5d8c4dff360cfc9fe48cbe690e6c76f287b38bc1c17",
    "fixtures/existing-estate.json": "7a1392c927352c9481194d392149c02a98afbca44ac9ac9032e0b55e3f1835e6",
    "compatibility/compatibility-snapshot.json": "3373984a4823525058b716bedbac6ad53da4a5727a7cab06a128a927aba232b6",
    "schemas/migration-plan.schema.json": "7a7fe175f0341f881e8842af783a8825adcf2f281b36a624d5116f3bdf61e33b",
    "specifications/vcf-installer/vcf-installer-openapi.json": "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d",
}


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def check_protected_inputs() -> None:
    for relative_path, expected in PROTECTED_SHA256.items():
        candidate = ROOT / relative_path
        actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
        require(actual == expected, f"protected seed input changed: {relative_path}")


def check_research_sources() -> None:
    text = RESEARCH_FILE.read_text(encoding="utf-8")
    require("Replace this placeholder" not in text, "research-sources.md is still the seed placeholder")
    entry_pattern = re.compile(
        r"^- \[(?P<title>[^\]\r\n]+)\]\((?P<url>https://[^\s)]+)\)\s+[—-]\s+(?P<note>\S.*)$",
        re.MULTILINE,
    )
    entries = list(entry_pattern.finditer(text))
    require(len(entries) >= 2, "research-sources.md must contain titled HTTPS Broadcom sources with notes")
    urls: list[str] = []
    for entry in entries:
        title = entry.group("title").strip()
        url = entry.group("url")
        note = entry.group("note").strip()
        host_match = re.match(r"https://([^/:?#]+)", url, re.IGNORECASE)
        require(host_match is not None, f"research source is not an HTTPS URL: {url}")
        hostname = host_match.group(1).lower().rstrip(".")
        require(hostname == "broadcom.com" or hostname.endswith(".broadcom.com"), f"research source is not Broadcom-published: {url}")
        require(len(title) >= 8, f"research source title is too short: {title!r}")
        require(len(note) >= 20, f"research source note is too short for {title!r}")
        urls.append(url)
    require(len(urls) == len(set(urls)), "research source URLs must be unique")


def json_pointer(document: Any, pointer: str) -> Any:
    require(pointer.startswith("#/"), f"unsupported non-local schema reference: {pointer}")
    current = document
    for raw_part in pointer[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        require(isinstance(current, dict) and part in current, f"unresolved schema reference: {pointer}")
        current = current[part]
    return current


def validate_schema(instance: Any, schema: dict[str, Any], root: dict[str, Any], path: str = "$") -> None:
    """Validate the JSON-Schema/OpenAPI keywords used by the bundled schemas."""
    if "$ref" in schema:
        validate_schema(instance, json_pointer(root, schema["$ref"]), root, path)
        return

    if instance is None and schema.get("nullable") is True:
        return

    for subschema in schema.get("allOf", []):
        validate_schema(instance, subschema, root, path)
    if "anyOf" in schema:
        require(
            any(schema_matches(instance, candidate, root, path) for candidate in schema["anyOf"]),
            f"{path}: did not match any allowed schema",
        )
    if "oneOf" in schema:
        matches = sum(schema_matches(instance, candidate, root, path) for candidate in schema["oneOf"])
        require(matches == 1, f"{path}: expected exactly one matching schema, found {matches}")
    if "not" in schema:
        require(not schema_matches(instance, schema["not"], root, path), f"{path}: matched a forbidden schema")

    expected_type = schema.get("type")
    if expected_type is not None:
        accepted = expected_type if isinstance(expected_type, list) else [expected_type]
        require(any(is_json_type(instance, item) for item in accepted), f"{path}: expected {accepted}, got {type(instance).__name__}")

    if "enum" in schema:
        require(instance in schema["enum"], f"{path}: {instance!r} is not in {schema['enum']!r}")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for key in required:
            require(key in instance, f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        for key, value in instance.items():
            child_path = f"{path}.{key}"
            if key in properties:
                validate_schema(value, properties[key], root, child_path)
            elif schema.get("additionalProperties") is False:
                raise VerificationError(f"{child_path}: additional property is not allowed")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_schema(value, schema["additionalProperties"], root, child_path)
        if "minProperties" in schema:
            require(len(instance) >= schema["minProperties"], f"{path}: too few properties")
        if "maxProperties" in schema:
            require(len(instance) <= schema["maxProperties"], f"{path}: too many properties")

    if isinstance(instance, list):
        if "minItems" in schema:
            require(len(instance) >= schema["minItems"], f"{path}: too few items")
        if "maxItems" in schema:
            require(len(instance) <= schema["maxItems"], f"{path}: too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            require(len(encoded) == len(set(encoded)), f"{path}: items are not unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, value in enumerate(instance):
                validate_schema(value, item_schema, root, f"{path}[{index}]")

    if isinstance(instance, str):
        if "minLength" in schema:
            require(len(instance) >= schema["minLength"], f"{path}: string is too short")
        if "maxLength" in schema:
            require(len(instance) <= schema["maxLength"], f"{path}: string is too long")
        if "pattern" in schema:
            require(re.search(schema["pattern"], instance) is not None, f"{path}: does not match {schema['pattern']!r}")

    if is_number(instance):
        if "minimum" in schema:
            require(instance >= schema["minimum"], f"{path}: below minimum")
        if "maximum" in schema:
            require(instance <= schema["maximum"], f"{path}: above maximum")
        exclusive_minimum = schema.get("exclusiveMinimum")
        if isinstance(exclusive_minimum, (int, float)) and not isinstance(exclusive_minimum, bool):
            require(instance > exclusive_minimum, f"{path}: not above exclusive minimum")
        exclusive_maximum = schema.get("exclusiveMaximum")
        if isinstance(exclusive_maximum, (int, float)) and not isinstance(exclusive_maximum, bool):
            require(instance < exclusive_maximum, f"{path}: not below exclusive maximum")


def schema_matches(instance: Any, schema: dict[str, Any], root: dict[str, Any], path: str) -> bool:
    try:
        validate_schema(instance, schema, root, path)
        return True
    except VerificationError:
        return False


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_json_type(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": is_number(value),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, False)


def compile_client(output_dir: Path) -> None:
    command = [
        "javac",
        "-encoding",
        "UTF-8",
        "-d",
        str(output_dir),
        str(ROOT / "ArchitectureClient.java"),
        str(ROOT / "TestMain.java"),
    ]
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=30)
    require(result.returncode == 0, f"Java compilation failed:\n{result.stdout}{result.stderr}")


def run_client(classes: Path, fixture: Path) -> Any:
    command = ["java", "-cp", str(classes), "TestMain", str(fixture), str(SNAPSHOT_FILE)]
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=30)
    require(result.returncode == 0, f"client failed for {fixture.name}:\n{result.stdout}{result.stderr}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise VerificationError(f"client output for {fixture.name} is not one JSON value: {error}") from error


def exact_network(actual: dict[str, Any], expected: dict[str, Any]) -> None:
    for key in ("networkType", "vlanId", "subnet", "gateway", "subnetMask", "mtu"):
        require(actual.get(key) == expected[key], f"network {expected['networkType']} has wrong {key}")
    ranges = actual.get("includeIpAddressRanges")
    require(isinstance(ranges, list) and len(ranges) == 1, f"network {expected['networkType']} must have one IP range")
    require(ranges[0].get("startIpAddress") == expected["startIpAddress"], f"network {expected['networkType']} has wrong range start")
    require(ranges[0].get("endIpAddress") == expected["endIpAddress"], f"network {expected['networkType']} has wrong range end")


def check_greenfield(spec: dict[str, Any], fixture: dict[str, Any], snapshot: dict[str, Any]) -> None:
    green = snapshot["greenfield"]
    release = snapshot["release"]
    minimum_hosts = green["minimumHostCount"]
    availability = fixture["availability"]
    capacity = fixture["capacity"]

    require(fixture["site"]["count"] == green["siteCount"] == 1, "fixture/snapshot must describe one site")
    require(fixture["site"]["deploymentModel"] == green["deploymentModel"] == "CONSOLIDATED", "deployment must be consolidated")
    require(capacity["hostsAvailable"] == minimum_hosts, "scenario must hold at the minimum supported host count")
    surviving = minimum_hosts - availability["hostFailuresToTolerate"]
    require(surviving == availability["requiredHostsRemaining"], "host survival target is inconsistent")
    required = capacity["requiredAfterOneHostFailure"]
    per_host = capacity["perHost"]
    require(surviving * per_host["physicalCores"] >= required["physicalCores"], "N+1 CPU capacity is insufficient")
    require(surviving * per_host["memoryGiB"] >= required["memoryGiB"], "N+1 memory capacity is insufficient")
    mirrored_usable = surviving * per_host["rawNvmeTiB"] / 2
    require(mirrored_usable >= required["usableStorageTiB"], "N+1 mirrored storage capacity is insufficient")

    require(spec.get("workflowType") == "VCF", "workflowType must be VCF")
    require(spec.get("version") == fixture["targetVcfVersion"] == release, "SddcSpec release mismatch")
    require(spec.get("sddcId") == fixture["site"]["sddcId"], "sddcId mismatch")
    require(spec.get("vcfInstanceName") == fixture["site"]["vcfInstanceName"], "VCF instance name mismatch")
    hosts = spec.get("hostSpecs")
    require(isinstance(hosts, list) and len(hosts) == minimum_hosts, "SddcSpec must use exactly four hosts")
    require([host.get("hostname") for host in hosts] == [host["hostname"] for host in fixture["hosts"]], "host inventory mismatch")
    for host in hosts:
        credentials = host.get("credentials", {})
        require(credentials.get("username") == "root", f"{host.get('hostname')}: ESX username must be root")
        require(credentials.get("password") == fixture["credentials"]["esxRootPassword"], f"{host.get('hostname')}: credential mismatch")

    dns = spec.get("dnsSpec", {})
    require(dns.get("subdomain") == fixture["dns"]["subdomain"], "DNS subdomain mismatch")
    require(dns.get("nameservers") == fixture["dns"]["nameservers"], "DNS server mismatch")
    require(spec.get("ntpServers") == fixture["ntpServers"], "NTP server mismatch")

    vcenter = spec.get("vcenterSpec", {})
    require(vcenter.get("vcenterHostname") == fixture["appliances"]["vcenterHostname"], "vCenter hostname mismatch")
    require(vcenter.get("rootVcenterPassword") == fixture["credentials"]["vcenterRootPassword"], "vCenter credential mismatch")
    require(vcenter.get("adminUserSsoPassword") == fixture["credentials"]["ssoPassword"], "SSO credential mismatch")
    require(vcenter.get("version") == green["componentCombination"]["vCenter"], "vCenter version is not in the pinned combination")
    require(vcenter.get("useExistingDeployment") is False, "greenfield vCenter cannot reuse a deployment")

    manager = spec.get("sddcManagerSpec", {})
    require(manager.get("hostname") == fixture["appliances"]["sddcManagerHostname"], "SDDC Manager hostname mismatch")
    require(manager.get("version") == green["componentCombination"]["SDDC Manager"], "SDDC Manager version mismatch")
    require(manager.get("useExistingDeployment") is False, "greenfield SDDC Manager cannot reuse a deployment")

    datastore = spec.get("datastoreSpec", {}).get("vsanSpec", {})
    require(datastore.get("esaConfig", {}).get("enabled") is True, "vSAN ESA must be enabled")
    require(datastore.get("failuresToTolerate") == availability["hostFailuresToTolerate"], "vSAN FTT mismatch")

    dvs_specs = spec.get("dvsSpecs")
    require(isinstance(dvs_specs, list) and len(dvs_specs) == 1, "consolidated design must use one DVS")
    dvs = dvs_specs[0]
    require(dvs.get("mtu") == 9000, "DVS MTU must support the fixture's jumbo-frame networks")
    require(dvs.get("networks") == [item["networkType"] for item in fixture["networks"]], "DVS networks mismatch")
    uplink_pairs = [f"{item.get('id')}:{item.get('uplink')}" for item in dvs.get("vmnicsToUplinks", [])]
    require(uplink_pairs == green["dvsUplinks"], "redundant DVS uplink mapping mismatch")

    network_specs = spec.get("networkSpecs")
    require(isinstance(network_specs, list) and len(network_specs) == len(fixture["networks"]), "network count mismatch")
    actual_networks = {item.get("networkType"): item for item in network_specs}
    require(len(actual_networks) == len(network_specs), "network types must be unique")
    for expected in fixture["networks"]:
        require(expected["networkType"] in actual_networks, f"missing {expected['networkType']} network")
        exact_network(actual_networks[expected["networkType"]], expected)

    nsx = spec.get("nsxtSpec", {})
    expected_nsx = fixture["nsx"]
    require(len(nsx.get("nsxtManagers", [])) == green["nsxManagerCount"], "NSX must have three managers")
    require([item.get("hostname") for item in nsx["nsxtManagers"]] == expected_nsx["managerHostnames"], "NSX manager names mismatch")
    require(nsx.get("vipFqdn") == expected_nsx["vipFqdn"], "NSX VIP mismatch")
    require(nsx.get("transportVlanId") == expected_nsx["transportVlanId"], "NSX transport VLAN mismatch")
    require(nsx.get("version") == green["componentCombination"]["NSX"], "NSX version mismatch")
    require(nsx.get("useExistingDeployment") is False, "greenfield NSX cannot reuse a deployment")
    tep = nsx.get("ipAddressPoolSpec", {})
    require(tep.get("name") == expected_nsx["tepPoolName"], "TEP pool name mismatch")
    require(len(tep.get("subnets", [])) == 1, "TEP pool must contain one subnet")
    tep_subnet = tep["subnets"][0]
    require(tep_subnet.get("cidr") == expected_nsx["tepCidr"] and tep_subnet.get("gateway") == expected_nsx["tepGateway"], "TEP subnet mismatch")
    require(tep_subnet.get("ipAddressPoolRanges") == [{"start": expected_nsx["tepStart"], "end": expected_nsx["tepEnd"]}], "TEP range mismatch")

    for service_name in green["requiredServices"]:
        require(service_name in spec, f"missing required VCF 9.1 service {service_name}")
    for service_name in ("fleetLcmSpec", "sddcLcmSpec", "fleetDepotSpec", "telemetryAcceptorSpec"):
        require(spec[service_name].get("version") == release, f"{service_name} version mismatch")

    vsp = spec["vspClusterSpec"]
    appliances = fixture["appliances"]
    require(vsp.get("platformFqdn") == appliances["vspPlatformFqdn"], "VSP platform FQDN mismatch")
    require(vsp.get("instanceFqdn") == appliances["vspInstanceFqdn"], "VSP instance FQDN mismatch")
    require(vsp.get("fleetFqdn") == appliances["vspFleetFqdn"], "VSP fleet FQDN mismatch")
    require(vsp.get("internalClusterCidrIpv4") == appliances["vspInternalCidr"], "VSP internal CIDR mismatch")
    require(vsp.get("version") == green["componentCombination"]["VCF Management Services"], "VSP version mismatch")
    pool = vsp.get("ipv4Pool", {})
    require(pool.get("cidr") == appliances["vspPoolCidr"], "VSP management CIDR mismatch")
    require(pool.get("ipRange") == {"startIpAddress": appliances["vspPoolStart"], "endIpAddress": appliances["vspPoolEnd"]}, "VSP management pool mismatch")
    start = int(ipaddress.ip_address(appliances["vspPoolStart"]))
    end = int(ipaddress.ip_address(appliances["vspPoolEnd"]))
    require(end - start + 1 >= green["minimumVspManagementAddresses"], "VSP pool has fewer than 12 addresses")

    operations = spec["vcfOperationsSpec"]
    require(operations.get("version") == green["componentCombination"]["VCF Operations"], "VCF Operations version mismatch")
    require(operations.get("useExistingDeployment") is False, "greenfield VCF Operations cannot reuse a deployment")
    require([node.get("hostname") for node in operations.get("nodes", [])] == [appliances["operationsHostname"]], "VCF Operations node mismatch")
    license_server = spec["licenseServerSpec"]
    require(license_server.get("hostname") == appliances["licenseServerHostname"], "license server hostname mismatch")
    require(license_server.get("version") == green["componentCombination"]["VCF License Server"], "license server version mismatch")
    require(license_server.get("useExistingDeployment") is False, "greenfield license server cannot reuse a deployment")


def check_migration(plan: dict[str, Any], fixture: dict[str, Any], snapshot: dict[str, Any]) -> None:
    require(plan.get("estateId") == fixture["estateId"], "migration estateId mismatch")
    require(plan.get("targetVcfVersion") == fixture["targetVcfVersion"] == snapshot["release"], "migration target release mismatch")
    steps = plan["steps"]
    inventory = fixture["components"]
    require(len(steps) == len(inventory), "migration must contain exactly one step per estate component")
    require([step["order"] for step in steps] == list(range(1, len(steps) + 1)), "migration order values must be contiguous")
    inventory_by_name = {item["name"]: item for item in inventory}
    require(len(inventory_by_name) == len(inventory), "estate component names must be unique")
    require({step["component"] for step in steps} == set(inventory_by_name), "migration component coverage mismatch")
    require(len({step["component"] for step in steps}) == len(steps), "migration contains a duplicate component")

    authority = snapshot["migration"]
    previous_stage = -1
    for step in steps:
        name = step["component"]
        expected = authority["components"][name]
        require(step["currentVersion"] == inventory_by_name[name]["currentVersion"], f"{name}: current version mismatch")
        require(step["target"] == expected["target"], f"{name}: target mismatch")
        require(step["action"] == expected["action"], f"{name}: action mismatch")
        require(set(step["gates"]) == set(expected["requiredGates"]), f"{name}: gates mismatch")
        stage = authority["stageOrder"][expected["stage"]]
        require(stage >= previous_stage, f"{name}: violates pinned stage order")
        previous_stage = stage


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temporary:
        classes = Path(temporary)
        compile_client(classes)
        greenfield_artifact = run_client(classes, GREENFIELD_FIXTURE)
        require(
            greenfield_artifact == run_client(classes, GREENFIELD_FIXTURE),
            "greenfield generation is not deterministic",
        )

        # This is deliberately the first artifact assertion: validate the raw
        # greenfield value with the installer specification's own SddcSpec.
        openapi = json.loads(OPENAPI.read_text(encoding="utf-8"))
        sddc_schema = openapi["components"]["schemas"]["SddcSpec"]
        validate_schema(greenfield_artifact, sddc_schema, openapi)

        check_protected_inputs()
        check_research_sources()
        fixture = json.loads(GREENFIELD_FIXTURE.read_text(encoding="utf-8"))
        snapshot = json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))
        check_greenfield(greenfield_artifact, fixture, snapshot)

        migration_artifact = run_client(classes, ESTATE_FIXTURE)
        require(
            migration_artifact == run_client(classes, ESTATE_FIXTURE),
            "migration generation is not deterministic",
        )
        migration_schema = json.loads(MIGRATION_SCHEMA.read_text(encoding="utf-8"))
        validate_schema(migration_artifact, migration_schema, migration_schema)
        estate = json.loads(ESTATE_FIXTURE.read_text(encoding="utf-8"))
        check_migration(migration_artifact, estate, snapshot)

    print("verification passed: installer schema, greenfield architecture, and migration plan")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (VerificationError, KeyError, TypeError, ValueError) as error:
        print(f"verification failed: {error}", file=sys.stderr)
        raise SystemExit(1)
