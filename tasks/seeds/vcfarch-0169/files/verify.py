#!/usr/bin/env python3
"""Offline verifier for the VCF architecture artifact."""

from __future__ import annotations

import ast
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib
from typing import Any
from urllib.parse import urlsplit


PROTECTED_HASHES = {
    "estate_inventory.json": "0d708463340bcdbba9a0b262005cd0909668845955bc43e088e586dcf5853aca",
    "compatibility_snapshot.json": "90172a8053ffc70a68ac5bf8479b5bb990ff7dd0bcc69f2db80bf5956ea5a060",
    "installer_spec.json": "3834727bc0d0ba611d808dec853c987286cad0e8364f193aa3780570c8f7e125",
}


class VerificationError(Exception):
    pass


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VerificationError(f"missing required file: {path.name}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(f"invalid JSON in {path.name}: {exc}") from exc


def resolve_ref(root_schema: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/"):
        raise VerificationError(f"unsupported schema reference: {reference}")
    value: Any = root_schema
    for token in reference[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        value = value[token]
    if not isinstance(value, dict):
        raise VerificationError(f"schema reference is not an object: {reference}")
    return value


def type_matches(instance: Any, expected: str) -> bool:
    if expected == "null":
        return instance is None
    if expected == "object":
        return isinstance(instance, dict)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "string":
        return isinstance(instance, str)
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if expected == "boolean":
        return isinstance(instance, bool)
    raise VerificationError(f"unsupported schema type: {expected}")


def validate_schema(
    instance: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    path: str = "$",
) -> list[str]:
    if "$ref" in schema:
        return validate_schema(instance, resolve_ref(root_schema, schema["$ref"]), root_schema, path)

    errors: list[str] = []
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: value is not in {schema['enum']!r}")

    expected_type = schema.get("type")
    if expected_type is not None:
        allowed = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(type_matches(instance, item) for item in allowed):
            errors.append(f"{path}: expected type {expected_type!r}")
            return errors

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                errors.append(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        for key, value in instance.items():
            child_path = f"{path}.{key}"
            if key in properties:
                errors.extend(validate_schema(value, properties[key], root_schema, child_path))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{child_path}: additional property is not allowed")

    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: more than {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            normalized = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(normalized) != len(set(normalized)):
                errors.append(f"{path}: items are not unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, value in enumerate(instance):
                errors.extend(validate_schema(value, item_schema, root_schema, f"{path}[{index}]"))

    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            errors.append(f"{path}: string is shorter than {schema['minLength']}")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            errors.append(f"{path}: string does not match {schema['pattern']!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: value is less than {schema['minimum']}")

    return errors


def ensure(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def check_protected_files(root: Path) -> None:
    for name, expected in PROTECTED_HASHES.items():
        path = root / name
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except FileNotFoundError as exc:
            raise VerificationError(f"missing protected fixture: {name}") from exc
        ensure(actual == expected, f"protected fixture was modified: {name}")


def check_architecture(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    ensure(plan["estate_id"] == inventory["estate_id"], "estate_id does not match inventory")
    ensure(plan["planning_date"] == inventory["planning_date"], "planning_date does not match inventory")
    ensure(plan["snapshot_id"] == snapshot["snapshot_id"], "snapshot_id does not match authority")

    decision = plan["architecture_decision"]
    authority = snapshot["storage_decision"]
    ensure(
        decision["selected_storage_architecture"] == authority["selected_architecture"],
        "wrong selected storage architecture",
    )
    alternatives = {item["architecture"]: item for item in decision["alternatives"]}
    ensure(set(alternatives) == {"OSA", "ESA"}, "architecture must compare OSA and ESA exactly once")
    candidates = {item["profile_id"]: item for item in inventory["host_candidates"]}
    required_traffic = set(authority["required_traffic_classes"])
    for architecture, expected in authority["alternatives"].items():
        actual = alternatives[architecture]
        scalar_fields = (
            "host_profile_id",
            "usable_tb_per_host",
            "minimum_data_hosts",
            "capacity_hosts",
            "host_count",
            "storage_policy",
        )
        for field in scalar_fields:
            ensure(actual[field] == expected[field], f"{architecture} {field} is not pinned value")
        ensure(actual["required_usable_tb"] == authority["required_usable_tb"], f"{architecture} capacity basis is wrong")
        ensure(
            actual["operational_spare_hosts"] == authority["operational_spare_hosts"],
            f"{architecture} spare-host count is wrong",
        )
        computed_capacity_hosts = math.ceil(actual["required_usable_tb"] / actual["usable_tb_per_host"])
        computed_total = max(actual["minimum_data_hosts"], computed_capacity_hosts) + actual["operational_spare_hosts"]
        ensure(actual["capacity_hosts"] == computed_capacity_hosts, f"{architecture} capacity-host calculation is wrong")
        ensure(actual["host_count"] == computed_total, f"{architecture} total host calculation is wrong")
        network = actual["network"]
        for field in ("uplinks_per_host", "uplink_speed_gbps", "mtu"):
            ensure(network[field] == expected[field], f"{architecture} network {field} is wrong")
        ensure(set(network["traffic_classes"]) == required_traffic, f"{architecture} traffic classes are incomplete")
        candidate = candidates[actual["host_profile_id"]]
        ensure(candidate["storage_architecture"] == architecture, f"{architecture} host profile mismatch")
        ensure(candidate["available_hosts"] >= actual["host_count"], f"{architecture} design exceeds available hosts")

    placements = {item["component"]: item for item in plan["target_placements"]}
    expected_placements = {item["component"]: item for item in snapshot["target_sizing"]}
    ensure(placements == expected_placements, "target component placement or sizing differs from snapshot")

    inventory_sources = {item["source_id"]: item for item in inventory["source_products"]}
    migrations = {item["source_id"]: item for item in plan["source_migrations"]}
    expected_migrations = {item["source_id"]: item for item in snapshot["migration_compatibility"]}
    ensure(set(migrations) == set(inventory_sources) == set(expected_migrations), "source migrations are incomplete")
    for source_id, expected in expected_migrations.items():
        actual = migrations[source_id]
        source = inventory_sources[source_id]
        for field in ("product", "version"):
            ensure(actual[field] == source[field] == expected[field], f"{source_id} {field} is wrong")
        for field in ("eogs_date", "target_component", "target_version", "method", "intermediate_versions"):
            ensure(actual[field] == expected[field], f"{source_id} {field} is wrong")
        ensure(set(actual["gate_ids"]) == set(expected["required_gate_ids"]), f"{source_id} gate set is wrong")
        dispositions = actual["content_dispositions"]
        ids = [item["content_id"] for item in dispositions]
        inventory_ids = [item["content_id"] for item in source["content"]]
        ensure(len(ids) == len(set(ids)), f"{source_id} content is accounted more than once")
        ensure(set(ids) == set(inventory_ids), f"{source_id} content accounting is incomplete")
        actual_pairs = {item["content_id"]: [item["status"], item["method"]] for item in dispositions}
        ensure(actual_pairs == expected["content_dispositions"], f"{source_id} content dispositions are wrong")

    gates = {item["gate_id"]: item for item in plan["gates"]}
    ensure(len(gates) == len(plan["gates"]), "gate inventory contains duplicate gate_ids")
    ensure(set(gates) == set(snapshot["required_gates"]), "gate inventory differs from snapshot")
    expected_steps = snapshot["ordered_steps"]
    ensure([item["sequence"] for item in plan["steps"]] == list(range(1, len(expected_steps) + 1)), "step sequence is not contiguous")
    ensure([item["step_id"] for item in plan["steps"]] == [item["step_id"] for item in expected_steps], "migration step order is wrong")
    for actual, expected in zip(plan["steps"], expected_steps):
        ensure(actual["source_id"] == expected["source_id"], f"{actual['step_id']} source_id is wrong")
        ensure(set(actual["gate_ids"]) == set(expected["required_gate_ids"]), f"{actual['step_id']} gate set is wrong")
        ensure(set(actual["gate_ids"]) <= set(gates), f"{actual['step_id']} references an unknown gate")
    step_ids = {item["step_id"] for item in plan["steps"]}
    for gate_id, gate in gates.items():
        ensure(set(gate["blocks_step_ids"]) <= step_ids, f"{gate_id} blocks an unknown step")
        used_by = {item["step_id"] for item in plan["steps"] if gate_id in item["gate_ids"]}
        ensure(set(gate["blocks_step_ids"]) == used_by, f"{gate_id} blocks_step_ids does not match step usage")

    research_log = plan["research_log"]
    urls = [item["url"] for item in research_log]
    ensure(len(urls) == len(set(urls)), "research_log contains duplicate URLs")
    required_topics = {"migration-path", "content-compatibility", "lifecycle", "sizing", "storage", "network"}
    covered_topics = {topic for item in research_log for topic in item["topics"]}
    ensure(required_topics <= covered_topics, "research_log does not cover every requested research topic")
    approved_domains = ("broadcom.com", "vmware.com")
    for index, record in enumerate(research_log):
        parsed = urlsplit(record["url"])
        hostname = (parsed.hostname or "").lower()
        ensure(
            parsed.scheme == "https" and any(hostname == domain or hostname.endswith("." + domain) for domain in approved_domains),
            f"research_log entry {index + 1} is not a Broadcom-published HTTPS page",
        )
        ensure(parsed.username is None and parsed.password is None, f"research_log entry {index + 1} has URL credentials")


def check_package(root: Path, plan: dict[str, Any], schema: dict[str, Any]) -> None:
    pyproject_path = root / "pyproject.toml"
    try:
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VerificationError("missing pyproject.toml") from exc
    except tomllib.TOMLDecodeError as exc:
        raise VerificationError(f"invalid pyproject.toml: {exc}") from exc
    project = pyproject.get("project", {})
    ensure(project.get("name") == "vcf-architecture", "pyproject project.name must be vcf-architecture")
    ensure(project.get("dependencies", []) == [], "third-party runtime dependencies are not allowed")
    ensure("optional-dependencies" not in project, "optional third-party dependencies are not allowed")

    package_dir = root / "vcf_architecture"
    ensure((package_dir / "__init__.py").is_file(), "missing vcf_architecture/__init__.py")
    ensure((package_dir / "__main__.py").is_file(), "missing vcf_architecture/__main__.py")
    python_files = sorted(package_dir.rglob("*.py"))
    ensure(bool(python_files), "Python package contains no modules")
    stdlib = set(sys.stdlib_module_names)
    for path in python_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            raise VerificationError(f"syntax error in {path.relative_to(root)}: {exc}") from exc
        for node in ast.walk(tree):
            imported: str | None = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".", 1)[0]
                    ensure(top in stdlib or top == "vcf_architecture", f"non-stdlib import {alias.name!r} in {path.name}")
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported = node.module.split(".", 1)[0]
            if imported is not None:
                ensure(imported in stdlib or imported == "vcf_architecture", f"non-stdlib import {imported!r} in {path.name}")

    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temp_dir:
        temp_path = Path(temp_dir)
        generated_path = temp_path / "migration_plan.json"
        repeated_path = temp_path / "migration_plan-repeated.json"
        env = os.environ.copy()
        env["PYTHONPATH"] = str(root)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        command = [
            sys.executable,
            "-m",
            "vcf_architecture",
            "--inventory",
            str(root / "estate_inventory.json"),
            "--snapshot",
            str(root / "compatibility_snapshot.json"),
            "--output",
            str(generated_path),
        ]
        completed = subprocess.run(command, cwd=root, env=env, text=True, capture_output=True, timeout=20, check=False)
        ensure(completed.returncode == 0, f"package CLI failed: {completed.stderr.strip()}")
        generated = load_json(generated_path)
        errors = validate_schema(generated, schema, schema)
        ensure(not errors, "package-generated artifact fails schema: " + "; ".join(errors[:10]))
        ensure(generated == plan, "package output does not reproduce committed migration_plan.json")

        repeated_command = [*command[:-1], str(repeated_path)]
        repeated = subprocess.run(repeated_command, cwd=root, env=env, text=True, capture_output=True, timeout=20, check=False)
        ensure(repeated.returncode == 0, f"repeated package CLI run failed: {repeated.stderr.strip()}")
        ensure(generated_path.read_bytes() == repeated_path.read_bytes(), "package output is not byte-for-byte deterministic")

        variant_inventory = json.loads(json.dumps(load_json(root / "estate_inventory.json")))
        variant_snapshot = json.loads(json.dumps(load_json(root / "compatibility_snapshot.json")))
        variant_inventory["planning_date"] = "2026-08-16"
        variant_snapshot["target_sizing"][0]["memory_gb_per_node"] += 1
        variant_inventory_path = temp_path / "estate-inventory-variant.json"
        variant_snapshot_path = temp_path / "compatibility-snapshot-variant.json"
        variant_output_path = temp_path / "migration-plan-variant.json"
        variant_inventory_path.write_text(json.dumps(variant_inventory), encoding="utf-8")
        variant_snapshot_path.write_text(json.dumps(variant_snapshot), encoding="utf-8")
        variant_command = [
            sys.executable,
            "-m",
            "vcf_architecture",
            "--inventory",
            str(variant_inventory_path),
            "--snapshot",
            str(variant_snapshot_path),
            "--output",
            str(variant_output_path),
        ]
        variant_run = subprocess.run(variant_command, cwd=root, env=env, text=True, capture_output=True, timeout=20, check=False)
        ensure(variant_run.returncode == 0, f"package CLI failed on valid input variants: {variant_run.stderr.strip()}")
        variant_plan = load_json(variant_output_path)
        ensure(variant_plan["planning_date"] == "2026-08-16", "package does not read planning_date from inventory")
        ensure(
            variant_plan["target_placements"][0]["memory_gb_per_node"] == variant_snapshot["target_sizing"][0]["memory_gb_per_node"],
            "package does not read target sizing from compatibility snapshot",
        )


def main() -> int:
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd().resolve()
    try:
        spec = load_json(root / "installer_spec.json")
        plan = load_json(root / spec.get("artifact_path", "migration_plan.json"))
        schema = spec["artifact_schema"]

        # The artifact schema is intentionally the first validation performed.
        schema_errors = validate_schema(plan, schema, schema)
        if schema_errors:
            raise VerificationError("schema validation failed:\n  " + "\n  ".join(schema_errors[:40]))

        check_protected_files(root)
        inventory = load_json(root / "estate_inventory.json")
        snapshot = load_json(root / "compatibility_snapshot.json")
        check_architecture(plan, inventory, snapshot)
        check_package(root, plan, schema)
    except (KeyError, TypeError, VerificationError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: migration_plan.json is schema-valid, snapshot-correct, and reproducible")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
