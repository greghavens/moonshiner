#!/usr/bin/env python3
"""Protected deterministic semantic checks for the VCF migration artifact."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from verify_schema import SchemaError, validate


ROOT = Path(__file__).resolve().parents[1]


def load(relative: str) -> Any:
    path = ROOT / relative
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssertionError(f"cannot load {relative}: {exc}") from exc


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def by_id(items: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        item_id = item["id"]
        check(item_id not in result, f"duplicate {label} id {item_id!r}")
        result[item_id] = item
    return result


def verify_plan_schema(plan: dict[str, Any]) -> None:
    schema = load("architecture/migration-plan.schema.json")
    validate(plan, schema, schema)


def verify_identity_and_path(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    check(plan["planSchemaVersion"] == 1, "wrong plan schema version")
    check(plan["estateId"] == inventory["estateId"], "estateId does not match inventory")
    check(plan["sddcId"] == inventory["domain"]["id"], "sddcId does not match domain")
    check(plan["sourceVcfVersion"] == inventory["vcfVersion"] == snapshot["sourceVcfVersion"], "source VCF version mismatch")
    check(plan["targetVcfVersion"] == inventory["targetVcfVersion"] == snapshot["targetVcfVersion"], "target VCF version mismatch")
    check(plan["version"] == snapshot["targetVcfVersion"], "SddcSpec version is not the target VCF release")
    check(plan["vcfUpgradePath"] in snapshot["supportedVcfPaths"], "VCF release path is not pinned as supported")
    check(plan["vcfUpgradePath"] == [inventory["vcfVersion"], inventory["targetVcfVersion"]], "plan must use the direct supported hop")
    forbidden = set(snapshot["forbiddenIntermediateReleases"])
    check(not forbidden.intersection(plan["vcfUpgradePath"]), "plan contains a forbidden 9.0 intermediate release")


def verify_topology(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    expected = snapshot["minimumTopology"]
    check(plan["topology"] == expected, "topology must exactly retain the pinned minimum consolidated design")
    check(inventory["deploymentModel"] == expected["deploymentModel"], "inventory deployment model drifted")
    check(inventory["site"]["availabilityZones"] == expected["availabilityZones"], "inventory availability-zone count drifted")
    hosts = inventory["domain"]["cluster"]["hosts"]
    check(len(hosts) == expected["hostCount"] == 4, "fixture must remain at the four-host minimum")
    check(inventory["domain"]["cluster"]["storage"] == expected["storage"], "fixture must use vSAN")

    target_hosts = [item["hostname"] for item in plan["hostSpecs"]]
    source_hosts = [item["hostname"] for item in hosts]
    check(target_hosts == source_hosts, "target hostSpecs must retain all four inventoried hosts in fixture order")
    check(len(set(target_hosts)) == 4, "target host names must be unique")
    check(plan["clusterSpec"]["clusterName"] == inventory["domain"]["cluster"]["name"], "cluster name changed")
    check(plan["clusterSpec"]["datacenterName"] == inventory["domain"]["cluster"]["datacenter"], "datacenter name changed")
    check(isinstance(plan["datastoreSpec"].get("vsanSpec"), dict), "target datastoreSpec must retain vSAN storage")

    check(plan["dnsSpec"] == inventory["infrastructure"]["dns"], "DNS design does not match inventory")
    check(plan["ntpServers"] == inventory["infrastructure"]["ntpServers"], "NTP design does not match inventory")
    expected_networks = inventory["infrastructure"]["networks"]
    check(plan["networkSpecs"] == expected_networks, "target networkSpecs must retain all inventoried networks")
    check({n["networkType"] for n in plan["networkSpecs"]} == {"MANAGEMENT", "VMOTION", "VSAN"}, "target networks must be management, vMotion, and vSAN")
    check(plan["dvsSpecs"], "target design must define at least one distributed switch")
    carried_networks = {
        network
        for dvs in plan["dvsSpecs"]
        for network in dvs.get("networks", [])
    }
    check(carried_networks == {"MANAGEMENT", "VMOTION", "VSAN"}, "distributed switches must carry every retained target network")


def verify_sddc_components(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    targets = snapshot["componentTargets"]
    inv = by_id(inventory["components"], "inventory component")

    check(plan["vcenterSpec"]["vcenterHostname"] == inv["vcenter-server"]["fqdn"], "vCenter FQDN changed")
    check(plan["vcenterSpec"].get("useExistingDeployment") is True, "vCenter must remain an existing deployment")
    check(plan["vcenterSpec"]["version"] == targets["vcenter-server"], "vCenter target build mismatch")
    check(plan["nsxtSpec"].get("useExistingDeployment") is True, "NSX must remain an existing deployment")
    check(plan["nsxtSpec"]["version"] == targets["nsx-manager"], "NSX target build mismatch")
    check(plan["sddcManagerSpec"].get("useExistingDeployment") is True, "SDDC Manager must remain an existing deployment")
    check(plan["sddcManagerSpec"]["version"] == targets["sddc-manager"], "SDDC Manager target build mismatch")
    check(plan["vcfOperationsSpec"].get("useExistingDeployment") is True, "VCF Operations must be upgraded in place")
    check(plan["vcfOperationsSpec"]["version"] == targets["vcf-operations"], "VCF Operations target mismatch")
    check(plan["licenseServerSpec"].get("useExistingDeployment") is False, "new license server must be deployed")
    check(plan["licenseServerSpec"]["version"] == targets["license-server"], "license server target mismatch")

    mgmt = inventory["infrastructure"]["managementServices"]
    check(len(mgmt["reservedIps"]) == 12, "fixture must retain twelve management-services IPs")
    check(plan["vcfManagementComponentsInfrastructureSpec"].get("localRegionNetwork") == {
        "networkName": mgmt["networkName"],
        "subnetMask": mgmt["subnetMask"],
        "gateway": mgmt["gateway"],
    }, "VCF Management Services network does not match the reserved fixture network")

    encoded = json.dumps(plan)
    check("${VC_ROOT_PASS}" in encoded, "SddcSpec must use an explicit vCenter password placeholder")
    check("VMware1!" not in encoded and "Password123" not in encoded, "do not place example credentials in the design")


def verify_component_plan(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    inv = by_id(inventory["components"], "inventory component")
    components = by_id(plan["components"], "planned component")
    targets = snapshot["componentTargets"]
    requirements = snapshot["componentGateRequirements"]
    catalog = snapshot["gateCatalog"]
    check(set(components) == set(inv) == set(targets) == set(requirements), "component plan must cover every fixture and target component exactly once")

    for component_id, source in inv.items():
        item = components[component_id]
        check(item["name"] == source["name"], f"{component_id}: name mismatch")
        check(item["sourceVersion"] == source["version"], f"{component_id}: source version mismatch")
        check(item["targetVersion"] == targets[component_id], f"{component_id}: target version mismatch")
        expected_action = "upgrade" if source["installed"] else "deploy"
        check(item["action"] == expected_action, f"{component_id}: action must be {expected_action}")
        gates = by_id(item["gates"], f"{component_id} gate")
        check(set(gates) == set(requirements[component_id]), f"{component_id}: gates do not match pinned requirements")
        for gate_id, gate in gates.items():
            check(gate["condition"] == catalog[gate_id], f"{component_id}: gate {gate_id} condition drifted")


def verify_stages(plan: dict[str, Any], snapshot: dict[str, Any]) -> None:
    stages = plan["stages"]
    orders = [stage["order"] for stage in stages]
    check(orders == sorted(orders) and len(orders) == len(set(orders)), "stages must be strictly ordered")
    check(len({stage["id"] for stage in stages}) == len(stages), "stage ids must be unique")

    placement: dict[str, int] = {}
    for stage in stages:
        check(stage["entryGates"] and stage["exitGates"], f"stage {stage['id']} must define entry and exit gates")
        for component_id in stage["componentIds"]:
            check(component_id not in placement, f"component {component_id} occurs in more than one stage")
            placement[component_id] = stage["order"]

    components = by_id(plan["components"], "planned component")
    check(set(placement) == set(components), "every component must occur in exactly one ordered stage")
    for component_id, component in components.items():
        check(component["stage"] == placement[component_id], f"{component_id}: component stage disagrees with ordered stages")
    for rule in snapshot["sequenceConstraints"]:
        check(placement[rule["before"]] < placement[rule["after"]], f"ordering constraint violated: {rule['before']} before {rule['after']}")
    for group in snapshot["sameStageComponents"]:
        check(len({placement[item] for item in group}) == 1, f"components must share a rolling stage: {group}")


def verify_stdlib_and_reproducibility() -> None:
    package = ROOT / "vcfarch"
    check((package / "__main__.py").is_file(), "vcfarch package has no executable __main__.py")
    python_files = sorted(package.glob("*.py"))
    check(python_files, "vcfarch package is missing")
    stdlib = set(sys.stdlib_module_names)
    for path in python_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                check(name in stdlib or name == "vcfarch", f"third-party import {name!r} in {path.relative_to(ROOT)}")

    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temp_dir:
        generated = Path(temp_dir) / "migration-plan.json"
        command = [
            sys.executable,
            "-m",
            "vcfarch",
            "--inventory",
            "fixtures/estate_inventory.json",
            "--compatibility",
            "compatibility/vcf-9.1-compatibility-snapshot.json",
            "--output",
            str(generated),
        ]
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=30, check=False)
        check(completed.returncode == 0, f"planner CLI failed:\nstdout={completed.stdout}\nstderr={completed.stderr}")
        expected = (ROOT / "architecture/migration-plan.json").read_bytes()
        check(generated.read_bytes() == expected, "planner CLI does not reproduce the checked-in artifact byte for byte")


def verify_research_record() -> None:
    record = load("research/sources.json")
    check(isinstance(record, dict), "research/sources.json must contain a JSON object")
    sources = record.get("sources")
    check(isinstance(sources, list) and len(sources) >= 2, "research record must contain at least two consulted sources")
    seen_urls: set[str] = set()
    for index, source in enumerate(sources):
        check(isinstance(source, dict), f"research source {index} must be an object")
        for field in ("title", "url", "accessedAt", "finding"):
            check(isinstance(source.get(field), str) and source[field].strip(), f"research source {index} has no non-empty {field}")
        parsed = urlparse(source["url"])
        check(parsed.scheme == "https" and bool(parsed.hostname), f"research source {index} must use an absolute HTTPS URL")
        hostname = parsed.hostname.lower()
        check(hostname != "localhost" and not hostname.endswith(".invalid"), f"research source {index} is not a real public source")
        check(source["url"] not in seen_urls, f"duplicate research source URL {source['url']!r}")
        seen_urls.add(source["url"])


def main() -> int:
    try:
        plan = load("architecture/migration-plan.json")
        inventory = load("fixtures/estate_inventory.json")
        snapshot = load("compatibility/vcf-9.1-compatibility-snapshot.json")
        verify_plan_schema(plan)
        verify_identity_and_path(plan, inventory, snapshot)
        verify_topology(plan, inventory, snapshot)
        verify_sddc_components(plan, inventory, snapshot)
        verify_component_plan(plan, inventory, snapshot)
        verify_stages(plan, snapshot)
        verify_research_record()
        verify_stdlib_and_reproducibility()
    except (AssertionError, SchemaError, KeyError, TypeError, ValueError) as exc:
        print(f"FAIL: migration plan verification: {exc}", file=sys.stderr)
        return 1
    print("PASS: migration architecture matches fixture and pinned compatibility snapshot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
