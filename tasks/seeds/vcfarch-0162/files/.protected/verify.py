#!/usr/bin/env python3
"""Deterministic offline verifier for the migration architecture seed."""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path.cwd()
ARTIFACT = ROOT / "migration-plan.json"
RESEARCH = ROOT / "research-sources.json"
ESTATE = ROOT / "fixtures" / "estate.json"
SNAPSHOT = ROOT / "fixtures" / "compatibility-snapshot.json"
MANIFEST = ROOT / "VcfAriaMigration" / "VcfAriaMigration.psd1"
MODULE = ROOT / "VcfAriaMigration" / "VcfAriaMigration.psm1"


class VerificationError(Exception):
    pass


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as error:
        raise VerificationError(f"missing required artifact: {path.name}") from error
    except json.JSONDecodeError as error:
        raise VerificationError(
            f"{path.name} is not valid JSON: line {error.lineno}, column {error.colno}"
        ) from error


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def exact_keys(value: Any, expected: list[str], context: str) -> None:
    require(isinstance(value, dict), f"{context} must be an object")
    actual = set(value)
    wanted = set(expected)
    missing = sorted(wanted - actual)
    extra = sorted(actual - wanted)
    require(not missing and not extra, f"{context} keys differ: missing={missing}, extra={extra}")


def required_keys(value: Any, expected: list[str], context: str) -> None:
    require(isinstance(value, dict), f"{context} must be an object")
    missing = sorted(set(expected) - set(value))
    require(not missing, f"{context} is missing required keys: {missing}")


def find_unique(items: list[dict[str, Any]], key: str, value: str, context: str) -> dict[str, Any]:
    matches = [item for item in items if isinstance(item, dict) and item.get(key) == value]
    require(len(matches) == 1, f"{context} must contain exactly one {key}={value!r}")
    return matches[0]


def verify_plan(plan: Any, estate: Any, snapshot: Any, context: str) -> None:
    schema = snapshot["artifactSchema"]
    exact_keys(plan, schema["topLevelKeys"], context)
    require(plan["schemaVersion"] == schema["schemaVersion"], f"{context} schemaVersion does not match snapshot")
    require(
        isinstance(plan["architectureId"], str) and plan["architectureId"].strip(),
        f"{context} architectureId must be nonblank",
    )
    require(plan["estateId"] == estate["estateId"], f"{context} estateId does not match estate fixture")
    require(
        plan["compatibilitySnapshotId"] == snapshot["snapshotId"],
        f"{context} compatibilitySnapshotId does not match",
    )
    require(plan["targetRelease"] == snapshot["targetRelease"], f"{context} targetRelease does not match snapshot")
    require(plan["managementDomainImpact"] == "none", f"{context} management domain impact must be none")

    workload = estate["fleet"]["addedWorkloadDomain"]
    cluster = workload["cluster"]
    stated = plan["addedWorkloadDomain"]
    exact_keys(stated, schema["addedWorkloadDomainKeys"], f"{context} addedWorkloadDomain")
    require(stated["id"] == workload["id"], f"{context} workload domain id does not match fixture")
    require(stated["clusterId"] == cluster["id"], f"{context} workload cluster id does not match fixture")
    require(cluster["hostCount"] == len(cluster["hosts"]), "protected estate hostCount contradicts its host list")
    require(stated["hostCount"] == cluster["hostCount"], f"{context} hostCount contradicts estate hostCount")
    require(
        stated["failuresToTolerate"] == cluster["failuresToTolerate"],
        f"{context} FTT contradicts estate FTT",
    )
    ftt = stated["failuresToTolerate"]
    require(isinstance(ftt, int) and not isinstance(ftt, bool) and ftt >= 0, f"{context} FTT must be a nonnegative integer")
    minimum_hosts = 2 * ftt + 1
    require(stated["minimumHostCount"] == minimum_hosts, f"{context} minimumHostCount must equal 2*FTT+1")
    require(stated["hostCount"] >= minimum_hosts, f"{context} host count cannot satisfy its failures-to-tolerate")

    placements = plan["placements"]
    require(isinstance(placements, list), f"{context} placements must be an array")
    require(len(placements) == len(snapshot["placements"]), f"{context} placement count does not match snapshot")
    capacity_used = {"vCpu": 0, "memoryGiB": 0, "storageGiB": 0}
    placement_ids: set[str] = set()
    for expected in snapshot["placements"]:
        actual = find_unique(placements, "id", expected["id"], f"{context} placements")
        exact_keys(actual, schema["placementKeys"], f"{context} placement {expected['id']}")
        placement_ids.add(actual["id"])
        for key, expected_value in expected.items():
            require(actual.get(key) == expected_value, f"{context} placement {expected['id']} has wrong {key}")
        require(actual["domainId"] == workload["id"], f"{context} placement {expected['id']} is not on added workload domain")
        require(actual["clusterId"] == cluster["id"], f"{context} placement {expected['id']} is not on added workload cluster")
        require(actual["antiAffinity"] is True, f"{context} placement {expected['id']} must use anti-affinity")
        require(actual["nodeCount"] >= ftt + 1, f"{context} placement {expected['id']} node count cannot tolerate FTT")
        require(actual["faultDomains"] >= ftt + 1, f"{context} placement {expected['id']} fault domains cannot tolerate FTT")
        capacity_used["vCpu"] += actual["nodeCount"] * actual["vCpuPerNode"]
        capacity_used["memoryGiB"] += actual["nodeCount"] * actual["memoryGiBPerNode"]
        capacity_used["storageGiB"] += actual["nodeCount"] * actual["storageGiBPerNode"]

    require(len(placement_ids) == len(placements), f"{context} placement ids must be unique")
    available = cluster["availableCapacity"]
    for resource, used in capacity_used.items():
        require(used <= available[resource], f"{context} target placements exceed {resource} capacity")

    waves = plan["waves"]
    rules = snapshot["migrationRules"]
    sources = estate["sourceProducts"]
    require(isinstance(waves, list), f"{context} waves must be an array")
    require(len(waves) == len(rules) == len(sources), f"{context} must have exactly one migration wave per source")
    require(
        [wave.get("order") for wave in waves if isinstance(wave, dict)] == sorted(rule["order"] for rule in rules),
        f"{context} migration waves are not in pinned order",
    )

    source_pairs = {(source["product"], source["version"]) for source in sources}
    wave_pairs = {
        (wave.get("sourceProduct"), wave.get("sourceVersion"))
        for wave in waves
        if isinstance(wave, dict)
    }
    require(wave_pairs == source_pairs, f"{context} waves do not cover every exact source product/version")

    for expected in rules:
        actual = find_unique(waves, "id", expected["id"], f"{context} waves")
        exact_keys(actual, schema["waveKeys"], f"{context} wave {expected['id']}")
        for key in (
            "order", "id", "sourceProduct", "sourceVersion", "legacyName",
            "targetComponent", "targetVersion", "migrationMethod",
            "supportBoundary", "placementId", "carryForward", "abandon",
            "manualRebuild", "gates", "automation",
        ):
            require(actual.get(key) == expected[key], f"{context} wave {expected['id']} has wrong {key}")
        require(actual["placementId"] in placement_ids, f"{context} wave {expected['id']} references unknown placement")
        require(actual["gates"], f"{context} wave {expected['id']} must have gates")
        exact_keys(actual["automation"], schema["automationKeys"], f"{context} wave {expected['id']} automation")

    management_id = estate["fleet"]["managementDomain"]["id"]
    require(
        all(item["domainId"] != management_id for item in placements),
        f"{context} management domain must not host a target",
    )


def verify_research(research: Any, estate: Any) -> None:
    required_keys(research, ["researchedAt", "sources", "liveDiscrepancies"], "research-sources")
    researched_at = research["researchedAt"]
    require(isinstance(researched_at, str) and researched_at.strip(), "researchedAt must be a nonblank ISO timestamp")
    try:
        parsed_time = datetime.fromisoformat(researched_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise VerificationError("researchedAt must be a valid ISO timestamp") from error
    require(parsed_time.utcoffset() is not None, "researchedAt must include a UTC offset")

    sources = research["sources"]
    require(isinstance(sources, list) and sources, "research sources must be a nonempty array")
    searchable_sources: list[str] = []
    for index, source in enumerate(sources):
        required_keys(source, ["url", "title", "publisher", "claims"], f"research source {index}")
        require(
            isinstance(source["publisher"], str) and "broadcom" in source["publisher"].casefold(),
            f"research source {index} publisher must identify Broadcom",
        )
        require(isinstance(source["title"], str) and source["title"].strip(), f"research source {index} title must be nonblank")
        require(isinstance(source["url"], str), f"research source {index} URL must be a string")
        parsed_url = urlsplit(source["url"])
        hostname = (parsed_url.hostname or "").lower()
        broadcom_published = (
            hostname == "broadcom.com"
            or hostname.endswith(".broadcom.com")
            or hostname == "blogs.vmware.com"
        )
        require(parsed_url.scheme == "https" and broadcom_published, f"research source {index} is not a Broadcom-published HTTPS URL")
        claims = source["claims"]
        require(isinstance(claims, list) and claims, f"research source {index} claims must be a nonempty array")
        require(
            all(isinstance(claim, str) and claim.strip() for claim in claims),
            f"research source {index} claims must be nonblank strings",
        )
        searchable_sources.append("\n".join([source["title"], *claims]).casefold())

    for product in estate["sourceProducts"]:
        product_names = {
            product["product"].casefold(),
            product["legacyName"].casefold(),
        }
        version = product["version"].casefold()
        require(
            any(
                version in source_text and any(name in source_text for name in product_names)
                for source_text in searchable_sources
            ),
            f"research sources do not cover exact source {product['product']} {product['version']}",
        )

    require(isinstance(research["liveDiscrepancies"], list), "liveDiscrepancies must be an array")


def run_export(inventory: Path, snapshot: Path, output: Path) -> None:
    require(MODULE.is_file(), "missing required artifact: VcfAriaMigration.psm1")
    script = r"""
$ErrorActionPreference = 'Stop'
$module = Import-Module -Name $env:VCF_VERIFY_MANIFEST -Force -PassThru
$exports = @($module.ExportedFunctions.Keys)
if ($exports.Count -ne 1 -or $exports[0] -ne 'Export-VcfAriaMigrationArchitecture') {
    throw "module must export only Export-VcfAriaMigrationArchitecture"
}
Export-VcfAriaMigrationArchitecture `
    -InventoryPath $env:VCF_VERIFY_INVENTORY `
    -CompatibilitySnapshotPath $env:VCF_VERIFY_SNAPSHOT `
    -OutputPath $env:VCF_VERIFY_OUTPUT | Out-Null
"""
    environment = os.environ.copy()
    environment.update(
        {
            "VCF_VERIFY_MANIFEST": str(MANIFEST),
            "VCF_VERIFY_INVENTORY": str(inventory),
            "VCF_VERIFY_SNAPSHOT": str(snapshot),
            "VCF_VERIFY_OUTPUT": str(output),
        }
    )
    try:
        result = subprocess.run(
            ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except FileNotFoundError as error:
        raise VerificationError("pwsh is required to verify the exporter") from error
    except subprocess.TimeoutExpired as error:
        raise VerificationError("PowerShell exporter timed out") from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise VerificationError(f"PowerShell exporter failed: {detail}")
    require(output.is_file(), "PowerShell exporter did not create its caller-supplied output")


def verify_exporter(plan: Any, estate: Any, snapshot: Any) -> None:
    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temporary:
        temporary_root = Path(temporary)
        canonical_output = temporary_root / "canonical" / "migration-plan.json"
        run_export(ESTATE, SNAPSHOT, canonical_output)
        generated = load_json(canonical_output)
        verify_plan(generated, estate, snapshot, "exported canonical plan")
        require(generated == plan, "checked-in migration-plan.json differs from exporter output")

        mutated_estate = copy.deepcopy(estate)
        mutated_snapshot = copy.deepcopy(snapshot)
        mutated_estate["estateId"] = "verifier-mutated-estate"
        mutated_workload = mutated_estate["fleet"]["addedWorkloadDomain"]
        mutated_workload["id"] = "verifier-mutated-wld"
        mutated_workload["cluster"]["id"] = "verifier-mutated-cluster"
        mutated_workload["cluster"]["failuresToTolerate"] = 1
        mutated_snapshot["snapshotId"] = "verifier-mutated-snapshot"
        mutated_snapshot["targetRelease"] = "9.0.2-verifier"
        for placement in mutated_snapshot["placements"]:
            placement["targetVersion"] = mutated_snapshot["targetRelease"]
        for rule in mutated_snapshot["migrationRules"]:
            rule["targetVersion"] = mutated_snapshot["targetRelease"]
        mutated_snapshot["placements"][0]["sizeProfile"] = "verifier-mutated-profile"
        mutated_snapshot["migrationRules"][0]["gates"].append("verifier-mutated-gate")

        mutated_estate_path = temporary_root / "mutated-estate.json"
        mutated_snapshot_path = temporary_root / "mutated-snapshot.json"
        with mutated_estate_path.open("w", encoding="utf-8") as handle:
            json.dump(mutated_estate, handle)
        with mutated_snapshot_path.open("w", encoding="utf-8") as handle:
            json.dump(mutated_snapshot, handle)
        mutated_output = temporary_root / "mutated" / "migration-plan.json"
        run_export(mutated_estate_path, mutated_snapshot_path, mutated_output)
        generated_mutation = load_json(mutated_output)
        verify_plan(generated_mutation, mutated_estate, mutated_snapshot, "exported mutated plan")


def verify() -> None:
    plan = load_json(ARTIFACT)
    estate = load_json(ESTATE)
    snapshot = load_json(SNAPSHOT)
    research = load_json(RESEARCH)
    verify_plan(plan, estate, snapshot, "migration-plan")
    verify_research(research, estate)
    verify_exporter(plan, estate, snapshot)


def main() -> int:
    try:
        verify()
    except (VerificationError, KeyError, TypeError, AttributeError, OSError) as error:
        print(f"VERIFICATION FAILED: {error}", file=sys.stderr)
        return 1
    print("VERIFICATION PASSED: migration architecture, exporter, and research record are consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
