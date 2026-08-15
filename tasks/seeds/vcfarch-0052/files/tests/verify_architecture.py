#!/usr/bin/env python3
"""Protected, offline verification for the generated VCF architecture."""

from __future__ import annotations

import ast
import copy
import importlib
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "architecture.json"
INVENTORY = ROOT / "fixtures" / "estate_inventory.json"
SNAPSHOT = ROOT / "fixtures" / "compatibility_snapshot.json"
OPENAPI = ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
RESEARCH = ROOT / "research.md"


class VerificationError(AssertionError):
    """A concise verification failure."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise VerificationError(f"missing required file: {path.relative_to(ROOT)}") from error
    except json.JSONDecodeError as error:
        raise VerificationError(
            f"invalid JSON in {path.relative_to(ROOT)}: {error}"
        ) from error


def resolve_pointer(document: Any, reference: str) -> Any:
    require(reference.startswith("#/"), f"unsupported non-local schema reference {reference}")
    value = document
    for encoded in reference[2:].split("/"):
        key = encoded.replace("~1", "/").replace("~0", "~")
        value = value[key]
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
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def validate_schema(
    value: Any,
    schema: dict[str, Any],
    document: dict[str, Any],
    path: str = "$",
) -> list[str]:
    """Validate the OpenAPI schema keywords used by the pinned specification."""
    if "$ref" in schema:
        return validate_schema(value, resolve_pointer(document, schema["$ref"]), document, path)

    errors: list[str] = []
    if value is None and schema.get("nullable"):
        return errors

    for branch in schema.get("allOf", []):
        errors.extend(validate_schema(value, branch, document, path))

    if "oneOf" in schema:
        matches = [
            validate_schema(value, branch, document, path)
            for branch in schema["oneOf"]
        ]
        if sum(not result for result in matches) != 1:
            errors.append(f"{path}: must match exactly one oneOf branch")
            return errors

    if "anyOf" in schema:
        matches = [
            validate_schema(value, branch, document, path)
            for branch in schema["anyOf"]
        ]
        if not any(not result for result in matches):
            errors.append(f"{path}: must match at least one anyOf branch")
            return errors

    expected = schema.get("type")
    if isinstance(expected, list):
        if not any(json_type_matches(value, item) for item in expected):
            errors.append(f"{path}: expected one of {expected}, got {type(value).__name__}")
            return errors
    elif expected and not json_type_matches(value, expected):
        errors.append(f"{path}: expected {expected}, got {type(value).__name__}")
        return errors

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value is not in enum {schema['enum']}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                errors.append(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in properties:
                errors.extend(validate_schema(child, properties[key], document, child_path))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{child_path}: additional property is not allowed")
            elif isinstance(schema.get("additionalProperties"), dict):
                errors.extend(
                    validate_schema(child, schema["additionalProperties"], document, child_path)
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
            serialized = [json.dumps(item, sort_keys=True) for item in value]
            if len(serialized) != len(set(serialized)):
                errors.append(f"{path}: array items are not unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(validate_schema(item, item_schema, document, f"{path}[{index}]"))

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], value)
            except re.error as error:
                errors.append(f"{path}: unusable schema pattern: {error}")
            else:
                if matched is None:
                    errors.append(f"{path}: does not match pattern {schema['pattern']!r}")

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


def validate_as_sddc_spec(artifact: Any, openapi: dict[str, Any]) -> None:
    schema = resolve_pointer(openapi, "#/components/schemas/SddcSpec")
    errors = validate_schema(artifact, schema, openapi)
    if errors:
        excerpt = "\n".join(f"  - {item}" for item in errors[:20])
        raise VerificationError(f"SddcSpec schema validation failed:\n{excerpt}")


def indexed(items: list[dict[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        value = item.get(key)
        require(isinstance(value, str) and value, f"{label} item is missing {key}")
        require(value not in result, f"duplicate {label} {value}")
        result[value] = item
    return result


def verify_installer_fields(
    artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    target = inventory["target"]
    management = inventory["managementDomain"]
    appliances = inventory["appliances"]
    services = inventory["services"]

    require(inventory["estateType"] == "GREENFIELD", "fixture is not greenfield")
    require(inventory["existingComponents"] == [], "greenfield inventory has components")
    require(artifact["sddcId"] == management["sddcId"], "wrong sddcId")
    require(artifact.get("workflowType") == target["workflowType"], "wrong workflowType")
    require(artifact.get("version") == target["vcfVersion"], "wrong SddcSpec version")
    require(openapi_version(snapshot) == target["installerSpecTag"], "snapshot/spec tag mismatch")

    hosts = artifact.get("hostSpecs", [])
    hostnames = [item.get("hostname") for item in hosts]
    require(len(hosts) == management["requiredHostCount"], "wrong management host count")
    require(
        len(set(hostnames)) == len(hostnames) and set(hostnames) == set(management["hostnames"]),
        "management hosts do not match inventory",
    )

    vcenter = artifact["vcenterSpec"]
    require(vcenter["vcenterHostname"] == appliances["vcenterFqdn"], "wrong vCenter FQDN")
    root_token = vcenter["rootVcenterPassword"]
    require(
        isinstance(root_token, str) and root_token.startswith("${") and root_token.endswith("}"),
        "vCenter credential must be an obvious substitution token",
    )
    require(
        artifact.get("sddcManagerSpec", {}).get("hostname")
        == appliances["sddcManagerHostname"],
        "wrong SDDC Manager hostname",
    )
    require(artifact["dnsSpec"]["subdomain"] == services["dnsSubdomain"], "wrong DNS domain")
    require(artifact["dnsSpec"].get("nameservers") == services["nameServers"], "wrong DNS servers")
    require(artifact.get("ntpServers") == services["ntpServers"], "wrong NTP servers")

    vsan = artifact.get("datastoreSpec", {}).get("vsanSpec", {})
    require(vsan.get("esaConfig", {}).get("enabled") is True, "vSAN ESA must be enabled")
    require(
        vsan.get("failuresToTolerate") == management["failuresToTolerate"],
        "vSAN failuresToTolerate does not match the requirement",
    )

    nsxt = artifact.get("nsxtSpec", {})
    require(nsxt.get("vipFqdn") == appliances["nsxVipFqdn"], "wrong NSX VIP")
    manager_names = [item.get("hostname") for item in nsxt.get("nsxtManagers", [])]
    require(
        len(manager_names) == len(set(manager_names))
        and set(manager_names) == set(appliances["nsxManagerHostnames"]),
        "wrong NSX Manager nodes",
    )
    host_tep = next(item for item in inventory["networks"] if item["networkType"] == "HOST_TEP")
    require(nsxt.get("transportVlanId") == host_tep["vlanId"], "wrong host TEP VLAN")


def openapi_version(snapshot: dict[str, Any]) -> str:
    return str(snapshot["installerSpecTag"])


def verify_networks(artifact: dict[str, Any], inventory: dict[str, Any]) -> None:
    wanted = indexed(inventory["networks"], "networkType", "inventory network")
    actual = indexed(artifact.get("networkSpecs", []), "networkType", "SddcSpec network")
    require(set(actual) == set(wanted), "SddcSpec network types do not exactly match inventory")

    networks: list[ipaddress.IPv4Network] = []
    vlans: list[int] = []
    for network_type, expected in wanted.items():
        observed = actual[network_type]
        for field in ("vlanId", "subnet", "gateway", "mtu"):
            require(
                observed.get(field) == expected[field],
                f"{network_type} has wrong {field}",
            )
        ranges = observed.get("includeIpAddressRanges")
        require(isinstance(ranges, list) and len(ranges) == 1, f"{network_type} needs one IP range")
        require(ranges[0].get("startIpAddress") == expected["poolStart"], f"{network_type} pool start")
        require(ranges[0].get("endIpAddress") == expected["poolEnd"], f"{network_type} pool end")
        parsed = ipaddress.ip_network(observed["subnet"], strict=True)
        require(ipaddress.ip_address(observed["gateway"]) in parsed, f"{network_type} gateway outside subnet")
        require(ipaddress.ip_address(expected["poolStart"]) in parsed, f"{network_type} pool outside subnet")
        require(ipaddress.ip_address(expected["poolEnd"]) in parsed, f"{network_type} pool outside subnet")
        networks.append(parsed)
        vlans.append(observed["vlanId"])

    require(len(vlans) == len(set(vlans)), "network VLAN IDs must be unique")
    for index, left in enumerate(networks):
        for right in networks[index + 1 :]:
            require(not left.overlaps(right), f"network overlap: {left} and {right}")

    dvs_specs = artifact.get("dvsSpecs", [])
    require(len(dvs_specs) == 1, "exactly one management-domain DVS is required")
    dvs = dvs_specs[0]
    management = inventory["managementDomain"]
    require(dvs.get("dvsName") == management["dvsName"], "wrong DVS name")
    require(dvs.get("mtu") == management["dvsMtu"], "wrong DVS MTU")
    mapping = {item.get("id"): item.get("uplink") for item in dvs.get("vmnicsToUplinks", [])}
    require(
        len(mapping) == len(dvs.get("vmnicsToUplinks", [])),
        "duplicate host pNIC mappings",
    )
    require(mapping == management["hostPnicMap"], "wrong host pNIC-to-uplink mapping")
    dvs_networks = dvs.get("networks", [])
    require(
        len(dvs_networks) == len(set(dvs_networks)) and set(dvs_networks) == set(wanted),
        "DVS does not carry each planned network exactly once",
    )


def verify_architecture_extension(
    artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    extension = artifact.get("x-architecture")
    require(isinstance(extension, dict), "missing x-architecture vendor extension")
    site = inventory["site"]
    requirements = inventory["edgeService"]
    management = inventory["managementDomain"]

    require(extension.get("site") == site, "site/failure-domain design does not match inventory")
    traced = extension.get("requirements", {})
    require(
        traced.get("northSouthThroughputGbps") == requirements["northSouthThroughputGbps"],
        "throughput requirement is not traced",
    )
    require(traced.get("nodeFailureTolerance") == requirements["nodeFailureTolerance"], "node failure requirement")
    require(traced.get("torFailureTolerance") == requirements["torFailureTolerance"], "ToR failure requirement")
    require(traced.get("managementFailuresToTolerate") == management["failuresToTolerate"], "management FTT trace")

    host_placements = extension.get("managementDomain", {}).get("hostPlacement", [])
    placement_names = [item.get("hostname") for item in host_placements]
    require(
        len(placement_names) == len(set(placement_names))
        and set(placement_names) == set(management["hostnames"]),
        "host placement must name every management host exactly once",
    )
    rack_counts = {rack: 0 for rack in site["racks"]}
    for placement in host_placements:
        require(placement.get("rack") in rack_counts, "host placed outside declared racks")
        rack_counts[placement["rack"]] += 1
    require(all(count >= 2 for count in rack_counts.values()), "management hosts must span both racks")

    versions = extension.get("compatibility", {}).get("componentVersions")
    require(versions == snapshot["componentRelease"], "component versions contradict pinned BOM")
    claim_ids = extension.get("compatibility", {}).get("claimIds")
    require(isinstance(claim_ids, list), "pinned compatibility claim IDs are missing")
    require(
        len(claim_ids) == len(set(claim_ids))
        and set(claim_ids) == set(snapshot["requiredClaimIds"]),
        "pinned compatibility claim IDs are incomplete or duplicated",
    )
    supported_claims = {item["id"] for item in snapshot["claims"] if item.get("supported") is True}
    require(set(claim_ids) <= supported_claims, "artifact cites an unsupported pinned claim")
    require(
        extension.get("compatibility", {}).get("snapshotId") == snapshot["snapshotId"],
        "wrong compatibility snapshot ID",
    )

    edge = extension.get("edgeCluster", {})
    require(edge.get("nodeCount") == requirements["edgeNodeCount"], "wrong Edge node count")
    remaining = edge["nodeCount"] - requirements["nodeFailureTolerance"]
    require(remaining > 0, "Edge node failure leaves no service node")
    throughput = requirements["northSouthThroughputGbps"]
    candidates = sorted(
        (
            item
            for item in snapshot["edgeFormFactors"]
            if item.get("supportedForTier0")
            and remaining * item["validatedNorthSouthGbpsPerNode"] >= throughput
        ),
        key=lambda item: item["validatedNorthSouthGbpsPerNode"],
    )
    require(candidates, "pinned snapshot has no form factor meeting capacity")
    selected = candidates[0]
    require(edge.get("formFactor") == selected["name"], "Edge form factor is not capacity-derived minimum")
    node_failure_capacity = remaining * selected["validatedNorthSouthGbpsPerNode"]
    require(edge.get("nodeFailureCapacityGbps") == node_failure_capacity, "wrong node-failure capacity")

    routing = edge.get("routing", {})
    require(routing.get("tier0Mode") == requirements["tier0Mode"], "Tier-0 must be active-active")
    require(routing.get("protocol") == requirements["routingProtocol"], "wrong routing protocol")
    require(routing.get("ecmp") is True, "active-active Tier-0 must use ECMP")

    nodes = edge.get("nodes", [])
    require(len(nodes) == requirements["edgeNodeCount"], "Edge node list is incomplete")
    node_names = [item.get("name") for item in nodes]
    require(len(node_names) == len(set(node_names)), "Edge node names must be unique")
    require({item.get("rack") for item in nodes} == set(site["racks"]), "Edge nodes must span the two racks")
    require(all(item.get("formFactor") == selected["name"] for item in nodes), "mixed Edge form factors")

    uplink_rule = snapshot["edgeUplinkRule"]
    uplinks = edge.get("uplinks", [])
    require(
        len(uplinks) == len(nodes) * uplink_rule["uplinksPerNode"],
        "wrong number of Edge data uplinks",
    )
    networks = indexed(inventory["networks"], "networkType", "inventory network")
    for node_name in node_names:
        per_node = [item for item in uplinks if item.get("edgeNode") == node_name]
        require(len(per_node) == uplink_rule["uplinksPerNode"], f"{node_name} uplink count")
        fabrics = {item.get("fabric") for item in per_node}
        require(fabrics == set(site["independentTorFabrics"]), f"{node_name} must use both fabrics")
        require(len({item.get("interface") for item in per_node}) == len(per_node), f"{node_name} interfaces")
        for item in per_node:
            fabric = item["fabric"]
            network_type = uplink_rule["fabricToNetworkType"][fabric]
            require(item.get("speedGbps") == uplink_rule["speedGbpsPerUplink"], "wrong Edge uplink speed")
            require(item.get("networkType") == network_type, "Edge uplink uses wrong network")
            require(item.get("vlanId") == networks[network_type]["vlanId"], "Edge uplink uses wrong VLAN")
            require(
                item.get("backingVdsUplink") == uplink_rule["fabricToVdsUplink"][fabric],
                "Edge uplink uses wrong VDS uplink",
            )

    surviving_fabrics = uplink_rule["uplinksPerNode"] - requirements["torFailureTolerance"]
    tor_failure_capacity = len(nodes) * surviving_fabrics * uplink_rule["speedGbpsPerUplink"]
    require(edge.get("torFailureCapacityGbps") == tor_failure_capacity, "wrong ToR-failure capacity")
    require(node_failure_capacity >= throughput, "Edge-node-failure capacity misses requirement")
    require(tor_failure_capacity >= throughput, "ToR-failure capacity misses requirement")


def verify_research() -> None:
    try:
        research = RESEARCH.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise VerificationError("missing required file: research.md") from error

    lines = research.splitlines()
    found_urls = re.findall(r"https://[^\s)>]+", research)
    urls = [url.rstrip(".,;]") for url in found_urls]
    require(len(set(urls)) >= 2, "research.md must record multiple live sources")
    require(len(urls) == len(set(urls)), "research.md contains duplicate source URLs")

    for url in urls:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        require(
            hostname == "broadcom.com" or hostname.endswith(".broadcom.com"),
            "research sources must be Broadcom-published HTTPS pages",
        )
        require(".invalid" not in hostname, "research source uses a non-reachable test domain")

        line_index = next(index for index, line in enumerate(lines) if url in line)
        line = lines[line_index]
        title = line[: line.index(url)].strip(" -*_[]()")
        if len(title) < 8:
            title = " ".join(lines[max(0, line_index - 2) : line_index]).strip(" -*_#[]()")
        conclusion = line[line.index(url) + len(url) :].strip(" -—.[]()")
        if len(conclusion) < 30:
            conclusion = " ".join(lines[line_index + 1 : line_index + 3]).strip(" -*_#[]()")
        require(len(title) >= 8, "research source is missing a descriptive title")
        require(len(conclusion) >= 30, "research source is missing its design conclusion")

    require(
        re.search(r"\b20\d{2}-\d{2}-\d{2}\b", research) is not None,
        "research.md must record an ISO access date",
    )
    lower = research.lower()
    topic_terms = {
        "compatibility": ("compatib", "matrix"),
        "interoperability": ("interoperab", "interop", "bom"),
        "sizing": ("sizing", "resizing", "form factor"),
        "upgrade": ("upgrade", "update", "lifecycle"),
        "Edge": ("edge",),
    }
    for topic, terms in topic_terms.items():
        require(any(term in lower for term in terms), f"research.md does not cover {topic} guidance")


def verify_package(
    artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    require(project.get("project", {}).get("dependencies", []) == [], "project has dependencies")

    for path in sorted((ROOT / "vcf_architecture").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imports = [node.module.split(".", 1)[0]]
            else:
                continue
            for imported in imports:
                require(
                    imported in sys.stdlib_module_names or imported == "vcf_architecture",
                    f"non-stdlib import {imported!r} in {path.name}",
                )

    sys.path.insert(0, str(ROOT))
    try:
        module = importlib.import_module("vcf_architecture")
        generated = module.build_architecture(copy.deepcopy(inventory), copy.deepcopy(snapshot))
    finally:
        sys.path.pop(0)
    require(generated == artifact, "build_architecture output differs from architecture.json")

    # Exercise the derivation contract with protected perturbations so a static
    # artifact or a builder that merely repeats the fixture cannot pass.
    lower_load_inventory = copy.deepcopy(inventory)
    lower_load_inventory["edgeService"]["northSouthThroughputGbps"] = 15
    lower_load = module.build_architecture(lower_load_inventory, copy.deepcopy(snapshot))
    lower_edge = lower_load.get("x-architecture", {}).get("edgeCluster", {})
    surviving_nodes = (
        lower_load_inventory["edgeService"]["edgeNodeCount"]
        - lower_load_inventory["edgeService"]["nodeFailureTolerance"]
    )
    expected_form_factor = min(
        (
            item
            for item in snapshot["edgeFormFactors"]
            if item.get("supportedForTier0")
            and surviving_nodes * item["validatedNorthSouthGbpsPerNode"] >= 15
        ),
        key=lambda item: item["validatedNorthSouthGbpsPerNode"],
    )
    require(
        lower_edge.get("formFactor") == expected_form_factor["name"],
        "Edge form factor is not throughput-derived",
    )
    require(
        lower_edge.get("nodeFailureCapacityGbps")
        == surviving_nodes * expected_form_factor["validatedNorthSouthGbpsPerNode"],
        "derived Edge capacity is not recomputed",
    )
    require(
        lower_load.get("x-architecture", {}).get("requirements", {}).get("northSouthThroughputGbps")
        == 15,
        "derived architecture does not trace the changed throughput requirement",
    )

    remapped_snapshot = copy.deepcopy(snapshot)
    remapped_snapshot["edgeUplinkRule"]["speedGbpsPerUplink"] = 40
    remapped_snapshot["edgeUplinkRule"]["fabricToVdsUplink"] = {
        "fabric-a": "uplink4",
        "fabric-b": "uplink3",
    }
    remapped = module.build_architecture(copy.deepcopy(inventory), remapped_snapshot)
    remapped_edge = remapped.get("x-architecture", {}).get("edgeCluster", {})
    require(remapped_edge.get("torFailureCapacityGbps") == 80, "ToR capacity is not uplink-derived")
    remapped_uplinks = remapped_edge.get("uplinks", [])
    require(
        all(item.get("speedGbps") == 40 for item in remapped_uplinks),
        "Edge uplink speeds are not compatibility-derived",
    )
    require(
        {item.get("fabric"): item.get("backingVdsUplink") for item in remapped_uplinks}
        == {"fabric-a": "uplink4", "fabric-b": "uplink3"},
        "Edge physical uplink mapping is not compatibility-derived",
    )

    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temporary:
        output = Path(temporary) / "architecture.json"
        environment = dict(os.environ)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "vcf_architecture",
                "--inventory",
                str(INVENTORY),
                "--compatibility",
                str(SNAPSHOT),
                "--output",
                str(output),
            ],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        require(process.returncode == 0, f"CLI failed: {process.stderr[-500:]}")
        require(load_json(output) == artifact, "CLI output differs from architecture.json")
        expected_bytes = json.dumps(artifact, indent=2, sort_keys=True) + "\n"
        require(output.read_text(encoding="utf-8") == expected_bytes, "CLI JSON is not deterministic")
        require(ARTIFACT.read_text(encoding="utf-8") == expected_bytes, "artifact JSON is not canonical")


def main() -> int:
    try:
        artifact = load_json(ARTIFACT)
        openapi = load_json(OPENAPI)

        # Contractual ordering: the upstream SddcSpec schema is the first check.
        validate_as_sddc_spec(artifact, openapi)

        inventory = load_json(INVENTORY)
        snapshot = load_json(SNAPSHOT)
        require(
            openapi.get("info", {}).get("version")
            == inventory["target"]["installerSpecTag"],
            "pinned OpenAPI document has the wrong version",
        )
        verify_installer_fields(artifact, inventory, snapshot)
        verify_networks(artifact, inventory)
        verify_architecture_extension(artifact, inventory, snapshot)
        verify_research()
        verify_package(artifact, inventory, snapshot)
    except VerificationError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    except (KeyError, TypeError, ValueError, StopIteration) as error:
        print(f"FAIL: malformed architecture: {error}", file=sys.stderr)
        return 1
    print("PASS: SddcSpec schema, pinned compatibility, capacity, availability, and package")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
