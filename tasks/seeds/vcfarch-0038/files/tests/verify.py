#!/usr/bin/env python3
"""Deterministic verifier for the generated VCF architecture artifacts."""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
from typing import Any


class VerificationError(AssertionError):
    pass


def fail(message: str) -> None:
    raise VerificationError(message)


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        fail(f"missing JSON artifact: {path.name}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {path.name}: {exc}")


def load_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        fail(f"missing research artifact: {path.name}")
    except UnicodeDecodeError as exc:
        fail(f"research artifact is not UTF-8: {exc}")


def resolve_pointer(document: Any, reference: str) -> Any:
    if not reference.startswith("#/"):
        fail(f"unsupported non-local schema reference: {reference}")
    current = document
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            fail(f"unresolvable schema reference: {reference}")
        current = current[part]
    return current


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
    fail(f"unsupported JSON Schema type: {expected}")


def validate(instance: Any, schema: Any, root: Any, path: str = "$") -> None:
    if isinstance(schema, bool):
        if not schema:
            fail(f"{path}: rejected by false schema")
        return
    if not isinstance(schema, dict):
        fail(f"{path}: malformed schema")

    if "$ref" in schema:
        validate(instance, resolve_pointer(root, schema["$ref"]), root, path)
        return

    if instance is None and schema.get("nullable") is True:
        return

    for child in schema.get("allOf", []):
        validate(instance, child, root, path)

    if "anyOf" in schema:
        accepted = 0
        for child in schema["anyOf"]:
            try:
                validate(instance, child, root, path)
                accepted += 1
            except VerificationError:
                pass
        if accepted == 0:
            fail(f"{path}: does not satisfy anyOf")

    if "oneOf" in schema:
        accepted = 0
        for child in schema["oneOf"]:
            try:
                validate(instance, child, root, path)
                accepted += 1
            except VerificationError:
                pass
        if accepted != 1:
            fail(f"{path}: must satisfy exactly one oneOf branch, got {accepted}")

    if "not" in schema:
        try:
            validate(instance, schema["not"], root, path)
        except VerificationError:
            pass
        else:
            fail(f"{path}: matches forbidden schema")

    expected_type = schema.get("type")
    if expected_type is not None:
        candidates = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(json_type_matches(instance, candidate) for candidate in candidates):
            fail(f"{path}: expected type {expected_type}, got {type(instance).__name__}")

    if "const" in schema and instance != schema["const"]:
        fail(f"{path}: expected constant {schema['const']!r}, got {instance!r}")
    if "enum" in schema and instance not in schema["enum"]:
        fail(f"{path}: value {instance!r} is not in enum")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                fail(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        for key, value in instance.items():
            child_path = f"{path}.{key}"
            if key in properties:
                validate(value, properties[key], root, child_path)
            elif schema.get("additionalProperties") is False:
                fail(f"{path}: unexpected property {key!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate(value, schema["additionalProperties"], root, child_path)
        if len(instance) < schema.get("minProperties", 0):
            fail(f"{path}: too few properties")
        if len(instance) > schema.get("maxProperties", math.inf):
            fail(f"{path}: too many properties")

    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            fail(f"{path}: too few items")
        if len(instance) > schema.get("maxItems", math.inf):
            fail(f"{path}: too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(encoded) != len(set(encoded)):
                fail(f"{path}: items must be unique")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, value in enumerate(instance):
                validate(value, item_schema, root, f"{path}[{index}]")

    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            fail(f"{path}: string is shorter than minLength")
        if len(instance) > schema.get("maxLength", math.inf):
            fail(f"{path}: string is longer than maxLength")
        pattern = schema.get("pattern")
        if pattern is not None:
            try:
                matched = re.search(pattern, instance)
            except re.error as exc:
                fail(f"{path}: invalid pattern in pinned schema: {exc}")
            if matched is None:
                fail(f"{path}: string does not match pattern {pattern!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if instance < schema.get("minimum", -math.inf):
            fail(f"{path}: value is below minimum")
        if instance > schema.get("maximum", math.inf):
            fail(f"{path}: value is above maximum")
        if instance <= schema.get("exclusiveMinimum", -math.inf):
            fail(f"{path}: value is not above exclusiveMinimum")
        if instance >= schema.get("exclusiveMaximum", math.inf):
            fail(f"{path}: value is not below exclusiveMaximum")


def validate_sddc_first(output_dir: Path, installer_schema_path: Path) -> dict[str, Any]:
    # This is deliberately the first grading operation: validate the pure artifact
    # against the installer's pinned SddcSpec before any project-specific checks.
    installer = load_json(installer_schema_path)
    sddc = load_json(output_dir / "sddc-spec.json")
    schema = resolve_pointer(installer, "#/components/schemas/SddcSpec")
    validate(sddc, schema, installer)
    return sddc


def expect(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        fail(f"{label}: expected {expected!r}, got {actual!r}")


def require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"{label}: expected object")
    return value


def require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        fail(f"{label}: expected array")
    return value


def check_symbolic_secrets(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if "password" in key.lower():
                if not isinstance(child, str) or re.fullmatch(r"\$\{[A-Z][A-Z0-9_]*\}", child) is None:
                    fail(f"{child_path}: credentials must be symbolic secret references")
            check_symbolic_secrets(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            check_symbolic_secrets(child, f"{path}[{index}]")


def check_sddc(sddc: dict[str, Any], snapshot: dict[str, Any]) -> None:
    expect(sddc.get("sddcId"), "chi1-m01", "sddcId")
    expect(sddc.get("workflowType"), "VCF", "workflowType")
    expect(sddc.get("version"), snapshot["targetRelease"], "SDDC version")
    expect(sddc.get("vcfInstanceName"), "central-vcf", "VCF instance name")

    expected_hosts = [f"chi1-esx{number:02d}" for number in range(1, 7)]
    hosts = require_list(sddc.get("hostSpecs"), "hostSpecs")
    expect([require_mapping(host, "host").get("hostname") for host in hosts], expected_hosts,
           "management-domain hosts")

    dns = require_mapping(sddc.get("dnsSpec"), "dnsSpec")
    expect(dns.get("subdomain"), "corp.example", "DNS subdomain")
    expect(dns.get("nameservers"), ["10.20.0.10", "10.20.0.11"], "DNS servers")
    expect(sddc.get("ntpServers"), ["10.20.0.20", "10.20.0.21"], "NTP servers")

    expected_networks = {
        "MANAGEMENT": (110, "10.20.10.0/24", "10.20.10.1", "10.20.10.31", "10.20.10.36"),
        "VMOTION": (120, "10.20.20.0/24", "10.20.20.1", "10.20.20.31", "10.20.20.36"),
        "VSAN": (130, "10.20.30.0/24", "10.20.30.1", "10.20.30.31", "10.20.30.36"),
        "VM_MANAGEMENT": (140, "10.20.40.0/24", "10.20.40.1", "10.20.40.20", "10.20.40.39"),
        "FLEET_MANAGEMENT": (150, "10.20.50.0/24", "10.20.50.1", "10.20.50.20", "10.20.50.31"),
    }
    networks = require_list(sddc.get("networkSpecs"), "networkSpecs")
    by_type = {require_mapping(item, "network").get("networkType"): item for item in networks}
    expect(set(by_type), set(expected_networks), "network types")
    for network_type, expected in expected_networks.items():
        item = by_type[network_type]
        expect(item.get("vlanId"), expected[0], f"{network_type} VLAN")
        expect(item.get("subnet"), expected[1], f"{network_type} subnet")
        expect(item.get("gateway"), expected[2], f"{network_type} gateway")
        expect(item.get("subnetMask"), "255.255.255.0", f"{network_type} subnet mask")
        expect(item.get("mtu"), 9000, f"{network_type} MTU")
        ranges = require_list(item.get("includeIpAddressRanges"), f"{network_type} ranges")
        expect(ranges, [{"startIpAddress": expected[3], "endIpAddress": expected[4]}],
               f"{network_type} IP range")

    dvs_specs = require_list(sddc.get("dvsSpecs"), "dvsSpecs")
    expect(len(dvs_specs), 1, "DVS count")
    dvs = require_mapping(dvs_specs[0], "DVS")
    expect(dvs.get("mtu"), 9000, "DVS MTU")
    expect(set(dvs.get("networks", [])), set(expected_networks), "DVS networks")
    expect(dvs.get("vmnicsToUplinks"), [
        {"id": "vmnic0", "uplink": "uplink1"},
        {"id": "vmnic1", "uplink": "uplink2"},
    ], "DVS uplink mapping")

    vcenter = require_mapping(sddc.get("vcenterSpec"), "vcenterSpec")
    expect(vcenter.get("vcenterHostname"), "chi1-vc01.corp.example", "vCenter hostname")
    expect(vcenter.get("version"), "9.1.0.0.25370922", "vCenter version")
    expect(vcenter.get("useExistingDeployment"), False, "greenfield vCenter flag")

    manager = require_mapping(sddc.get("sddcManagerSpec"), "sddcManagerSpec")
    expect(manager.get("hostname"), "chi1-sddc01.corp.example", "SDDC Manager hostname")
    expect(manager.get("version"), "9.1.0.0", "SDDC Manager version")
    expect(manager.get("useExistingDeployment"), False, "greenfield SDDC Manager flag")

    nsx = require_mapping(sddc.get("nsxtSpec"), "nsxtSpec")
    expect(nsx.get("vipFqdn"), "chi1-nsx.corp.example", "NSX VIP")
    expect(nsx.get("version"), "9.1.0.0.25318225", "NSX version")
    expect(nsx.get("useExistingDeployment"), False, "greenfield NSX flag")
    expect([node.get("hostname") for node in require_list(nsx.get("nsxtManagers"), "NSX managers")],
           [f"chi1-nsx{number:02d}.corp.example" for number in range(1, 4)], "NSX managers")

    datastore = require_mapping(sddc.get("datastoreSpec"), "datastoreSpec")
    vsan = require_mapping(datastore.get("vsanSpec"), "vSAN spec")
    expect(vsan.get("failuresToTolerate"), 1, "vSAN failures-to-tolerate")
    esa = require_mapping(vsan.get("esaConfig"), "vSAN ESA config")
    expect(esa.get("enabled"), True, "vSAN ESA enabled")

    operations = require_mapping(sddc.get("vcfOperationsSpec"), "VCF Operations spec")
    expect(operations.get("version"), "9.1.0.0", "VCF Operations version")
    expect(operations.get("loadBalancerFqdn"), "chi1-ops.corp.example", "VCF Operations VIP")
    expect(operations.get("useExistingDeployment"), False, "greenfield VCF Operations flag")
    expect([node.get("hostname") for node in require_list(operations.get("nodes"), "VCF Operations nodes")],
           [f"chi1-ops{number:02d}.corp.example" for number in range(1, 4)], "VCF Operations nodes")

    license_server = require_mapping(sddc.get("licenseServerSpec"), "License Server spec")
    expect(license_server.get("hostname"), "chi1-lic01.corp.example", "License Server hostname")
    expect(license_server.get("version"), "9.1.0.0", "License Server version")
    expect(license_server.get("useExistingDeployment"), False, "greenfield License Server flag")

    infrastructure = require_mapping(
        sddc.get("vcfManagementComponentsInfrastructureSpec"), "management infrastructure")
    local_network = require_mapping(infrastructure.get("localRegionNetwork"), "local region network")
    expect(local_network, {
        "networkName": "FLEET_MANAGEMENT",
        "subnetMask": "255.255.255.0",
        "gateway": "10.20.50.1",
    }, "management services network")
    check_symbolic_secrets(sddc)


def check_topology(decision: dict[str, Any]) -> None:
    expected_scalars = {
        "schemaVersion": "1.0",
        "selectedTopology": "SINGLE_SITE_PRIMARY",
        "primarySite": "CHI1",
        "recoverySite": "OMA1",
        "recoveryPattern": "ASYNC_BACKUP_RESTORE",
        "selectedHostCount": 6,
        "licensedCoresAvailable": 192,
        "licensedCoresUsed": 192,
        "rpoMinutes": 15,
        "rtoHours": 4,
    }
    for key, expected in expected_scalars.items():
        expect(decision.get(key), expected, f"topology decision {key}")
    expect(decision.get("selectedHosts"), [f"chi1-esx{number:02d}" for number in range(1, 7)],
           "selected topology hosts")
    expect(decision.get("survivingCapacity"), {
        "cores": 160,
        "memoryGiB": 2560,
        "usableStorageTiB": 25
    }, "N+1 surviving capacity")
    expect(decision.get("requirements"), {
        "cores": 120,
        "memoryGiB": 2048,
        "usableStorageTiB": 24
    }, "capacity requirements")
    rejected = require_list(decision.get("rejectedTopologies"), "rejected topologies")
    expect(rejected, [{
        "topology": "STRETCHED_MANAGEMENT",
        "reasonCode": "ENTITLEMENT_CORE_LIMIT",
        "requiredLicensedCores": 256
    }], "licensing topology rejection")


def check_migration(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    expect(plan.get("schemaVersion"), "1.0", "migration schemaVersion")
    expect(plan.get("estateId"), inventory.get("estateId"), "migration estateId")
    expect(plan.get("targetRelease"), snapshot.get("targetRelease"), "migration target release")

    inventory_items = {item["componentId"]: item for item in inventory["components"]}
    authority_items = {
        item["componentId"]: item for item in snapshot["migration"]["components"]
    }
    steps = require_list(plan.get("steps"), "migration steps")
    expect(len(steps), len(inventory_items), "migration step count")
    expect([step.get("order") for step in steps], list(range(1, len(steps) + 1)),
           "migration order sequence")
    step_items = {step.get("componentId"): step for step in steps}
    expect(set(step_items), set(inventory_items), "migration component coverage")

    for component_id, source in inventory_items.items():
        step = require_mapping(step_items[component_id], f"step {component_id}")
        authority = authority_items[component_id]
        expect(step.get("componentName"), source["name"], f"{component_id} name")
        expect(step.get("currentVersion"), source["version"], f"{component_id} current version")
        expect(step.get("targetVersion"), authority["targetVersion"], f"{component_id} target")
        expect(step.get("action"), authority["action"], f"{component_id} action")
        expect(set(step.get("gates", [])), set(authority["requiredGates"]), f"{component_id} gates")

    positions = {step["componentId"]: step["order"] for step in steps}
    for before, after in snapshot["migration"]["precedence"]:
        if positions[before] >= positions[after]:
            fail(f"migration precedence violated: {before} must precede {after}")


def check_research(path: Path) -> None:
    text = load_text(path)
    source_entries = re.findall(r"(?ms)^-\s+.*?(?=^-\s+|\Z)", text)
    if len(source_entries) < 3:
        fail("research-consulted.md must contain at least three source bullets")

    urls: list[str] = []
    for index, entry in enumerate(source_entries, start=1):
        url_match = re.search(r"https://[^\s)>]+", entry)
        if url_match is None:
            fail(f"research source {index}: missing HTTPS URL")
        url = url_match.group(0).rstrip(".,;—-")
        urls.append(url)
        if re.search(r"\b20\d{2}-\d{2}-\d{2}\b", entry) is None:
            fail(f"research source {index}: missing ISO access date")

        title = re.sub(r"[*_#`\[\]()—-]", " ", entry[:url_match.start()])
        if len(re.findall(r"[A-Za-z0-9]+", title)) < 3:
            fail(f"research source {index}: missing page title")
        fact = entry[url_match.end():]
        if len(re.findall(r"[A-Za-z0-9]+", fact)) < 6:
            fail(f"research source {index}: missing compatibility or upgrade-path fact")

    if len(set(urls)) != len(urls):
        fail("research source URLs must be unique")
    if any(re.search(r"(?:\.invalid(?:/|$)|localhost|127\.0\.0\.1)", url) for url in urls):
        fail("research sources must be real public URLs, not fixtures or placeholders")
    if not any(re.search(r"github\.com/vmware/vcf-api-specs/tree/9\.1\.0\.0(?:/|$)", url)
               for url in urls):
        fail("research must include the upstream vcf-api-specs 9.1.0.0 tag")
    broadcom_urls = [url for url in urls if re.search(r"(?:^|\.)broadcom\.com(?:/|$)", url)]
    if len(broadcom_urls) < 2:
        fail("research must include Broadcom-published compatibility and upgrade sources")

    lowered = text.lower()
    if "upgrade" not in lowered:
        fail("research must record an upgrade-path fact")
    if not any(term in lowered for term in ("compatib", "supported", "interoperab", "latency")):
        fail("research must record a support or compatibility fact")


def main() -> int:
    if len(sys.argv) != 7:
        print("usage: verify.py OUTPUT INVENTORY SNAPSHOT PLAN_SCHEMA INSTALLER_SCHEMA RESEARCH",
              file=sys.stderr)
        return 2
    output_dir, inventory_path, snapshot_path, plan_schema_path, installer_schema_path, research_path = map(
        Path, sys.argv[1:])

    try:
        sddc = validate_sddc_first(output_dir, installer_schema_path)

        # Project-specific checks begin only after SddcSpec schema validation succeeds.
        inventory = load_json(inventory_path)
        snapshot = load_json(snapshot_path)
        decision = load_json(output_dir / "topology-decision.json")
        plan = load_json(output_dir / "migration-plan.json")
        plan_schema = load_json(plan_schema_path)
        validate(plan, plan_schema, plan_schema)

        actual_entries = sorted(path.name for path in output_dir.iterdir())
        expect(actual_entries, ["migration-plan.json", "sddc-spec.json", "topology-decision.json"],
               "generated architecture files")
        check_sddc(sddc, snapshot)
        check_topology(require_mapping(decision, "topology decision"))
        check_migration(require_mapping(plan, "migration plan"), inventory, snapshot)
        check_research(research_path)
    except VerificationError as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        return 1

    print("verification passed: installer schema, greenfield architecture, topology, migration plan, "
          "and research record")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
