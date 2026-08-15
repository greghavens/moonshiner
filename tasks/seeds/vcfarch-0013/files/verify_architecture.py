#!/usr/bin/env python3
"""Protected, offline acceptance verifier for the VCF architecture artifact."""

from __future__ import annotations

import ast
import datetime
import ipaddress
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
OPENAPI_PATH = ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
GREENFIELD_PATH = ROOT / "architecture" / "greenfield-sddc.json"
MIGRATION_PATH = ROOT / "architecture" / "migration-plan.json"
RESEARCH_PATH = ROOT / "research.md"


class VerificationError(AssertionError):
    pass


def fail(message: str) -> None:
    raise VerificationError(message)


def load_json(path: Path, label: str) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        fail(f"{label} is missing: {path.relative_to(ROOT)}")
    except json.JSONDecodeError as exc:
        fail(f"{label} is not valid JSON: {exc}")


class SchemaValidator:
    """Small stdlib validator for the JSON Schema keywords used by the pins."""

    def __init__(self, document: dict[str, Any]):
        self.document = document

    def _resolve(self, reference: str) -> dict[str, Any]:
        if not reference.startswith("#/"):
            fail(f"unsupported non-local schema reference {reference!r}")
        node: Any = self.document
        for raw in reference[2:].split("/"):
            key = raw.replace("~1", "/").replace("~0", "~")
            try:
                node = node[key]
            except (KeyError, TypeError):
                fail(f"unresolvable schema reference {reference!r}")
        if not isinstance(node, dict):
            fail(f"schema reference {reference!r} does not name an object")
        return node

    def validate(self, instance: Any, schema: dict[str, Any], path: str = "$") -> None:
        if "$ref" in schema:
            self.validate(instance, self._resolve(schema["$ref"]), path)
            return

        if "allOf" in schema:
            for child in schema["allOf"]:
                self.validate(instance, child, path)
        if "anyOf" in schema:
            if not any(self._valid(instance, child, path) for child in schema["anyOf"]):
                fail(f"{path} does not match any allowed schema")
        if "oneOf" in schema:
            matches = sum(self._valid(instance, child, path) for child in schema["oneOf"])
            if matches != 1:
                fail(f"{path} must match exactly one schema, matched {matches}")
        if "not" in schema and self._valid(instance, schema["not"], path):
            fail(f"{path} matches a forbidden schema")

        if "const" in schema and instance != schema["const"]:
            fail(f"{path} must equal {schema['const']!r}")
        if "enum" in schema and instance not in schema["enum"]:
            fail(f"{path} is not one of {schema['enum']!r}")

        expected_type = schema.get("type")
        if expected_type is not None and not self._is_type(instance, expected_type):
            if not (instance is None and schema.get("nullable") is True):
                fail(f"{path} must have type {expected_type}")

        if isinstance(instance, dict):
            required = schema.get("required", [])
            for name in required:
                if name not in instance:
                    fail(f"{path} is missing required property {name!r}")
            properties = schema.get("properties", {})
            for name, value in instance.items():
                if name in properties:
                    self.validate(value, properties[name], f"{path}.{name}")
                elif schema.get("additionalProperties") is False:
                    fail(f"{path} has unexpected property {name!r}")
                elif isinstance(schema.get("additionalProperties"), dict):
                    self.validate(value, schema["additionalProperties"], f"{path}.{name}")
            if "minProperties" in schema and len(instance) < schema["minProperties"]:
                fail(f"{path} has too few properties")
            if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
                fail(f"{path} has too many properties")

        if isinstance(instance, list):
            if "minItems" in schema and len(instance) < schema["minItems"]:
                fail(f"{path} has too few items")
            if "maxItems" in schema and len(instance) > schema["maxItems"]:
                fail(f"{path} has too many items")
            if schema.get("uniqueItems"):
                encoded = [json.dumps(item, sort_keys=True) for item in instance]
                if len(encoded) != len(set(encoded)):
                    fail(f"{path} must contain unique items")
            item_schema = schema.get("items")
            if isinstance(item_schema, dict):
                for index, value in enumerate(instance):
                    self.validate(value, item_schema, f"{path}[{index}]")

        if isinstance(instance, str):
            if "minLength" in schema and len(instance) < schema["minLength"]:
                fail(f"{path} is shorter than {schema['minLength']}")
            if "maxLength" in schema and len(instance) > schema["maxLength"]:
                fail(f"{path} is longer than {schema['maxLength']}")
            if "pattern" in schema and re.search(schema["pattern"], instance) is None:
                fail(f"{path} does not match {schema['pattern']!r}")

        if isinstance(instance, (int, float)) and not isinstance(instance, bool):
            if "minimum" in schema and instance < schema["minimum"]:
                fail(f"{path} is below minimum {schema['minimum']}")
            if "maximum" in schema and instance > schema["maximum"]:
                fail(f"{path} exceeds maximum {schema['maximum']}")

    def _valid(self, instance: Any, schema: dict[str, Any], path: str) -> bool:
        try:
            self.validate(instance, schema, path)
        except VerificationError:
            return False
        return True

    @staticmethod
    def _is_type(instance: Any, expected: str | list[str]) -> bool:
        if isinstance(expected, list):
            return any(SchemaValidator._is_type(instance, item) for item in expected)
        checks = {
            "object": lambda value: isinstance(value, dict),
            "array": lambda value: isinstance(value, list),
            "string": lambda value: isinstance(value, str),
            "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
            "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": lambda value: isinstance(value, bool),
            "null": lambda value: value is None,
        }
        return expected in checks and checks[expected](instance)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def validate_installer_artifact_first() -> tuple[dict[str, Any], dict[str, Any]]:
    """This is deliberately the first acceptance operation in main()."""
    openapi = load_json(OPENAPI_PATH, "pinned VCF Installer OpenAPI document")
    greenfield = load_json(GREENFIELD_PATH, "greenfield SddcSpec artifact")
    try:
        sddc_schema = openapi["components"]["schemas"]["SddcSpec"]
    except (KeyError, TypeError):
        fail("pinned installer specification does not contain components.schemas.SddcSpec")
    SchemaValidator(openapi).validate(greenfield, sddc_schema)
    return openapi, greenfield


def validate_greenfield_semantics(
    artifact: dict[str, Any], requirements: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    workload = requirements["workload_domain"]
    bom = snapshot["greenfield_bom"]
    require(artifact["sddcId"] == workload["sddc_id"], "wrong greenfield sddcId")
    require(artifact.get("version") == requirements["target_version"], "wrong SddcSpec version")

    expected_hosts = [
        f"{workload['host_prefix']}{number:02d}"
        for number in range(1, workload["host_count"] + 1)
    ]
    hostnames = [host.get("hostname") for host in artifact.get("hostSpecs", [])]
    require(hostnames == expected_hosts, "hostSpecs must contain all eight ordered CHI-02 hosts")

    vcenter = artifact["vcenterSpec"]
    expected_vcenter = workload["vcenter"]
    require(vcenter.get("vcenterHostname") == expected_vcenter["hostname"], "wrong vCenter hostname")
    require(vcenter.get("rootVcenterPassword") == expected_vcenter["root_password_placeholder"], "use the fixture-only vCenter placeholder")
    require(vcenter.get("vmSize") == expected_vcenter["vm_size"], "wrong vCenter size")
    require(vcenter.get("storageSize") == expected_vcenter["storage_size"], "wrong vCenter storage size")
    require(vcenter.get("useExistingDeployment") is False, "greenfield vCenter cannot reuse an existing deployment")
    require(vcenter.get("version") == bom["vcenter"], "vCenter is not on the pinned 9.1 BOM")

    cluster = artifact.get("clusterSpec", {})
    require(cluster.get("clusterName") == workload["cluster_name"], "wrong cluster name")
    require(cluster.get("datacenterName") == workload["datacenter_name"], "wrong datacenter name")

    storage = artifact.get("datastoreSpec", {}).get("vsanSpec", {})
    expected_storage = workload["storage"]
    require(storage.get("datastoreName") == expected_storage["datastore_name"], "wrong vSAN datastore")
    require(storage.get("failuresToTolerate") == expected_storage["failures_to_tolerate"], "wrong vSAN FTT")
    require(storage.get("esaConfig", {}).get("enabled") is True, "vSAN ESA must be enabled")

    require(artifact["dnsSpec"] == workload["dns"], "DNS design does not match the site requirement")
    require(artifact.get("ntpServers") == workload["ntp_servers"], "NTP design does not match the site requirement")

    expected_networks = {network["type"]: network for network in workload["networks"]}
    actual_networks = artifact.get("networkSpecs", [])
    require(len(actual_networks) == len(expected_networks), "networkSpecs must contain every required network exactly once")
    require(len({network.get("networkType") for network in actual_networks}) == len(actual_networks), "networkSpecs contains duplicate network types")
    for network in actual_networks:
        kind = network.get("networkType")
        require(kind in expected_networks, f"unexpected network type {kind!r}")
        wanted = expected_networks[kind]
        require(network.get("vlanId") == wanted["vlan_id"], f"wrong VLAN for {kind}")
        require(network.get("subnet") == wanted["subnet"], f"wrong subnet for {kind}")
        require(network.get("gateway") == wanted["gateway"], f"wrong gateway for {kind}")
        require(network.get("mtu") == wanted["mtu"], f"wrong MTU for {kind}")
        ranges = network.get("includeIpAddressRanges")
        require(ranges == [{"startIpAddress": wanted["start_ip"], "endIpAddress": wanted["end_ip"]}], f"wrong address range for {kind}")
        subnet = ipaddress.ip_network(wanted["subnet"])
        require(ipaddress.ip_address(wanted["start_ip"]) in subnet and ipaddress.ip_address(wanted["end_ip"]) in subnet, f"fixture range escapes {kind} subnet")

    expected_switches = workload["distributed_switches"]
    actual_switches = artifact.get("dvsSpecs", [])
    require(len(actual_switches) == len(expected_switches), "wrong number of distributed switches")
    for actual, wanted in zip(actual_switches, expected_switches):
        require(actual.get("dvsName") == wanted["name"], "wrong distributed-switch name")
        require(actual.get("networks") == wanted["networks"], f"wrong networks on {wanted['name']}")
        require(actual.get("mtu") == wanted["mtu"], f"wrong MTU on {wanted['name']}")
        uplink_entries = actual.get("vmnicsToUplinks", [])
        require(len(uplink_entries) == len(wanted["uplinks"]), f"wrong number of uplink mappings on {wanted['name']}")
        mapping = {entry.get("id"): entry.get("uplink") for entry in uplink_entries}
        require(mapping == wanted["uplinks"], f"wrong uplink redundancy on {wanted['name']}")

    nsx = artifact.get("nsxtSpec", {})
    expected_nsx = workload["nsx"]
    require([item.get("hostname") for item in nsx.get("nsxtManagers", [])] == expected_nsx["manager_hostnames"], "wrong NSX manager set")
    require(nsx.get("vipFqdn") == expected_nsx["vip_fqdn"], "wrong NSX VIP")
    require(nsx.get("nsxtManagerSize") == expected_nsx["manager_size"], "wrong NSX manager size")
    require(nsx.get("transportVlanId") == expected_nsx["transport_vlan"], "wrong NSX transport VLAN")
    require(nsx.get("useExistingDeployment") is False, "greenfield NSX cannot reuse an existing deployment")
    require(nsx.get("version") == bom["nsx"], "NSX is not on the pinned 9.1 BOM")

    extension = artifact.get("x-architecture")
    require(isinstance(extension, dict), "SddcSpec must include x-architecture design calculations")
    require(extension.get("purpose") == "GREENFIELD_VI_WORKLOAD_DOMAIN", "wrong architecture purpose")
    require(extension.get("site") == workload["site"], "wrong target site")
    require(extension.get("componentVersions") == bom, "greenfield component versions do not match the pinned BOM")
    require(extension.get("edgeNodeCount") == expected_nsx["edge_node_count"], "wrong edge-node count")
    integration = extension.get("integration", {})
    fleet = requirements["existing_fleet"]
    require(integration.get("operation") == "ADD_VI_WORKLOAD_DOMAIN", "wrong fleet integration operation")
    require(integration.get("fleetId") == fleet["fleet_id"], "wrong existing fleet")
    require(integration.get("managementDomainId") == fleet["management_domain_id"], "wrong management domain")
    require(integration.get("managementDomainChanges") == [], "greenfield design changes the management domain")

    profile = workload["host_profile"]
    host_count = workload["host_count"]
    after_count = host_count - workload["availability"]["host_failures_to_tolerate"]
    require(
        after_count >= workload["availability"]["minimum_hosts_after_failure"],
        "host count does not satisfy the stated N+1 minimum",
    )
    installed = {
        "hosts": host_count,
        "cpuCores": host_count * profile["cpu_cores"],
        "memoryGiB": host_count * profile["memory_gib"],
        "rawStorageTiB": host_count * profile["raw_storage_tib"],
    }
    after_failure = {
        "hosts": after_count,
        "cpuCores": after_count * profile["cpu_cores"],
        "memoryGiB": after_count * profile["memory_gib"],
        "rawStorageTiB": after_count * profile["raw_storage_tib"],
    }
    demand = workload["demand"]
    required_raw = demand["usable_storage_tib"] * expected_storage["protection_overhead_ratio"]
    capacity = extension.get("capacity", {})
    require(capacity.get("installed") == installed, "installed-capacity arithmetic is wrong")
    require(capacity.get("afterSingleHostFailure") == after_failure, "N+1 capacity arithmetic is wrong")
    require(capacity.get("demand") == demand, "capacity demand was not preserved")
    require(capacity.get("requiredRawStorageTiB") == required_raw, "protected-storage arithmetic is wrong")
    require(capacity.get("rawStorageHeadroomTiB") == after_failure["rawStorageTiB"] - required_raw, "storage headroom arithmetic is wrong")
    require(demand["cpu_cores"] <= after_failure["cpuCores"], "CPU demand does not fit after one host failure")
    require(demand["memory_gib"] <= after_failure["memoryGiB"], "memory demand does not fit after one host failure")
    require(required_raw <= after_failure["rawStorageTiB"], "protected storage does not fit after one host failure")
    require(extension.get("availability") == workload["availability"], "availability intent was not preserved")

    allowed_password = expected_vcenter["root_password_placeholder"]

    def validate_passwords(value: Any, path: str = "$") -> None:
        if isinstance(value, dict):
            for name, child in value.items():
                child_path = f"{path}.{name}"
                if "password" in name.lower():
                    require(child == allowed_password, f"{child_path} does not use the fixture-only credential placeholder")
                validate_passwords(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                validate_passwords(child, f"{path}[{index}]")

    validate_passwords(artifact)


def validate_migration_semantics(
    plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    require(plan["estate_id"] == inventory["estate_id"], "migration plan names the wrong estate")
    expected_target = {
        "fleet_id": inventory["target_fleet_id"],
        "workload_domain_id": inventory["target_workload_domain_id"],
        "vcf_version": inventory["target_vcf_version"],
    }
    require(plan["target"] == expected_target, "migration target does not match the inventory")
    require(plan["management_domain"] == {"mode": "UNCHANGED", "changes": []}, "management domain is not immutable")

    components = {item["id"]: item for item in inventory["components"]}
    steps = plan["steps"]
    require(len(steps) == len(components), "migration plan must have exactly one step per inventory component")
    require([step["order"] for step in steps] == list(range(1, len(steps) + 1)), "migration step orders must be contiguous")
    require([step["component_id"] for step in steps] == snapshot["ordered_component_ids"], "migration steps violate the pinned order")
    require(len({step["component_id"] for step in steps}) == len(steps), "migration plan repeats a component")

    known_gates = snapshot["gate_definitions"]
    edge_keys = {
        (edge["component_type"], edge["source"], edge["target"])
        for edge in snapshot["supported_upgrade_edges"]
    }
    for step in steps:
        component_id = step["component_id"]
        require(component_id in components, f"unknown migration component {component_id!r}")
        component = components[component_id]
        authority = snapshot["component_plan"].get(component_id)
        require(authority is not None, f"snapshot lacks component {component_id!r}")
        require(step["component_name"] == component["name"], f"wrong name for {component_id}")
        require(step["component_type"] == component["type"], f"wrong type for {component_id}")
        require(step["scope"] == component["scope"], f"wrong scope for {component_id}")
        require(step["source_version"] == component["version"], f"wrong source version for {component_id}")
        require(step["target_version"] == authority["target"], f"wrong target version for {component_id}")
        require(step["action"] == authority["action"], f"wrong action for {component_id}")
        require(step["gates"] == authority["gates"], f"missing, extra, or reordered gates for {component_id}")
        require(all(gate in known_gates for gate in step["gates"]), f"unknown gate on {component_id}")
        if component["scope"] == "MANAGEMENT_DOMAIN":
            require(step["action"] == "PRESERVE", f"management component {component_id} is mutated")
            require(step["target_version"] == step["source_version"], f"management component {component_id} changes version")
        else:
            edge = (component["type"], component["version"], step["target_version"])
            require(edge in edge_keys, f"unsupported pinned source-to-target edge for {component_id}")

    import_versions = snapshot["supported_import_combination"]
    by_type: dict[str, set[str]] = {}
    for step in steps:
        if step["scope"] == "CANDIDATE_WORKLOAD_DOMAIN":
            by_type.setdefault(step["component_type"], set()).add(step["target_version"])
    require(by_type.get("VCENTER") == {import_versions["vcenter"]}, "vCenter import baseline mismatch")
    require(by_type.get("ESX_HOST") == {import_versions["esxi"]}, "ESXi import baseline mismatch")
    require(by_type.get("VSAN") == {import_versions["vsan"]}, "vSAN import baseline mismatch")
    require(by_type.get("NSX_T_MANAGER") == {import_versions["nsx"]}, "NSX import baseline mismatch")
    require(inventory["target_vcf_version"] == import_versions["target_fleet"], "target fleet is outside pinned combination")
    require(
        plan["outcome"] == {
            "state": "VI_WORKLOAD_DOMAIN_ADDED",
            "fleet_id": inventory["target_fleet_id"],
            "workload_domain_id": inventory["target_workload_domain_id"],
            "management_domain_changes": [],
        },
        "migration outcome is wrong",
    )


def validate_stdlib_package() -> None:
    package_dir = ROOT / "vcf_architecture"
    require(package_dir.is_dir(), "vcf_architecture package is missing")
    python_files = sorted(package_dir.glob("*.py"))
    require(python_files, "vcf_architecture package contains no Python modules")
    local_modules = {path.stem for path in python_files} | {"vcf_architecture"}
    network_modules = {"ftplib", "http.client", "socket", "urllib.request"}
    for path in python_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            fail(f"cannot parse {path.relative_to(ROOT)}: {exc}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module]
            else:
                continue
            for module in modules:
                root = module.split(".")[0]
                require(root in sys.stdlib_module_names or root in local_modules, f"third-party import {root!r} in {path.name}")
                require(module not in network_modules, f"network import {module!r} makes the package non-offline")


def validate_research_record() -> None:
    try:
        text = RESEARCH_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        fail("live-research record is missing: research.md")

    lowered = text.lower()
    require(any(marker in lowered for marker in ("access", "consulted", "retrieved")), "research.md does not identify an access date")
    dates = re.findall(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)", text)
    require(dates, "research.md does not record an ISO access date")
    for value in dates:
        try:
            datetime.date.fromisoformat(value)
        except ValueError:
            fail(f"research.md contains invalid access date {value!r}")

    raw_urls = re.findall(r"https://[^\s<>]+", text)
    urls = [value.rstrip(".,;:!?'\"”’)") for value in raw_urls]
    require(len(set(urls)) >= 2, "research.md must record at least two distinct live sources")
    for url in urls:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        published_by_broadcom = (
            hostname == "broadcom.com"
            or hostname.endswith(".broadcom.com")
            or hostname == "vmware.com"
            or hostname.endswith(".vmware.com")
        )
        require(published_by_broadcom, f"research source is not Broadcom-published: {url}")
        lines = text.splitlines()
        source_index = next(index for index, line in enumerate(lines) if url in line)
        source_line = lines[source_index]
        before_url, _, after_url = source_line.partition(url)
        title_context = " ".join(lines[max(0, source_index - 1) : source_index] + [before_url])
        decision_context = " ".join([after_url] + lines[source_index + 1 : source_index + 2])
        require(len(re.findall(r"[A-Za-z]", title_context)) >= 8, f"research source has no title: {url}")
        require(
            any(marker in decision_context.lower() for marker in ("inform", "decision", "support", "compatib", "interoperab", "sequence", "order", "baseline", "cross-check", "upgrade", "precheck")),
            f"research source has no recorded compatibility or sequencing decision: {url}",
        )

    require(
        any(marker in lowered for marker in ("compatib", "interoperab", "supported", "combination", "baseline")),
        "research.md does not record a compatibility decision",
    )
    require(
        any(marker in lowered for marker in ("sequence", "order", "before", "upgrade path", "precheck")),
        "research.md does not record an upgrade-path or sequencing decision",
    )
    require("8.0" in lowered and "4.2" in lowered, "research.md omits the researched vSphere/NSX import combination")


def validate_package_and_cli(
    greenfield: dict[str, Any], migration: dict[str, Any]
) -> None:
    validate_stdlib_package()
    output_dir = ROOT / ".verify-output"
    if output_dir.exists():
        shutil.rmtree(output_dir)
    command = [
        sys.executable,
        "-m",
        "vcf_architecture",
        "--requirements",
        "fixtures/greenfield-requirements.json",
        "--inventory",
        "fixtures/estate.json",
        "--snapshot",
        "compatibility/compatibility-snapshot.json",
        "--output-dir",
        ".verify-output",
    ]
    try:
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=20)
        require(completed.returncode == 0, f"CLI failed: {completed.stderr.strip() or completed.stdout.strip()}")
        require(load_json(output_dir / "greenfield-sddc.json", "CLI greenfield output") == greenfield, "CLI greenfield output differs")
        require(load_json(output_dir / "migration-plan.json", "CLI migration output") == migration, "CLI migration output differs")
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)


def main() -> int:
    # Binding ordering requirement: installer-schema validation happens first.
    _openapi, greenfield = validate_installer_artifact_first()

    migration = load_json(MIGRATION_PATH, "migration-plan artifact")
    migration_schema = load_json(ROOT / "specifications" / "migration-plan.schema.json", "migration-plan schema")
    SchemaValidator(migration_schema).validate(migration, migration_schema)

    requirements = load_json(ROOT / "fixtures" / "greenfield-requirements.json", "greenfield requirements fixture")
    inventory = load_json(ROOT / "fixtures" / "estate.json", "estate inventory fixture")
    snapshot = load_json(ROOT / "compatibility" / "compatibility-snapshot.json", "compatibility snapshot")

    validate_greenfield_semantics(greenfield, requirements, snapshot)
    validate_migration_semantics(migration, inventory, snapshot)
    validate_research_record()
    validate_package_and_cli(greenfield, migration)
    print("PASS: installer schema, architecture, migration plan, research, compatibility, package, and CLI")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
