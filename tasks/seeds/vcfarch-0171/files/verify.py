#!/usr/bin/env python3
"""Offline verifier for the Northstar VCF migration architecture."""

from __future__ import annotations

import ast
import datetime as dt
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = ROOT / "installer_spec.schema.json"
ARTIFACT_PATH = ROOT / "migration_architecture.json"
INVENTORY_PATH = ROOT / "estate_inventory.json"
SNAPSHOT_PATH = ROOT / "compatibility_snapshot.json"
PACKAGE = "vcf_migration_architecture"


class VerificationError(Exception):
    """A deterministic verification failure."""


class SchemaError(VerificationError):
    """An installer schema validation failure."""


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise VerificationError(f"missing required file: {path.name}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(
            f"invalid JSON in {path.name}: line {exc.lineno}, column {exc.colno}"
        ) from exc


def resolve_ref(root_schema: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise SchemaError(f"unsupported non-local schema reference: {ref}")
    current: Any = root_schema
    for token in ref[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or token not in current:
            raise SchemaError(f"unresolvable schema reference: {ref}")
        current = current[token]
    if not isinstance(current, dict):
        raise SchemaError(f"schema reference is not an object: {ref}")
    return current


def type_matches(value: Any, expected: str) -> bool:
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
    raise SchemaError(f"unsupported schema type: {expected}")


def validate_format(value: str, format_name: str, path: str) -> None:
    if format_name == "date":
        try:
            dt.date.fromisoformat(value)
        except ValueError as exc:
            raise SchemaError(f"{path}: expected ISO calendar date") from exc
    elif format_name == "uri":
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise SchemaError(f"{path}: expected an absolute HTTP(S) URI")
    else:
        raise SchemaError(f"{path}: unsupported format {format_name!r}")


def validate_schema(
    value: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    path: str = "$",
) -> None:
    """Validate the complete artifact against the shipped installer schema subset."""
    if "$ref" in schema:
        validate_schema(value, resolve_ref(root_schema, schema["$ref"]), root_schema, path)
        return

    if "const" in schema and value != schema["const"]:
        raise SchemaError(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaError(f"{path}: value {value!r} is not in the allowed enumeration")

    expected_type = schema.get("type")
    if expected_type is not None and not type_matches(value, expected_type):
        raise SchemaError(f"{path}: expected {expected_type}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [key for key in required if key not in value]
        if missing:
            raise SchemaError(f"{path}: missing required properties {missing}")
        if len(value) < schema.get("minProperties", 0):
            raise SchemaError(f"{path}: too few properties")

        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in properties:
                validate_schema(child, properties[key], root_schema, child_path)
            elif additional is False:
                raise SchemaError(f"{child_path}: additional property is not allowed")
            elif isinstance(additional, dict):
                validate_schema(child, additional, root_schema, child_path)

    if isinstance(value, list):
        minimum = schema.get("minItems", 0)
        if len(value) < minimum:
            raise SchemaError(f"{path}: expected at least {minimum} items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(encoded) != len(set(encoded)):
                raise SchemaError(f"{path}: duplicate array items are not allowed")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, child in enumerate(value):
                validate_schema(child, item_schema, root_schema, f"{path}[{index}]")

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise SchemaError(f"{path}: string is shorter than minLength")
        if "format" in schema:
            validate_format(value, schema["format"], path)

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise SchemaError(f"{path}: value is below minimum {schema['minimum']}")


def unique_map(items: list[dict[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        item_key = item[key]
        if item_key in result:
            raise VerificationError(f"duplicate {label}: {item_key}")
        result[item_key] = item
    return result


def expected_design_demand(inventory: dict[str, Any], component: str) -> dict[str, int]:
    growth = 1 + inventory["requirements"]["growth_headroom_percent"] / 100
    demand = inventory["service_demand"]
    if component == "VCF Operations":
        return {
            "objects": math.ceil(demand["operations"]["objects"] * growth),
            "collected_metrics": math.ceil(
                demand["operations"]["collected_metrics"] * growth
            ),
        }
    if component == "VCF Automation":
        return {
            "managed_workloads": math.ceil(
                demand["automation"]["managed_workloads"] * growth
            ),
            "concurrent_requests": math.ceil(
                demand["automation"]["concurrent_requests"] * growth
            ),
        }
    if component == "VCF Operations for Logs":
        ingest = math.ceil(demand["logs"]["ingest_gb_per_day"] * growth)
        return {
            "ingest_gb_per_day": ingest,
            "events_per_second": math.ceil(
                demand["logs"]["events_per_second"] * growth
            ),
            "active_syslog_connections": math.ceil(
                demand["logs"]["active_syslog_connections"] * growth
            ),
            "retained_data_gb": (
                ingest * inventory["requirements"]["target_log_retention_days"]
            ),
        }
    raise VerificationError(f"unknown target component in sizing check: {component}")


def verify_architecture(
    artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    failures: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    check(artifact["estate_id"] == inventory["estate_id"], "estate_id does not match inventory")
    check(
        artifact["target_release"] == inventory["target_release"] == snapshot["target_release"],
        "target release does not match fixture and pinned snapshot",
    )

    research = artifact["research"]
    check(
        research["completed_at"] == inventory["as_of"],
        "research completion date does not match the estate snapshot date",
    )
    research_urls: list[str] = []
    for source in research["sources"]:
        research_urls.append(source["url"])
        hostname = (urlsplit(source["url"]).hostname or "").lower()
        check(
            source["publisher"].strip().lower() == "broadcom",
            f"research source is not identified as Broadcom-published: {source['url']}",
        )
        check(
            hostname == "broadcom.com" or hostname.endswith(".broadcom.com"),
            f"research source is not hosted on a Broadcom domain: {source['url']}",
        )
        check(
            source["accessed_at"] == research["completed_at"],
            f"research access date does not match completion date: {source['url']}",
        )
    check(
        len(research_urls) == len(set(research_urls)),
        "research source URLs must be unique",
    )

    components = unique_map(
        artifact["architecture"]["components"], "component", "target component"
    )
    expected_targets = snapshot["targets"]
    check(set(components) == set(expected_targets), "target component set is incomplete or unexpected")

    cluster_capacity = inventory["domains"][0]["clusters"][0]["available_capacity"]
    total_vcpus = 0
    total_memory = 0
    total_storage_gb = 0
    for name, expected in expected_targets.items():
        actual = components.get(name)
        if actual is None:
            continue
        check(actual["version"] == expected["version"], f"{name}: incorrect target version")
        check(
            actual["deployment_model"] == expected["deployment_model"],
            f"{name}: incorrect deployment model",
        )
        check(actual["placement"] == expected["placement"], f"{name}: incorrect placement")
        check(actual["sizing"] == expected["sizing"], f"{name}: incorrect pinned sizing")
        check(
            actual["sizing"]["design_demand"] == expected_design_demand(inventory, name),
            f"{name}: design demand does not derive from inventory headroom",
        )
        for metric, demand_value in actual["sizing"]["design_demand"].items():
            check(
                demand_value <= actual["sizing"]["surviving_capacity"].get(metric, -1),
                f"{name}: surviving capacity does not cover {metric}",
            )
        node_count = actual["sizing"]["node_count"]
        total_vcpus += node_count * actual["sizing"]["vcpus_per_node"]
        total_memory += node_count * actual["sizing"]["memory_gb_per_node"]
        total_storage_gb += node_count * actual["sizing"]["data_disk_gb_per_node"]

    check(total_vcpus <= cluster_capacity["vcpus"], "target vCPU design exceeds placement capacity")
    check(
        total_memory <= cluster_capacity["memory_gb"],
        "target memory design exceeds placement capacity",
    )
    check(
        total_storage_gb <= cluster_capacity["storage_tb"] * 1000,
        "target storage design exceeds placement capacity",
    )

    inventory_sources = unique_map(inventory["source_products"], "id", "inventory source")
    migrations = unique_map(artifact["migrations"], "source_id", "migration source")
    boundaries = unique_map(
        artifact["lifecycle_boundaries"], "source_id", "lifecycle source"
    )
    check(set(migrations) == set(inventory_sources), "migration coverage does not match inventory")
    check(set(boundaries) == set(inventory_sources), "lifecycle coverage does not match inventory")

    for source_id, source in inventory_sources.items():
        authority = snapshot["migrations"].get(source_id)
        migration = migrations.get(source_id)
        boundary = boundaries.get(source_id)
        if authority is None or migration is None or boundary is None:
            continue
        check(migration["source_product"] == source["product"], f"{source_id}: product name mismatch")
        check(migration["source_version"] == source["version"], f"{source_id}: source version mismatch")
        for field in ("target_component", "target_version", "method", "version_path"):
            check(
                migration[field] == authority[field],
                f"{source_id}: {field} conflicts with pinned compatibility snapshot",
            )
        check(
            migration["method"] not in authority["forbidden_methods"],
            f"{source_id}: forbidden migration method selected",
        )

        carry = unique_map(migration["carry_forward"], "content_id", f"{source_id} carry item")
        abandon = unique_map(migration["abandon"], "content_id", f"{source_id} abandon item")
        check(set(carry) == set(authority["carry"]), f"{source_id}: carry-forward set mismatch")
        check(set(abandon) == set(authority["abandon"]), f"{source_id}: abandon set mismatch")
        for content_id, action in authority["carry"].items():
            if content_id in carry:
                check(carry[content_id]["action"] == action, f"{source_id}: wrong action for {content_id}")
        for content_id, action in authority["abandon"].items():
            if content_id in abandon:
                check(
                    abandon[content_id]["action"] == action,
                    f"{source_id}: wrong action for {content_id}",
                )
        inventory_content = {item["id"] for item in source["content"]}
        check(
            set(carry).isdisjoint(abandon) and set(carry) | set(abandon) == inventory_content,
            f"{source_id}: content dispositions do not partition inventory",
        )

        expected_boundary = {
            "source_id": source_id,
            "product": source["product"],
            "version": source["version"],
            "eogs": authority["eogs"],
            "status_at_snapshot": authority["status_at_snapshot"],
        }
        check(boundary == expected_boundary, f"{source_id}: incorrect lifecycle boundary")

    steps = artifact["steps"]
    required_steps = snapshot["required_sequence"]
    check(
        [step["order"] for step in steps] == list(range(1, len(steps) + 1)),
        "step order must be contiguous and start at 1",
    )
    check(
        [step["id"] for step in steps] == [step["id"] for step in required_steps],
        "ordered migration sequence does not match the pinned sequence",
    )
    for actual, expected in zip(steps, required_steps):
        check(actual["component"] == expected["component"], f"{actual['id']}: wrong component")
        gate_ids = [gate["id"] for gate in actual["gates"]]
        check(len(gate_ids) == len(set(gate_ids)), f"{actual['id']}: duplicate gate IDs")
        check(
            set(expected["required_gates"]).issubset(gate_ids),
            f"{actual['id']}: missing required gates",
        )

    # Reachability and claim selection are intentionally established through the
    # task's real web research. Verification remains offline and does not prescribe
    # particular Broadcom pages or queries.
    if failures:
        raise VerificationError("semantic verification failed:\n- " + "\n- ".join(failures))


def verify_stdlib_package() -> None:
    package_dir = ROOT / PACKAGE
    required = {"__init__.py", "__main__.py", "planner.py"}
    if not package_dir.is_dir():
        raise VerificationError(f"missing package directory: {PACKAGE}")
    present = {path.name for path in package_dir.iterdir() if path.is_file()}
    missing = required - present
    if missing:
        raise VerificationError(f"package is missing required modules: {sorted(missing)}")

    stdlib = set(sys.stdlib_module_names)
    for python_file in sorted(package_dir.glob("*.py")):
        try:
            tree = ast.parse(python_file.read_text(encoding="utf-8"), filename=str(python_file))
        except SyntaxError as exc:
            raise VerificationError(f"syntax error in {python_file.name}: {exc}") from exc
        for node in ast.walk(tree):
            module: str | None = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".", 1)[0]
                    if top not in stdlib and top != PACKAGE:
                        raise VerificationError(
                            f"non-stdlib import {alias.name!r} in {python_file.name}"
                        )
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                module = node.module.split(".", 1)[0]
                if module not in stdlib and module != PACKAGE:
                    raise VerificationError(
                        f"non-stdlib import {node.module!r} in {python_file.name}"
                    )


def verify_reproducible(artifact: dict[str, Any], schema: dict[str, Any]) -> None:
    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temp_dir:
        generated = Path(temp_dir) / "migration_architecture.json"
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        command = [
            sys.executable,
            "-m",
            PACKAGE,
            "--inventory",
            INVENTORY_PATH.name,
            "--compatibility",
            SNAPSHOT_PATH.name,
            "--output",
            str(generated),
        ]
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise VerificationError(f"package CLI failed ({result.returncode}): {detail}")
        regenerated = load_json(generated)
        validate_schema(regenerated, schema, schema)
        if regenerated != artifact:
            raise VerificationError(
                "package CLI output is not identical to migration_architecture.json"
            )


def main() -> int:
    try:
        # The artifact is validated against the installer specification before the
        # fixture, compatibility authority, package, or semantic checks are read.
        schema = load_json(SCHEMA_PATH)
        artifact = load_json(ARTIFACT_PATH)
        validate_schema(artifact, schema, schema)

        inventory = load_json(INVENTORY_PATH)
        snapshot = load_json(SNAPSHOT_PATH)
        verify_architecture(artifact, inventory, snapshot)
        verify_stdlib_package()
        verify_reproducible(artifact, schema)
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: schema, architecture, compatibility, package, and reproducibility checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
