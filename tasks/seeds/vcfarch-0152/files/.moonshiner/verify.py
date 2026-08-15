#!/usr/bin/env python3
"""Protected, offline verifier for vcfarch-0152."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
ARTIFACT = ROOT / "migration-installer-spec.json"
SCHEMA = ROOT / "installer-spec.schema.json"
INVENTORY = ROOT / "estate-inventory.json"
SNAPSHOT = ROOT / "compatibility-snapshot.json"
PROTECTED_HASHES = {
    "estate-inventory.json": "63ad3b04bf42bd944967bec85706fed9336db3f818f5c965400123b41a5fdb93",
    "compatibility-snapshot.json": "84476c862262b7033db681a2b17edb4de4f5214c130e41129b0a1a7e5bcef1ff",
    "installer-spec.schema.json": "7972722e07846d6459072f67e78302b16f7cef57fd2bb4e2970f371a7aecf2d4",
}


class VerificationError(Exception):
    pass


def fail(message: str) -> None:
    raise VerificationError(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"missing required file: {path.relative_to(ROOT)}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {path.relative_to(ROOT)}: {exc}")


def json_type_ok(value: Any, expected: str) -> bool:
    checks = {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "null": lambda v: v is None,
    }
    return expected in checks and checks[expected](value)


def validate_schema(value: Any, schema: dict[str, Any], path: str = "$") -> None:
    """Validate the JSON-Schema subset used by the shipped installer schema."""
    if "const" in schema and value != schema["const"]:
        fail(f"schema validation failed at {path}: expected constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        fail(f"schema validation failed at {path}: {value!r} is not in the allowed values")

    expected = schema.get("type")
    if expected and not json_type_ok(value, expected):
        fail(f"schema validation failed at {path}: expected {expected}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [key for key in required if key not in value]
        if missing:
            fail(f"schema validation failed at {path}: missing {missing}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extras = sorted(set(value) - set(properties))
            if extras:
                fail(f"schema validation failed at {path}: unexpected properties {extras}")
        for key, child in value.items():
            if key in properties:
                validate_schema(child, properties[key], f"{path}.{key}")

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            fail(f"schema validation failed at {path}: too few items")
        if schema.get("uniqueItems"):
            serialized = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(serialized) != len(set(serialized)):
                fail(f"schema validation failed at {path}: items must be unique")
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(value):
                validate_schema(item, item_schema, f"{path}[{index}]")

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            fail(f"schema validation failed at {path}: string is too short")
        pattern = schema.get("pattern")
        if pattern and re.search(pattern, value) is None:
            fail(f"schema validation failed at {path}: does not match {pattern}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            fail(f"schema validation failed at {path}: below minimum {schema['minimum']}")


def canonical_rows(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> set[tuple[Any, ...]]:
    return {tuple(row[key] for key in keys) for row in rows}


def verify_protected_inputs() -> None:
    for relative, expected in PROTECTED_HASHES.items():
        path = ROOT / relative
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except FileNotFoundError:
            fail(f"missing protected input: {relative}")
        if actual != expected:
            fail(f"protected input was modified: {relative}")


def powershell_literal(path: Path) -> str:
    return "'" + str(path.resolve()).replace("'", "''") + "'"


def verify_module_output(manifest: Path, spec: dict[str, Any], schema: dict[str, Any]) -> None:
    with tempfile.TemporaryDirectory(prefix=".vcfarch-verify-", dir=ROOT) as temp_dir:
        generated_path = Path(temp_dir) / "migration-installer-spec.json"
        command = "\n".join(
            [
                "$ErrorActionPreference = 'Stop'",
                f"Import-Module -Name {powershell_literal(manifest)} -Force",
                "New-VcfAriaMigrationInstallerSpec "
                f"-InventoryPath {powershell_literal(INVENTORY)} "
                f"-CompatibilitySnapshotPath {powershell_literal(SNAPSHOT)} "
                f"-SchemaPath {powershell_literal(SCHEMA)} "
                f"-OutputPath {powershell_literal(generated_path)} | Out-Null",
            ]
        )
        try:
            completed = subprocess.run(
                ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=60,
                check=False,
            )
        except FileNotFoundError:
            fail("pwsh is required to verify the PowerShell deliverable")
        except subprocess.TimeoutExpired:
            fail("PowerShell module generation exceeded 60 seconds")
        if completed.returncode != 0:
            detail = completed.stdout.strip()[-1200:]
            fail(f"PowerShell module could not generate the artifact: {detail}")
        try:
            generated = json.loads(generated_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            fail(f"PowerShell module did not write valid JSON: {exc}")
        validate_schema(generated, schema)
        if generated != spec:
            fail("migration-installer-spec.json is not the deterministic output of the delivered module")


def verify() -> None:
    # The installer artifact is parsed and validated against its own schema first.
    # Do not load the fixture, pinned authority, module, or research ledger above this point.
    schema = load_json(SCHEMA)
    spec = load_json(ARTIFACT)
    validate_schema(spec, schema)

    verify_protected_inputs()
    inventory = load_json(INVENTORY)
    snapshot = load_json(SNAPSHOT)

    if spec["designId"] != inventory["estateId"] + "-vcf9-migration":
        fail("designId must identify the supplied estate")

    if set(spec["generatedBy"]["sdkModules"]) != set(snapshot["requiredSdkModules"]):
        fail("generatedBy.sdkModules does not match the pinned SDK prerequisites")

    foundation = inventory["foundation"]
    domain = foundation["managementDomain"]
    topo = spec["topology"]
    expected_hosts = [host["id"] for host in domain["hosts"]]
    if topo["siteId"] != inventory["site"]["id"]:
        fail("topology uses the wrong site")
    if topo["deploymentModel"] != foundation["deploymentModel"]:
        fail("topology is not the required consolidated model")
    actual_domain = topo["managementDomain"]
    if actual_domain["name"] != domain["name"] or actual_domain["cluster"] != domain["cluster"]:
        fail("management-domain placement does not match inventory")
    if actual_domain["storage"] != domain["storage"]:
        fail("management-domain storage does not match inventory")
    minimum_hosts = snapshot["minimumSupportedManagementHostCount"]
    if actual_domain["hostCount"] != minimum_hosts or actual_domain["hosts"] != expected_hosts:
        fail("design must use exactly the four inventoried minimum-count hosts in inventory order")

    boundary_keys = (
        "sourceId", "sourceProduct", "sourceVersion", "targetComponent",
        "targetVersion", "transitionMode", "endOfGeneralSupport",
    )
    if canonical_rows(spec["supportBoundaries"], boundary_keys) != canonical_rows(snapshot["productRules"], boundary_keys):
        fail("source/target paths or support boundaries differ from the pinned snapshot")
    if len(spec["supportBoundaries"]) != len(snapshot["productRules"]):
        fail("support boundaries must contain exactly one mapping per source product")

    expected_sizing = {row["component"]: row for row in snapshot["targetSizing"]}
    components = {row["component"]: row for row in spec["targetComponents"]}
    if set(components) != set(expected_sizing) or len(spec["targetComponents"]) != 3:
        fail("targetComponents must name exactly Operations, Automation, and Operations for Logs")

    sizing_fields = (
        "deploymentModel", "preset", "nodeCount", "vcpuPerNode",
        "memoryGbPerNode", "dataDiskGbPerNode",
    )
    for component_name, expected in expected_sizing.items():
        actual = components[component_name]
        if actual["version"] != snapshot["targetRelease"]:
            fail(f"{component_name} target release is not pinned release")
        for field in sizing_fields:
            if actual[field] != expected[field]:
                fail(f"{component_name} has incorrect {field}")
        if len(actual["placement"]) != actual["nodeCount"]:
            fail(f"{component_name} must place every node exactly once")
        node_names = [item["node"] for item in actual["placement"]]
        placed_hosts = [item["host"] for item in actual["placement"]]
        if len(node_names) != len(set(node_names)):
            fail(f"{component_name} repeats a node placement")
        if not set(placed_hosts).issubset(expected_hosts):
            fail(f"{component_name} is placed outside the management cluster")
        if component_name == "VCF Operations for Logs" and len(set(placed_hosts)) != 3:
            fail("the three Logs nodes must be anti-affined across three hosts")

    target_vcpu = sum(c["nodeCount"] * c["vcpuPerNode"] for c in components.values())
    target_memory = sum(c["nodeCount"] * c["memoryGbPerNode"] for c in components.values())
    target_storage = sum(c["nodeCount"] * c["dataDiskGbPerNode"] for c in components.values())
    source_logs = next(p for p in inventory["sourceProducts"] if p["id"] == "aria-logs")
    peak = topo["peakSuiteDemand"]
    if peak != {
        "vcpu": target_vcpu + source_logs["deployment"]["nodes"] * source_logs["deployment"]["vcpuPerNode"],
        "memoryGb": target_memory + source_logs["deployment"]["nodes"] * source_logs["deployment"]["memoryGbPerNode"],
        "storageGb": target_storage + source_logs["deployment"]["dataStorageGb"],
        "includesParallelLogs": True,
    }:
        fail("peakSuiteDemand must include the complete target plus the parallel legacy Logs cluster")
    existing = domain["existingCoreManagementLoad"]
    if peak["vcpu"] + existing["vcpu"] > sum(h["physicalCores"] for h in domain["hosts"]):
        fail("peak architecture exceeds inventoried physical cores")
    if peak["memoryGb"] + existing["memoryGb"] > sum(h["memoryGb"] for h in domain["hosts"]):
        fail("peak architecture exceeds inventoried memory")
    if peak["storageGb"] + existing["storageGb"] > domain["usableStorageGb"]:
        fail("peak architecture exceeds inventoried usable storage")

    steps = spec["steps"]
    orders = [step["order"] for step in steps]
    if orders != sorted(orders) or len(orders) != len(set(orders)):
        fail("migration step order must be strict and unique")
    required_actions = snapshot["requiredActions"]
    if [step["action"] for step in steps] != [row["action"] for row in required_actions]:
        fail("migration actions do not follow the pinned supported sequence")
    ids_by_action = {step["action"]: step["id"] for step in steps}
    source_by_id = {p["id"]: p for p in inventory["sourceProducts"]}
    product_tuples = {(p["id"], p["product"], p["version"]) for p in inventory["sourceProducts"]}

    for step, rule in zip(steps, required_actions):
        expected_dependencies = [ids_by_action[action] for action in rule["dependsOn"]]
        if step["dependsOn"] != expected_dependencies:
            fail(f"{step['action']} has incorrect dependencies")
        required_gates = set(rule["gates"])
        actual_gates = {gate["id"] for gate in step["gates"]}
        if not required_gates.issubset(actual_gates):
            fail(f"{step['action']} is missing required gates: {sorted(required_gates - actual_gates)}")
        for dependency in step["dependsOn"]:
            if dependency not in {earlier["id"] for earlier in steps if earlier["order"] < step["order"]}:
                fail(f"{step['action']} depends on a missing or later step")
        for source in step["sources"]:
            if (source["sourceId"], source["product"], source["version"]) not in product_tuples:
                fail(f"{step['action']} names a source/version not present in inventory")

    all_step_sources = {source["sourceId"] for step in steps for source in step["sources"]}
    if all_step_sources != set(source_by_id):
        fail("every source product must participate in the ordered plan")

    content_owner: dict[str, str] = {}
    for product in inventory["sourceProducts"]:
        for item in product["content"]:
            if item["id"] in content_owner:
                fail(f"fixture contains duplicate content id {item['id']}")
            content_owner[item["id"]] = product["id"]

    occurrences: Counter[str] = Counter()
    actual_dispositions: dict[str, dict[str, str]] = {}
    for step in steps:
        step_sources = {source["sourceId"] for source in step["sources"]}
        for carry in step["carries"]:
            content_id = carry["contentId"]
            occurrences[content_id] += 1
            actual_dispositions[content_id] = {"disposition": "carry", "method": carry["method"]}
            if content_id in content_owner and content_owner[content_id] not in step_sources:
                fail(f"{content_id} is dispositioned by a step that does not name its source")
        for abandon in step["abandons"]:
            content_id = abandon["contentId"]
            occurrences[content_id] += 1
            actual_dispositions[content_id] = {
                "disposition": "abandon",
                "method": abandon["method"],
                "reasonCode": abandon["reasonCode"],
            }
            if content_id in content_owner and content_owner[content_id] not in step_sources:
                fail(f"{content_id} is dispositioned by a step that does not name its source")

    if set(occurrences) != set(content_owner):
        missing = sorted(set(content_owner) - set(occurrences))
        extra = sorted(set(occurrences) - set(content_owner))
        fail(f"content coverage differs from inventory; missing={missing}, extra={extra}")
    duplicated = sorted(item for item, count in occurrences.items() if count != 1)
    if duplicated:
        fail(f"content must be dispositioned exactly once: {duplicated}")

    expected_dispositions = {
        row["contentId"]: {key: value for key, value in row.items() if key != "contentId"}
        for row in snapshot["contentRules"]
    }
    if actual_dispositions != expected_dispositions:
        bad = sorted(
            key for key in set(actual_dispositions) | set(expected_dispositions)
            if actual_dispositions.get(key) != expected_dispositions.get(key)
        )
        fail(f"content compatibility decisions differ from pinned snapshot: {bad}")

    logs_usage = source_logs["usage"]
    logs_size = expected_sizing["VCF Operations for Logs"]
    if logs_usage["retentionDays"] > logs_size["capacityLimits"]["maximumTransferDays"]:
        fail("fixture retention exceeds the pinned Logs transfer boundary")
    if logs_usage["ingestionGbPerDay"] > logs_size["nodeCount"] * logs_size["capacityLimits"]["ingestionGbPerDayPerNode"]:
        fail("target Logs sizing cannot sustain fixture ingestion")
    if logs_usage["activeSyslogConnections"] > logs_size["nodeCount"] * logs_size["capacityLimits"]["activeConnectionsPerNode"]:
        fail("target Logs sizing cannot sustain fixture connections")

    manifest = ROOT / "VcfAriaMigration" / "VcfAriaMigration.psd1"
    module = ROOT / "VcfAriaMigration" / "VcfAriaMigration.psm1"
    if not manifest.is_file() or not module.is_file():
        fail("PowerShell module manifest and implementation are required")
    manifest_text = manifest.read_text(encoding="utf-8")
    module_text = module.read_text(encoding="utf-8")
    for module_name in snapshot["requiredSdkModules"]:
        if module_name not in manifest_text or module_name not in module_text:
            fail(f"PowerShell module must declare and import {module_name}")
    required_tokens = [
        "New-VcfAriaMigrationInstallerSpec", "InventoryPath", "CompatibilitySnapshotPath",
        "SchemaPath", "OutputPath", "ConvertFrom-Json", "ConvertTo-Json", "Import-Module",
    ]
    for token in required_tokens:
        if token not in module_text:
            fail(f"PowerShell implementation is missing {token}")
    if re.search(r"Export-ModuleMember[^\n]+New-VcfAriaMigrationInstallerSpec", module_text, re.IGNORECASE) is None:
        fail("PowerShell module must export New-VcfAriaMigrationInstallerSpec")
    vendored = [path for path in (ROOT / "VcfAriaMigration").rglob("*") if path.is_file() and path.suffix.lower() in {".dll", ".nupkg"}]
    if vendored:
        fail("VMware SDK binaries or packages must not be vendored")
    verify_module_output(manifest, spec, schema)


if __name__ == "__main__":
    try:
        verify()
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print("PASS: migration installer specification matches fixture and pinned compatibility snapshot")
