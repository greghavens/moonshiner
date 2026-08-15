"""Deterministic verifier for the VCF brownfield architecture artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .jsonschema import SchemaError, validate_file


class VerificationError(ValueError):
    """Raised when the architecture disagrees with protected seed inputs."""


PACKAGE_ROOT = Path(__file__).resolve().parent.parent
INSTALLER_SPEC = PACKAGE_ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
PLAN_SCHEMA = PACKAGE_ROOT / "schemas" / "brownfield-migration-plan.schema.json"
INVENTORY = PACKAGE_ROOT / "fixtures" / "estate.json"
SNAPSHOT = PACKAGE_ROOT / "authority" / "compatibility-snapshot.json"

PROTECTED_SHA256 = {
    INSTALLER_SPEC: "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d",
    PLAN_SCHEMA: "53d0d2561fd3a495f69123a9e30cc0978f693b6d8338c218a772a551fdc99106",
    INVENTORY: "6f647b620cdfcecb199826deab17ae16e07fe6afff11ee04b636f9cc3593a68d",
    SNAPSHOT: "f240d8e0ade28f0ec7259ee97e65735b384f326a53b3bc6d275d0fc04639aa0f",
}


def _read_json(path: Path) -> Any:
    if not path.is_file():
        raise VerificationError(f"missing required JSON file: {path}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError(f"cannot read JSON from {path}: {exc}") from exc


def _assert_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise VerificationError(f"{label}: expected {expected!r}, got {actual!r}")


def _edge_matches(source: str, pattern: str) -> bool:
    source_parts = source.split(".")
    pattern_parts = pattern.split(".")
    return len(source_parts) == len(pattern_parts) and all(
        expected == "x" or actual == expected
        for actual, expected in zip(source_parts, pattern_parts)
    )


def _network_design(profile: dict[str, Any], vsan_vlan: int) -> dict[str, int]:
    return {
        "minimumNicGbps": profile["network"]["minimumNicGbps"],
        "uplinksPerHost": profile["network"]["uplinksPerHost"],
        "mtu": profile["network"]["mtu"],
        "vsanVlanId": vsan_vlan,
    }


def _check_protected_inputs() -> None:
    for path, expected in PROTECTED_SHA256.items():
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise VerificationError(f"protected input changed: {path.relative_to(PACKAGE_ROOT)}")


def _check_version_path(artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    path = artifact["versionPath"]
    _assert_equal(path[0], inventory["estate"]["vcfVersion"], "versionPath source")
    _assert_equal(path[-1], snapshot["targetVcfVersion"], "versionPath target")
    edges = snapshot["supportedUpgradeEdges"]
    for source, target in zip(path, path[1:]):
        supported = any(_edge_matches(source, edge["fromPattern"]) and target == edge["to"] for edge in edges)
        if not supported:
            raise VerificationError(f"unsupported version hop: {source} -> {target}")


def _check_plan(artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    inventory_by_id = {item["id"]: item for item in inventory["components"]}
    expected_sequence = snapshot["componentSequence"]
    plan = artifact["migrationPlan"]
    _assert_equal(len(plan), len(inventory_by_id), "migrationPlan component count")
    _assert_equal([step["order"] for step in plan], list(range(1, len(plan) + 1)), "migrationPlan order")
    _assert_equal([step["componentId"] for step in plan], [item["id"] for item in expected_sequence], "component sequence")
    if len({step["componentId"] for step in plan}) != len(plan):
        raise VerificationError("migrationPlan repeats a component")

    for step, target in zip(plan, expected_sequence):
        source = inventory_by_id.get(step["componentId"])
        if source is None:
            raise VerificationError(f"unknown component: {step['componentId']}")
        expected_fields = {
            "componentName": source["name"],
            "currentVersion": source["currentVersion"],
            "currentBuild": source["currentBuild"],
            "targetVersion": target["targetVersion"],
            "targetBuild": target["targetBuild"],
            "action": target["action"],
            "dependsOn": target["dependsOn"],
            "gates": target["gates"],
        }
        for field, expected in expected_fields.items():
            _assert_equal(step[field], expected, f"{step['componentId']}.{field}")

        completed = {prior["componentId"] for prior in plan[: step["order"] - 1]}
        missing_dependencies = set(step["dependsOn"]) - completed
        if missing_dependencies:
            raise VerificationError(
                f"{step['componentId']} precedes dependencies {sorted(missing_dependencies)!r}"
            )


def _check_storage(artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    requirement = inventory["capacity"]
    profiles = snapshot["storageProfiles"]
    vsan_site = next(item for item in inventory["site"]["networks"] if item["networkType"] == "VSAN")
    expected_alternatives = []
    for name in ("OSA", "ESA"):
        profile = profiles[name]
        capacity_hosts = math.ceil(requirement["requiredUsableTiB"] / profile["usableCapacityPerHostTiB"])
        target_hosts = max(profile["minimumClusterHosts"], capacity_hosts)
        expected_alternatives.append(
            {
                "profile": name,
                "targetHostCount": target_hosts,
                "fitsPermanentHostLimit": target_hosts <= requirement["maximumPermanentHosts"],
                "network": _network_design(profile, vsan_site["vlanId"]),
                "transition": profile["transitionFromOSA"],
            }
        )

    decision = artifact["storageDecision"]
    _assert_equal(decision["requiredUsableTiB"], requirement["requiredUsableTiB"], "required usable capacity")
    _assert_equal(decision["alternatives"], expected_alternatives, "storage alternatives")
    viable = [item for item in expected_alternatives if item["fitsPermanentHostLimit"]]
    if len(viable) != 1:
        raise VerificationError("pinned scenario must have exactly one storage profile that fits")
    selected = viable[0]
    _assert_equal(decision["selectedProfile"], selected["profile"], "selected storage profile")
    _assert_equal(decision["targetHostCount"], selected["targetHostCount"], "target host count")
    _assert_equal(decision["network"], selected["network"], "selected network design")
    _assert_equal(decision["transition"], selected["transition"], "selected storage transition")

    esa_enabled = artifact.get("datastoreSpec", {}).get("vsanSpec", {}).get("esaConfig", {}).get("enabled")
    _assert_equal(esa_enabled, selected["profile"] == "ESA", "SddcSpec ESA setting")
    _assert_equal(len(artifact.get("hostSpecs", [])), selected["targetHostCount"], "SddcSpec host count")
    _assert_equal(
        [item["hostname"] for item in artifact["hostSpecs"]],
        inventory["targetHostnames"],
        "SddcSpec target hosts",
    )

    network_specs = artifact.get("networkSpecs", [])
    _assert_equal(len(network_specs), len(inventory["site"]["networks"]), "SddcSpec network count")
    network_by_type = {item["networkType"]: item for item in network_specs}
    if len(network_by_type) != len(network_specs):
        raise VerificationError("SddcSpec repeats a network type")
    for site_network in inventory["site"]["networks"]:
        actual = network_by_type.get(site_network["networkType"])
        if actual is None:
            raise VerificationError(f"missing SddcSpec network {site_network['networkType']}")
        for field in ("vlanId", "subnet", "gateway", "subnetMask", "mtu"):
            _assert_equal(actual.get(field), site_network[field], f"{site_network['networkType']}.{field}")
        _assert_equal(
            actual.get("includeIpAddressRanges"),
            [
                {
                    "startIpAddress": site_network["startIpAddress"],
                    "endIpAddress": site_network["endIpAddress"],
                }
            ],
            f"{site_network['networkType']} IP ranges",
        )
    _assert_equal(network_by_type["VSAN"]["mtu"], selected["network"]["mtu"], "vSAN MTU")

    dvs_specs = artifact.get("dvsSpecs", [])
    mappings = [mapping for dvs in dvs_specs for mapping in dvs.get("vmnicsToUplinks", [])]
    _assert_equal(len(mappings), selected["network"]["uplinksPerHost"], "DVS uplink count")
    if len({mapping["id"] for mapping in mappings}) != len(mappings):
        raise VerificationError("DVS repeats a physical vmnic mapping")
    if len({mapping["uplink"] for mapping in mappings}) != len(mappings):
        raise VerificationError("DVS repeats a logical uplink mapping")
    vsan_dvs = [dvs for dvs in dvs_specs if "VSAN" in dvs.get("networks", [])]
    _assert_equal(len(vsan_dvs), 1, "DVS carrying vSAN")
    _assert_equal(vsan_dvs[0].get("mtu"), selected["network"]["mtu"], "vSAN DVS MTU")


def _check_sddc_identity(artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    estate = inventory["estate"]
    site = inventory["site"]
    _assert_equal(artifact["inventoryId"], inventory["inventoryId"], "inventoryId")
    _assert_equal(artifact["sddcId"], estate["sddcId"], "sddcId")
    _assert_equal(artifact["vcfInstanceName"], estate["vcfInstanceName"], "vcfInstanceName")
    _assert_equal(artifact["targetVcfVersion"], snapshot["targetVcfVersion"], "targetVcfVersion")
    _assert_equal(artifact.get("version"), snapshot["targetVcfVersion"], "SddcSpec version")
    _assert_equal(artifact.get("workflowType"), "VCF", "workflowType")
    _assert_equal(artifact["clusterSpec"]["datacenterName"], estate["datacenterName"], "datacenterName")
    _assert_equal(artifact["clusterSpec"]["clusterName"], estate["clusterName"], "clusterName")
    _assert_equal(artifact["dnsSpec"]["subdomain"], site["dnsSubdomain"], "DNS subdomain")
    _assert_equal(artifact["dnsSpec"].get("nameservers"), site["dnsServers"], "DNS servers")
    _assert_equal(artifact.get("ntpServers"), site["ntpServers"], "NTP servers")
    _assert_equal(artifact["vcenterSpec"]["vcenterHostname"], site["vcenterHostname"], "vCenter hostname")
    _assert_equal(artifact["vcenterSpec"].get("version"), snapshot["targetVcfVersion"], "vCenter version")
    _assert_equal(artifact["vcenterSpec"].get("useExistingDeployment"), True, "vCenter reuse setting")
    _assert_equal(artifact["sddcManagerSpec"]["hostname"], site["sddcManagerHostname"], "SDDC Manager hostname")
    _assert_equal(artifact["sddcManagerSpec"].get("version"), snapshot["targetVcfVersion"], "SDDC Manager version")
    _assert_equal(artifact["sddcManagerSpec"].get("useExistingDeployment"), True, "SDDC Manager reuse setting")
    _assert_equal(
        [item.get("hostname") for item in artifact["nsxtSpec"]["nsxtManagers"]],
        site["nsxManagerHostnames"],
        "NSX Manager hostnames",
    )
    _assert_equal(artifact["nsxtSpec"]["vipFqdn"], site["nsxVipFqdn"], "NSX VIP")
    _assert_equal(artifact["nsxtSpec"].get("version"), snapshot["targetVcfVersion"], "NSX version")
    _assert_equal(artifact["nsxtSpec"].get("useExistingDeployment"), True, "NSX reuse setting")


def verify_file(artifact_path: Path | str) -> None:
    """Validate an architecture. Research notes are intentionally not read."""

    artifact_path = Path(artifact_path)
    artifact = _read_json(artifact_path)

    # Phase 1 is deliberately first: the artifact must satisfy VMware's own
    # tagged SddcSpec before seed-specific schema or compatibility checks run.
    try:
        validate_file(artifact, INSTALLER_SPEC, "#/components/schemas/SddcSpec")
    except SchemaError as exc:
        raise VerificationError(f"installer SddcSpec validation failed: {exc}") from exc

    _check_protected_inputs()
    try:
        validate_file(artifact, PLAN_SCHEMA)
    except SchemaError as exc:
        raise VerificationError(f"brownfield plan schema validation failed: {exc}") from exc

    inventory = _read_json(INVENTORY)
    snapshot = _read_json(SNAPSHOT)
    _check_sddc_identity(artifact, inventory, snapshot)
    _check_version_path(artifact, inventory, snapshot)
    _check_plan(artifact, inventory, snapshot)
    _check_storage(artifact, inventory, snapshot)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", nargs="?", default="architecture.json", type=Path)
    args = parser.parse_args(argv)
    try:
        verify_file(args.artifact)
    except VerificationError as exc:
        parser.exit(1, f"verification failed: {exc}\n")
    print("architecture verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
