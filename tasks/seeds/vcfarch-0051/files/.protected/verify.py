#!/usr/bin/env python3
"""Deterministic acceptance verifier for vcfarch-0051."""

from __future__ import annotations

import ipaddress
import json
import math
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]


class VerificationError(AssertionError):
    pass


def fail(message: str) -> None:
    raise VerificationError(message)


def read_json(path: Path):
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        fail(f"missing required artifact: {path.relative_to(ROOT)}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {path.relative_to(ROOT)}: {exc}")


def resolve_pointer(document, reference: str):
    if not reference.startswith("#/"):
        fail(f"unsupported non-local schema reference: {reference}")
    value = document
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        try:
            value = value[part]
        except (KeyError, TypeError):
            fail(f"unresolvable schema reference: {reference}")
    return value


def json_type_matches(value, expected: str) -> bool:
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
    fail(f"unsupported JSON Schema type in verifier: {expected}")


def validate_schema(value, schema, document, path: str = "$") -> None:
    """Validate the JSON Schema/OpenAPI keywords used by the pinned contracts."""
    if "$ref" in schema:
        validate_schema(value, resolve_pointer(document, schema["$ref"]), document, path)
        return

    if "const" in schema and value != schema["const"]:
        fail(f"{path}: expected constant {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        fail(f"{path}: {value!r} is not one of {schema['enum']!r}")

    expected_type = schema.get("type")
    if expected_type is not None:
        choices = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(json_type_matches(value, choice) for choice in choices):
            fail(f"{path}: expected schema type {expected_type!r}, got {type(value).__name__}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [name for name in required if name not in value]
        if missing:
            fail(f"{path}: missing required properties {missing!r}")
        properties = schema.get("properties", {})
        for name, child in value.items():
            if name in properties:
                validate_schema(child, properties[name], document, f"{path}.{name}")
            elif schema.get("additionalProperties") is False:
                fail(f"{path}: additional property {name!r} is not allowed")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_schema(
                    child, schema["additionalProperties"], document, f"{path}.{name}"
                )
        if "minProperties" in schema and len(value) < schema["minProperties"]:
            fail(f"{path}: has fewer than {schema['minProperties']} properties")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            fail(f"{path}: has more than {schema['maxProperties']} properties")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            fail(f"{path}: has fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            fail(f"{path}: has more than {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(encoded) != len(set(encoded)):
                fail(f"{path}: items must be unique")
        if "items" in schema:
            for index, child in enumerate(value):
                validate_schema(child, schema["items"], document, f"{path}[{index}]")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            fail(f"{path}: string is shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            fail(f"{path}: string is longer than {schema['maxLength']}")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            fail(f"{path}: string does not match {schema['pattern']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            fail(f"{path}: value is below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            fail(f"{path}: value is above maximum {schema['maximum']}")


def same(actual, expected, label: str) -> None:
    if actual != expected:
        fail(f"{label}: expected {expected!r}, got {actual!r}")


def minimum_host_count(requirements) -> int:
    capacity = requirements["capacity"]
    needed = capacity["requiredAfterHostFailure"]
    host = capacity["host"]
    failures = requirements["availability"]["hostFailuresToTolerate"]
    cpu = math.ceil(needed["cpuCores"] / host["cpuCores"]) + failures
    memory = math.ceil(needed["memoryGiB"] / host["memoryGiB"]) + failures
    storage = capacity["storage"]
    usable_per_surviving_host = (
        host["rawStorageTiB"]
        / storage["mirrorCopies"]
        * (1 - storage["freeSpacePercent"] / 100)
    )
    disk = math.ceil(needed["usableStorageTiB"] / usable_per_surviving_host) + failures
    return max(cpu, memory, disk)


def expected_networks(requirements):
    return [
        {
            "networkType": network["networkType"],
            "subnet": network["cidr"],
            "gateway": network["gateway"],
            "subnetMask": network["subnetMask"],
            "includeIpAddressRanges": [
                {
                    "startIpAddress": network["rangeStart"],
                    "endIpAddress": network["rangeEnd"],
                }
            ],
            "vlanId": network["vlanId"],
            "mtu": network["mtu"],
        }
        for network in requirements["networks"]
    ]


def verify_sddc_semantics(sddc, requirements) -> None:
    same(sddc.get("sddcId"), requirements["designId"], "SddcSpec.sddcId")
    same(sddc.get("vcfInstanceName"), requirements["instanceName"], "VCF instance name")
    same(sddc.get("workflowType"), "VCF", "workflow type")
    same(sddc.get("version"), requirements["targetBundle"], "installer version")

    expected_count = minimum_host_count(requirements)
    if not requirements["capacity"]["useSmallestCompliantHostCount"]:
        fail("fixture must request the smallest compliant host count")
    same(len(sddc.get("hostSpecs", [])), expected_count, "right-sized host count")
    naming = requirements["hostNaming"]
    expected_hosts = [
        f"{naming['prefix']}{index:0{naming['digits']}d}"
        for index in range(naming["firstIndex"], naming["firstIndex"] + expected_count)
    ]
    same([host.get("hostname") for host in sddc["hostSpecs"]], expected_hosts, "host names")

    site = requirements["site"]
    same(sddc.get("dnsSpec"), {
        "subdomain": site["dnsSubdomain"],
        "nameservers": site["dnsServers"],
    }, "DNS design")
    same(sddc.get("ntpServers"), site["ntpServers"], "NTP design")
    same(sddc.get("networkSpecs"), expected_networks(requirements), "network design")

    parsed_networks = [ipaddress.ip_network(item["subnet"], strict=False) for item in sddc["networkSpecs"]]
    for left_index, left in enumerate(parsed_networks):
        for right in parsed_networks[left_index + 1:]:
            if left.overlaps(right):
                fail(f"management networks overlap: {left} and {right}")

    switch = requirements["switching"]
    same(len(sddc.get("dvsSpecs", [])), 1, "distributed switch count")
    dvs = sddc["dvsSpecs"][0]
    same(dvs.get("dvsName"), switch["dvsName"], "distributed switch name")
    same(dvs.get("mtu"), switch["mtu"], "distributed switch MTU")
    same(dvs.get("vmnicsToUplinks"), switch["uplinks"], "dual-ToR uplink mapping")
    same(dvs.get("networks"), [n["networkType"] for n in requirements["networks"]], "DVS networks")

    storage = sddc.get("datastoreSpec", {}).get("vsanSpec", {})
    same(storage.get("esaConfig"), {"enabled": True}, "vSAN ESA selection")
    same(
        storage.get("failuresToTolerate"),
        requirements["availability"]["hostFailuresToTolerate"],
        "vSAN failures to tolerate",
    )

    nsx_requirement = requirements["nsx"]
    nsx = sddc.get("nsxtSpec", {})
    same(
        [node.get("hostname") for node in nsx.get("nsxtManagers", [])],
        nsx_requirement["managerHostnames"],
        "NSX manager placement",
    )
    same(nsx.get("vipFqdn"), nsx_requirement["vipFqdn"], "NSX VIP")
    same(nsx.get("transportVlanId"), nsx_requirement["transportVlanId"], "NSX transport VLAN")
    same(nsx.get("ipAddressPoolSpec"), {
        "name": nsx_requirement["tepPoolName"],
        "subnets": [{
            "cidr": nsx_requirement["tepCidr"],
            "gateway": nsx_requirement["tepGateway"],
            "ipAddressPoolRanges": [{
                "start": nsx_requirement["tepRangeStart"],
                "end": nsx_requirement["tepRangeEnd"],
            }],
        }],
    }, "NSX TEP pool")

    appliances = requirements["appliances"]
    same(sddc.get("vcenterSpec", {}).get("vcenterHostname"), appliances["vcenterHostname"], "vCenter")
    same(sddc.get("sddcManagerSpec", {}).get("hostname"), appliances["sddcManagerHostname"], "SDDC Manager")
    same(
        sddc.get("vcfOperationsFleetManagementSpec", {}).get("hostname"),
        appliances["fleetManagementHostname"],
        "fleet management appliance",
    )
    operations = sddc.get("vcfOperationsSpec", {})
    same(
        [{"hostname": node.get("hostname"), "type": node.get("type")} for node in operations.get("nodes", [])],
        appliances["operationsNodes"],
        "VCF Operations HA nodes",
    )
    same(operations.get("loadBalancerFqdn"), appliances["operationsLoadBalancerFqdn"], "Operations load balancer")
    same(
        sddc.get("vcfAutomationSpec", {}).get("ipPool"),
        appliances["automationIpPool"],
        "VCF Automation HA IP pool",
    )
    same(
        sddc.get("vcfAutomationSpec", {}).get("hostname"),
        appliances["automationHostname"],
        "VCF Automation hostname",
    )

    for label, deployment in (
        ("vCenter", sddc.get("vcenterSpec", {})),
        ("SDDC Manager", sddc.get("sddcManagerSpec", {})),
        ("NSX", nsx),
        ("fleet management", sddc.get("vcfOperationsFleetManagementSpec", {})),
        ("VCF Operations", operations),
        ("VCF Automation", sddc.get("vcfAutomationSpec", {})),
    ):
        same(deployment.get("useExistingDeployment"), False, f"{label} greenfield flag")
        same(deployment.get("version"), requirements["targetBundle"], f"{label} version")


def verify_research(research, compatibility) -> None:
    if not isinstance(research, dict) or set(research) != {"consulted"}:
        fail("research record must contain exactly the top-level 'consulted' array")
    consulted = research["consulted"]
    if not isinstance(consulted, list):
        fail("research consulted value must be an array")

    catalog = compatibility["sourceCatalog"]
    same(len(consulted), len(catalog), "research source coverage")
    expected = {item["title"]: item for item in catalog}
    if len(expected) != len(catalog):
        fail("compatibility source catalog titles must be unique")

    seen = set()
    for index, entry in enumerate(consulted):
        path = f"research consulted entry {index + 1}"
        if not isinstance(entry, dict) or set(entry) != {"title", "url", "accessedOn", "claims"}:
            fail(f"{path} must contain exactly title, url, accessedOn, and claims")
        title = entry["title"]
        if not isinstance(title, str):
            fail(f"{path} title must be a string")
        if title not in expected:
            fail(f"{path} has an unknown source title {title!r}")
        if title in seen:
            fail(f"research source {title!r} is duplicated")
        seen.add(title)

        source = expected[title]
        same(entry["url"], source["url"], f"{title} source URL")
        parsed = urlparse(entry["url"])
        if parsed.scheme != "https" or parsed.hostname not in {
            "interopmatrix.broadcom.com",
            "knowledge.broadcom.com",
        }:
            fail(f"{title}: source must be an HTTPS Broadcom URL")
        try:
            accessed_on = date.fromisoformat(entry["accessedOn"])
        except (TypeError, ValueError):
            fail(f"{title}: accessedOn must be an ISO YYYY-MM-DD date")
        if accessed_on < date.fromisoformat(compatibility["asOf"]):
            fail(f"{title}: accessedOn predates the compatibility snapshot")
        claims = entry["claims"]
        if (
            not isinstance(claims, list)
            or not claims
            or any(not isinstance(claim, str) or not claim.strip() for claim in claims)
        ):
            fail(f"{title}: claims must be a nonempty list of nonempty strings")

    same(seen, set(expected), "research source coverage")


def verify_migration_semantics(plan, estate, compatibility) -> None:
    same(plan["estateId"], estate["estateId"], "migration estate")
    same(plan["targetBundle"], compatibility["targetBundle"], "migration target bundle")
    inventory = {item["id"]: item for item in estate["components"]}
    authority = {item["componentId"]: item for item in compatibility["components"]}
    step_ids = [step["componentId"] for step in plan["steps"]]
    same(step_ids, [item["id"] for item in estate["components"]], "inventory coverage and order")
    same(set(inventory), set(authority), "compatibility snapshot inventory coverage")
    same([step["order"] for step in plan["steps"]], list(range(1, len(step_ids) + 1)), "contiguous step order")

    seen = set()
    for step in plan["steps"]:
        component_id = step["componentId"]
        current = inventory[component_id]
        rule = authority[component_id]
        same(step["order"], rule["order"], f"{component_id} pinned order")
        same(step["component"], current["name"], f"{component_id} current component")
        same(step["currentVersion"], current["version"], f"{component_id} current version")
        same(step["targetComponent"], rule["targetName"], f"{component_id} target component")
        same(step["targetVersion"], rule["targetVersion"], f"{component_id} target version")
        same(step["action"], rule["requiredAction"], f"{component_id} compatibility action")
        if step["action"] in rule["unsupportedActions"]:
            fail(f"{component_id}: migration uses an explicitly unsupported action")
        same(step["gates"], rule["requiredGates"], f"{component_id} gates")
        same(step["dataDisposition"], rule["dataDisposition"], f"{component_id} data disposition")
        same(step["dataDisposition"], current["historyRequirement"], f"{component_id} history requirement")
        for gate in step["gates"]:
            if gate.startswith("step:") and gate.removeprefix("step:") not in seen:
                fail(f"{component_id}: prerequisite {gate!r} is not an earlier step")
        seen.add(component_id)


def load_and_validate_sddc(path: Path, openapi):
    artifact = read_json(path)
    schema = openapi["components"]["schemas"]["SddcSpec"]
    validate_schema(artifact, schema, openapi)
    return artifact


def main() -> int:
    # Binding first phase: load the submitted installer artifact and validate it
    # against the SddcSpec in the installer specification before any fixture,
    # migration, package, or architecture-specific assertion is evaluated.
    openapi = read_json(ROOT / "specifications/vcf-installer/vcf-installer-openapi.json")
    sddc = load_and_validate_sddc(ROOT / "artifacts/sddc-spec.json", openapi)

    compatibility = read_json(ROOT / "constraints/compatibility-snapshot.json")
    research = read_json(ROOT / "artifacts/research.json")
    verify_research(research, compatibility)

    migration_schema = read_json(ROOT / "constraints/migration-plan-schema.json")
    migration = read_json(ROOT / "artifacts/migration-plan.json")
    validate_schema(migration, migration_schema, migration_schema)

    requirements = read_json(ROOT / "fixtures/site-requirements.json")
    estate = read_json(ROOT / "fixtures/estate.json")
    verify_sddc_semantics(sddc, requirements)
    verify_migration_semantics(migration, estate, compatibility)

    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temporary:
        output = Path(temporary)
        command = [
            sys.executable,
            "-B",
            "-m",
            "vcf_architect",
            "--requirements",
            str(ROOT / "fixtures/site-requirements.json"),
            "--estate",
            str(ROOT / "fixtures/estate.json"),
            "--compatibility",
            str(ROOT / "constraints/compatibility-snapshot.json"),
            "--output-dir",
            str(output),
        ]
        completed = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
        if completed.returncode != 0:
            fail(f"package CLI failed ({completed.returncode}): {completed.stderr.strip()}")

        generated_sddc = load_and_validate_sddc(output / "sddc-spec.json", openapi)
        generated_migration = read_json(output / "migration-plan.json")
        validate_schema(generated_migration, migration_schema, migration_schema)
        same(generated_sddc, sddc, "checked-in and generated SddcSpec")
        same(generated_migration, migration, "checked-in and generated migration plan")

    print("PASS: research, installer schema, greenfield architecture, and ordered migration plan")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
