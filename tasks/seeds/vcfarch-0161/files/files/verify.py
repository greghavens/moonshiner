#!/usr/bin/env python3
"""Deterministic verifier for vcfarch-0161.

The verifier intentionally uses only the submitted architecture artifacts and the
pinned local fixture/schema/snapshot.  It performs no network or research checks.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from datetime import date
from typing import Any
from urllib.parse import urlparse


FIXED_HASHES = {
    "estate-inventory.json": "79af6961b894dc85b85a0abcd788307274fc6748462a13212f1a5c457eff6fe2",
    "compatibility-snapshot.json": "93c56cb7abae2ae277878220faa0c9f724aa6c42ff3cc103258526779f39de44",
    "migration-plan.schema.json": "68d7af719a5ff15795eefc3b9981fc9013d2f12e0486e120654503503895a513",
}

ROOT = Path(__file__).resolve().parent.parent


class VerificationError(Exception):
    pass


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise VerificationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=reject_duplicate_keys)
    except FileNotFoundError as exc:
        raise VerificationError(f"missing required file: {path.name}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(f"invalid JSON in {path.name}: {exc}") from exc


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def require_object(value: Any, path: str) -> dict[str, Any]:
    require(isinstance(value, dict), f"{path} must be an object")
    return value


def require_array(value: Any, path: str) -> list[Any]:
    require(isinstance(value, list), f"{path} must be an array")
    return value


def require_string(value: Any, path: str) -> str:
    require(isinstance(value, str) and bool(value), f"{path} must be a non-empty string")
    return value


def require_int(value: Any, path: str) -> int:
    require(isinstance(value, int) and not isinstance(value, bool), f"{path} must be an integer")
    return value


def require_keys(value: dict[str, Any], expected: set[str], path: str) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    require(not missing and not extra, f"{path} keys differ; missing={missing}, extra={extra}")


def require_string_array(value: Any, path: str) -> list[str]:
    items = require_array(value, path)
    require(all(isinstance(item, str) and item for item in items), f"{path} must contain non-empty strings")
    require(len(items) == len(set(items)), f"{path} must not contain duplicates")
    return items


def same_set(actual: list[str], expected: list[str], path: str) -> None:
    require(set(actual) == set(expected) and len(actual) == len(expected), f"{path} does not match the pinned snapshot")


def verify_fixed_inputs(files_dir: Path) -> None:
    for name, expected_hash in FIXED_HASHES.items():
        path = files_dir / name
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except FileNotFoundError as exc:
            raise VerificationError(f"missing protected input: files/{name}") from exc
        require(digest == expected_hash, f"protected input was modified: files/{name}")


def verify_research(root: Path, snapshot: dict[str, Any]) -> None:
    path = root / "research-sources.md"
    try:
        research = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise VerificationError("missing required file: research-sources.md") from exc
    require(bool(research.strip()), "research-sources.md must not be empty")

    access_dates = re.findall(
        r"(?i)\b(?:access(?:ed|\s+date)?|consulted(?:\s+on)?|retrieved(?:\s+on)?)\b"
        r"[^\n0-9]{0,20}(\d{4}-\d{2}-\d{2})",
        research,
    )
    require(bool(access_dates), "research-sources.md must record an ISO access date")
    try:
        parsed_dates = [date.fromisoformat(value) for value in access_dates]
        snapshot_date = date.fromisoformat(snapshot["snapshotVersion"])
    except (KeyError, TypeError, ValueError) as exc:
        raise VerificationError("research access date or snapshotVersion is invalid") from exc
    require(
        any(value >= snapshot_date for value in parsed_dates),
        "research access date predates the pinned compatibility snapshot",
    )

    raw_urls = re.findall(r"(?i)https://[^\s)>]+", research)
    urls = [value.rstrip(".,;:") for value in raw_urls]
    require(len(urls) >= 2, "research-sources.md must identify consulted Broadcom pages")
    for url in urls:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        official = (
            host == "broadcom.com"
            or host.endswith(".broadcom.com")
            or host == "vmware.com"
            or host.endswith(".vmware.com")
        )
        require(official, f"research URL is not Broadcom-published: {url}")
        require(parsed.path not in {"", "/"}, f"research URL does not identify a page: {url}")
        require(".invalid" not in host and host not in {"localhost", "127.0.0.1"}, f"research URL is not a real source: {url}")

        source_line = next((line for line in research.splitlines() if url in line), "")
        without_urls = re.sub(r"(?i)https://[^\s)>]+", "", source_line)
        words = re.findall(r"[A-Za-z][A-Za-z0-9'-]+", without_urls)
        require(len(words) >= 3, f"research source lacks an informed claim: {url}")

    normalized = research.lower()
    for version in ("8.18.3", "8.18.1", "9.0.2"):
        require(version in normalized, f"research-sources.md does not cover version {version}")
    require("operations for logs" in normalized, "research-sources.md does not cover Operations for Logs")
    require("automation" in normalized, "research-sources.md does not cover Automation")
    require(
        any(term in normalized for term in ("upgrade", "migration", "greenfield", "fresh deployment")),
        "research-sources.md does not cover supported migration paths",
    )
    require(
        any(term in normalized for term in (
            "compatibility", "compatible", "incompatible", "carry", "retain", "abandon",
            "discard", "recreation", "transfer", "exception", "configuration",
        )),
        "research-sources.md does not cover content/configuration compatibility or exceptions",
    )
    require(
        any(term in normalized for term in (
            "end of general support", "end-of-support", "eogs", "lifecycle", "support boundary",
        )),
        "research-sources.md does not cover lifecycle boundaries",
    )
    require("host" in normalized and ("ftt" in normalized or "failures to tolerate" in normalized),
            "research-sources.md does not cover the management-domain host-count/FTT constraint")


def verify_plan(plan: Any, inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    plan = require_object(plan, "plan")
    require_keys(plan, {"schemaVersion", "architecture", "components", "steps"}, "plan")
    require(plan["schemaVersion"] == "1.0", "schemaVersion must be 1.0")

    architecture = require_object(plan["architecture"], "architecture")
    require_keys(architecture, {"estateId", "topology", "siteCount", "managementDomain"}, "architecture")
    require(architecture["estateId"] == inventory["estateId"], "architecture.estateId differs from inventory")
    require(architecture["topology"] == inventory["managementDomain"]["topology"], "architecture.topology differs from inventory")
    require_int(architecture["siteCount"], "architecture.siteCount")
    require(architecture["siteCount"] == len(inventory["sites"]) == 1, "architecture must contain exactly one site")

    md = require_object(architecture["managementDomain"], "architecture.managementDomain")
    require_keys(
        md,
        {"siteId", "vcenterId", "clusterId", "hostCount", "failuresToTolerate", "storagePolicy", "minimumSupportedHostCount"},
        "architecture.managementDomain",
    )
    inv_md = inventory["managementDomain"]
    for key in ("siteId", "vcenterId", "clusterId", "storagePolicy"):
        require_string(md[key], f"architecture.managementDomain.{key}")
        require(md[key] == inv_md[key], f"architecture.managementDomain.{key} differs from inventory")
    host_count = require_int(md["hostCount"], "architecture.managementDomain.hostCount")
    ftt = require_int(md["failuresToTolerate"], "architecture.managementDomain.failuresToTolerate")
    minimum = require_int(md["minimumSupportedHostCount"], "architecture.managementDomain.minimumSupportedHostCount")
    require(host_count == inv_md["hostCount"], "hostCount differs from the estate fixture")
    require(ftt == inv_md["failuresToTolerate"], "failuresToTolerate differs from the estate fixture")

    matching_rules = [
        rule
        for rule in snapshot["hostRules"]
        if rule["topology"] == architecture["topology"]
        and rule["storagePolicy"] == md["storagePolicy"]
        and rule["failuresToTolerate"] == ftt
    ]
    require(len(matching_rules) == 1, "no unique pinned host rule matches topology, storage policy, and FTT")
    rule_minimum = matching_rules[0]["minimumHostCount"]
    require(minimum == rule_minimum, "minimumSupportedHostCount contradicts the pinned FTT/storage rule")
    require(host_count >= rule_minimum, f"hostCount {host_count} cannot satisfy FTT={ftt}; at least {rule_minimum} hosts are required")
    require(host_count == rule_minimum, "the consolidated design is not at the minimum supported host count")

    products = {product["id"]: product for product in inventory["products"]}
    components = require_array(plan["components"], "components")
    require(len(components) == len(products) == 3, "components must map all three and only the three inventoried products")
    component_ids = [require_string(require_object(item, "components[]").get("id"), "components[].id") for item in components]
    require(len(component_ids) == len(set(component_ids)), "component IDs must be unique")
    require(set(component_ids) == set(products), "component IDs differ from inventory product IDs")

    placement_expected = {
        "siteId": inv_md["siteId"],
        "vcenterId": inv_md["vcenterId"],
        "clusterId": inv_md["clusterId"],
        "datastoreId": inv_md["datastoreId"],
        "networkId": inv_md["networkId"],
        "antiAffinity": "spread-nodes-across-distinct-hosts",
    }
    all_gate_ids = set(snapshot["gateCatalog"])

    for component in components:
        component = require_object(component, "components[]")
        require_keys(component, {"id", "source", "target", "carryForward", "abandoned", "gates"}, f"component {component.get('id')}")
        component_id = component["id"]
        product = products[component_id]
        expected = snapshot["components"][component_id]

        source = require_object(component["source"], f"component {component_id}.source")
        require_keys(source, {"product", "version", "build", "support"}, f"component {component_id}.source")
        require(source["product"] == product["product"] == expected["sourceProduct"], f"component {component_id} source product mismatch")
        require(source["version"] == product["version"] == expected["sourceVersion"], f"component {component_id} source version mismatch")
        require(source["build"] == product["build"] == expected["sourceBuild"], f"component {component_id} source build mismatch")
        support = require_object(source["support"], f"component {component_id}.source.support")
        require_keys(support, {"status", "endOfGeneralSupport"}, f"component {component_id}.source.support")
        require(support["status"] == expected["supportStatus"], f"component {component_id} support status mismatch")
        require(support["endOfGeneralSupport"] == expected["endOfGeneralSupport"], f"component {component_id} EOGS mismatch")

        target = require_object(component["target"], f"component {component_id}.target")
        require_keys(target, {"component", "version", "migrationMode", "placement", "sizing"}, f"component {component_id}.target")
        require(target["component"] == expected["targetComponent"], f"component {component_id} target component mismatch")
        require(target["version"] == expected["targetVersion"] == snapshot["targetRelease"], f"component {component_id} target version mismatch")
        require(target["migrationMode"] == expected["migrationMode"], f"component {component_id} migration mode mismatch")
        placement = require_object(target["placement"], f"component {component_id}.target.placement")
        require_keys(placement, set(placement_expected), f"component {component_id}.target.placement")
        require(placement == placement_expected, f"component {component_id} is not placed on the consolidated management domain")
        sizing = require_object(target["sizing"], f"component {component_id}.target.sizing")
        require_keys(sizing, {"profile", "nodeCount", "vcpuPerNode", "memoryGbPerNode", "diskGbPerNode"}, f"component {component_id}.target.sizing")
        require(sizing == expected["sizing"], f"component {component_id} sizing differs from pinned design")

        carried = require_string_array(component["carryForward"], f"component {component_id}.carryForward")
        abandoned = require_string_array(component["abandoned"], f"component {component_id}.abandoned")
        gates = require_string_array(component["gates"], f"component {component_id}.gates")
        same_set(carried, expected["carryForward"], f"component {component_id}.carryForward")
        same_set(abandoned, expected["abandoned"], f"component {component_id}.abandoned")
        same_set(gates, expected["gates"], f"component {component_id}.gates")
        require(set(carried).isdisjoint(abandoned), f"component {component_id} carries and abandons the same item")
        require(set(carried) | set(abandoned) == set(product["content"]), f"component {component_id} does not dispose every inventoried item")
        require(set(gates) <= all_gate_ids, f"component {component_id} uses an unknown gate")

    steps = require_array(plan["steps"], "steps")
    expected_steps = snapshot["stepOrder"]
    require(len(steps) == len(expected_steps) == 8, "steps must contain the complete pinned sequence")
    seen_step_ids: set[str] = set()
    for index, (step, expected) in enumerate(zip(steps, expected_steps), start=1):
        step = require_object(step, f"steps[{index - 1}]")
        require_keys(step, {"order", "id", "componentId", "action", "gatedBy", "completionEvidence"}, f"steps[{index - 1}]")
        require_int(step["order"], f"steps[{index - 1}].order")
        require(step["order"] == index, "step order values must be consecutive and begin at 1")
        require(step["id"] == expected["id"], f"step {index} is out of order or has the wrong ID")
        require(step["id"] not in seen_step_ids, "step IDs must be unique")
        seen_step_ids.add(step["id"])
        require(step["componentId"] == expected["componentId"], f"step {step['id']} componentId mismatch")
        require(step["componentId"] == "platform" or step["componentId"] in products, f"step {step['id']} references an unknown component")
        require(step["action"] == expected["action"], f"step {step['id']} action differs from pinned plan")
        gated_by = require_string_array(step["gatedBy"], f"step {step['id']}.gatedBy")
        evidence = require_string_array(step["completionEvidence"], f"step {step['id']}.completionEvidence")
        require(gated_by == expected["gatedBy"], f"step {step['id']} gates differ from pinned order")
        require(evidence == expected["completionEvidence"], f"step {step['id']} completion evidence differs from pinned order")
        require(set(gated_by) <= all_gate_ids, f"step {step['id']} uses an unknown gate")


def run_module(root: Path, inventory_path: Path, snapshot_path: Path) -> Any:
    manifest = root / "VcfMigrationArchitecture.psd1"
    module = root / "VcfMigrationArchitecture.psm1"
    require(manifest.is_file(), "missing VcfMigrationArchitecture.psd1")
    require(module.is_file(), "missing VcfMigrationArchitecture.psm1")

    inspect_script = r"""
$ErrorActionPreference = 'Stop'
$data = Import-PowerShellDataFile -Path $env:VCF_MANIFEST
$names = @($data.RequiredModules | ForEach-Object {
    if ($_ -is [string]) { $_ } else { $_.ModuleName }
})
[ordered]@{
    RootModule = [string] $data.RootModule
    RequiredModules = $names
    FunctionsToExport = @($data.FunctionsToExport)
} | ConvertTo-Json -Compress
"""
    env = os.environ.copy()
    env["VCF_MANIFEST"] = str(manifest)
    inspected = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", inspect_script],
        text=True,
        capture_output=True,
        env=env,
        timeout=30,
        check=False,
    )
    require(inspected.returncode == 0, f"module manifest cannot be read: {inspected.stderr.strip()}")
    try:
        module_info = json.loads(inspected.stdout)
    except json.JSONDecodeError as exc:
        raise VerificationError("module manifest returned invalid metadata") from exc
    require(module_info.get("RootModule") == module.name, "module manifest RootModule is incorrect")
    exported_functions = module_info.get("FunctionsToExport", [])
    if isinstance(exported_functions, str):
        exported_functions = [exported_functions]
    require(exported_functions == ["Export-VcfMigrationPlan"],
            "manifest must export exactly Export-VcfMigrationPlan")

    with tempfile.TemporaryDirectory(prefix="vcfarch-0161-") as temporary:
        generated = Path(temporary) / "migration-plan.json"
        export_script = r"""
$ErrorActionPreference = 'Stop'
Import-Module -Name $env:VCF_MANIFEST -Force
$command = Get-Command -Name Export-VcfMigrationPlan -Module VcfMigrationArchitecture -ErrorAction Stop
foreach ($name in @('InventoryPath', 'CompatibilityPath', 'OutputPath')) {
    if (-not $command.Parameters.ContainsKey($name)) {
        throw "Export-VcfMigrationPlan parameter $name must be mandatory"
    }
    $mandatory = @($command.Parameters[$name].Attributes | Where-Object {
        $_ -is [System.Management.Automation.ParameterAttribute] -and $_.Mandatory
    }).Count -gt 0
    if (-not $mandatory) {
        throw "Export-VcfMigrationPlan parameter $name must be mandatory"
    }
}
Export-VcfMigrationPlan -InventoryPath $env:VCF_INVENTORY -CompatibilityPath $env:VCF_COMPATIBILITY -OutputPath $env:VCF_OUTPUT
"""
        env["VCF_INVENTORY"] = str(inventory_path)
        env["VCF_COMPATIBILITY"] = str(snapshot_path)
        env["VCF_OUTPUT"] = str(generated)
        exported = subprocess.run(
            ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", export_script],
            text=True,
            capture_output=True,
            env=env,
            timeout=60,
            check=False,
        )
        require(exported.returncode == 0, f"PowerShell module failed: {exported.stderr.strip() or exported.stdout.strip()}")
        return load_json(generated)


def main() -> int:
    root = ROOT
    files_dir = root / "files"
    try:
        verify_fixed_inputs(files_dir)
        inventory = require_object(load_json(files_dir / "estate-inventory.json"), "inventory")
        snapshot = require_object(load_json(files_dir / "compatibility-snapshot.json"), "compatibility snapshot")
        verify_research(root, snapshot)
        submitted = load_json(root / "migration-plan.json")
        verify_plan(submitted, inventory, snapshot)
        generated = run_module(root, files_dir / "estate-inventory.json", files_dir / "compatibility-snapshot.json")
        verify_plan(generated, inventory, snapshot)
        require(generated == submitted, "migration-plan.json differs from the module's generated architecture")

        # Exercise both input path parameters with compatible variants so a
        # checked-in constant plan cannot masquerade as a working generator.
        variant_inventory = json.loads(json.dumps(inventory))
        variant_inventory["estateId"] = "verification-estate"
        variant_inventory["sites"][0]["id"] = "verification-site"
        variant_md = variant_inventory["managementDomain"]
        variant_md["siteId"] = "verification-site"
        variant_md["vcenterId"] = "verification-vcenter"
        variant_md["clusterId"] = "verification-cluster"
        variant_md["datastoreId"] = "verification-datastore"
        variant_md["networkId"] = "verification-network"

        variant_snapshot = json.loads(json.dumps(snapshot))
        variant_snapshot["targetRelease"] = "9.0.2-verification"
        for component in variant_snapshot["components"].values():
            component["targetVersion"] = variant_snapshot["targetRelease"]
            component["endOfGeneralSupport"] = "2028-12-31"
            component["sizing"]["vcpuPerNode"] += 1

        with tempfile.TemporaryDirectory(prefix="vcfarch-0161-inputs-") as temporary:
            temporary_dir = Path(temporary)
            inventory_path = temporary_dir / "inventory.json"
            snapshot_path = temporary_dir / "compatibility.json"
            inventory_path.write_text(json.dumps(variant_inventory), encoding="utf-8")
            snapshot_path.write_text(json.dumps(variant_snapshot), encoding="utf-8")
            variant_generated = run_module(root, inventory_path, snapshot_path)
        verify_plan(variant_generated, variant_inventory, variant_snapshot)
        require(variant_generated != generated, "module ignores its inventory or compatibility input")
    except (VerificationError, OSError, UnicodeError, subprocess.SubprocessError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: migration architecture matches the protected fixture and compatibility snapshot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
