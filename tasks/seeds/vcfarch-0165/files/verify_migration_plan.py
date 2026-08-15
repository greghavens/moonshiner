"""Protected, offline verifier for the VCF migration architecture artifact."""

from __future__ import annotations

import ast
import importlib
import json
import re
import subprocess
import sys
import tempfile
import tomllib
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent


class VerificationError(Exception):
    pass


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VerificationError(f"missing required file: {path.name}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(f"invalid JSON in {path.name}: {exc}") from exc


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
    raise VerificationError(f"schema uses unsupported type {expected!r}")


def resolve_ref(root_schema: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise VerificationError(f"schema uses non-local reference {ref!r}")
    node: Any = root_schema
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            raise VerificationError(f"schema reference cannot be resolved: {ref!r}")
        node = node[part]
    if not isinstance(node, dict):
        raise VerificationError(f"schema reference is not an object: {ref!r}")
    return node


def validate_schema(
    instance: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    path: str = "$",
) -> list[str]:
    """Validate the documented JSON-Schema subset used by installer_spec.json."""
    if "$ref" in schema:
        return validate_schema(instance, resolve_ref(root_schema, schema["$ref"]), root_schema, path)

    errors: list[str] = []
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: value {instance!r} is not in the allowed enum")

    expected_type = schema.get("type")
    if expected_type is not None:
        if not isinstance(expected_type, str):
            raise VerificationError(f"schema type at {path} must be a string")
        if not json_type_matches(instance, expected_type):
            errors.append(f"{path}: expected {expected_type}, got {type(instance).__name__}")
            return errors

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                errors.append(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in instance:
                if key not in properties:
                    errors.append(f"{path}: additional property {key!r} is not allowed")
        for key, child_schema in properties.items():
            if key in instance:
                errors.extend(validate_schema(instance[key], child_schema, root_schema, f"{path}.{key}"))

    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            errors.append(f"{path}: has fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: has more than {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(encoded) != len(set(encoded)):
                errors.append(f"{path}: array items are not unique")
        child_schema = schema.get("items")
        if child_schema is not None:
            for index, item in enumerate(instance):
                errors.extend(validate_schema(item, child_schema, root_schema, f"{path}[{index}]"))

    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            errors.append(f"{path}: string is shorter than {schema['minLength']}")
        pattern = schema.get("pattern")
        if pattern is not None and re.search(pattern, instance) is None:
            errors.append(f"{path}: string does not match {pattern!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: value is less than {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: value is greater than {schema['maximum']}")
    return errors


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def keyed(items: list[dict[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        item_key = item[key]
        require(item_key not in result, f"duplicate {label} {item_key!r}")
        result[item_key] = item
    return result


def verify_semantics(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    require(plan["inventory_revision"] == inventory["inventory_revision"], "inventory revision mismatch")
    require(plan["compatibility_snapshot_id"] == snapshot["snapshot_id"], "compatibility snapshot mismatch")
    require(plan["planning_date"] == inventory["planning_date"], "planning date mismatch")
    require(plan["target_release"] == snapshot["target_release"], "target release mismatch")

    research_topics: set[str] = set()
    research_urls: set[str] = set()
    planning_date = date.fromisoformat(plan["planning_date"])
    for record in plan["research"]:
        parsed = urlsplit(record["url"])
        hostname = (parsed.hostname or "").lower().rstrip(".")
        require(
            parsed.scheme == "https"
            and parsed.username is None
            and parsed.password is None
            and (hostname == "broadcom.com" or hostname.endswith(".broadcom.com")),
            f"research source is not a Broadcom-published HTTPS page: {record['url']!r}",
        )
        require(record["url"] not in research_urls, f"duplicate research URL {record['url']!r}")
        research_urls.add(record["url"])
        require(
            date.fromisoformat(record["consulted_on"]) <= planning_date,
            f"research consultation occurs after the planning date: {record['url']!r}",
        )
        research_topics.update(record["topics"])
    required_research_topics = {
        "migration-path",
        "content-compatibility",
        "sizing",
        "lifecycle-boundary",
    }
    require(
        required_research_topics <= research_topics,
        "research does not cover migration paths, content compatibility, sizing, and lifecycle boundaries",
    )

    protected = inventory["fleet"]["protected_management_domain"]
    preservation = plan["management_domain_preservation"]
    require(preservation["domain_id"] == protected["domain_id"], "wrong management domain in preservation design")
    require(preservation["changes"] == snapshot["placement_rule"]["management_domain_changes"], "management domain must remain unchanged")

    target_domain = snapshot["placement_rule"]["target_domain_id"]
    target_cluster = snapshot["placement_rule"]["target_cluster_id"]
    require(inventory["lifecycle"]["placement_domain_id"] == target_domain, "source lifecycle appliance must be outside the management domain")
    require(inventory["lifecycle"]["placement_cluster_id"] == target_cluster, "source lifecycle appliance placement mismatch")
    wld = inventory["fleet"]["new_workload_domain"]
    allowed_networks = set(wld["networks"])
    expected_components = keyed(snapshot["target_components"], "component_id", "snapshot component")
    actual_components = keyed(plan["target_components"], "component_id", "plan component")
    require(set(actual_components) == set(expected_components), "target component set does not match the pinned snapshot")
    comparison_fields = {
        "component", "version", "size", "node_count", "vcpu_per_node",
        "memory_gib_per_node", "storage_gib_per_node", "capacity",
    }
    for component_id, expected in expected_components.items():
        actual = actual_components[component_id]
        for field in comparison_fields:
            require(actual[field] == expected[field], f"{component_id}: {field} differs from pinned sizing")
        require(actual["domain_id"] == target_domain, f"{component_id}: target must be in the new workload domain")
        require(actual["cluster_id"] == target_cluster, f"{component_id}: target must use the workload-domain cluster")
        require(actual["domain_id"] != protected["domain_id"], f"{component_id}: management-domain placement is forbidden")
        require(set(actual["network_ids"]) <= allowed_networks and actual["network_ids"], f"{component_id}: invalid target network placement")

    available = {
        "vcpu": wld["placement_cluster"]["available_vcpu"],
        "memory_gib": wld["placement_cluster"]["available_memory_gib"],
        "storage_gib": wld["placement_cluster"]["available_storage_gib"],
    }
    planned = {
        "vcpu": sum(c["node_count"] * c["vcpu_per_node"] for c in actual_components.values()),
        "memory_gib": sum(c["node_count"] * c["memory_gib_per_node"] for c in actual_components.values()),
        "storage_gib": sum(c["node_count"] * c["storage_gib_per_node"] for c in actual_components.values()),
    }
    summary = plan["capacity_summary"]
    require(summary["domain_id"] == target_domain and summary["cluster_id"] == target_cluster, "capacity summary placement mismatch")
    require(summary["available"] == available, "available capacity must come from the inventory")
    require(summary["planned"] == planned, "planned capacity does not equal component sizing")
    for dimension in available:
        require(planned[dimension] <= available[dimension], f"planned {dimension} exceeds workload-domain capacity")

    source_inventory = keyed(inventory["source_products"], "source_id", "inventory source")
    expected_paths = keyed(snapshot["source_paths"], "source_id", "snapshot source path")
    actual_paths = keyed(plan["source_migrations"], "source_id", "plan source migration")
    require(set(actual_paths) == set(source_inventory) == set(expected_paths), "every and only inventoried source must be mapped")
    scalar_fields = {
        "source_product", "source_version", "target_component_id", "target_component",
        "target_version", "migration_mode",
    }
    for source_id, source in source_inventory.items():
        expected = expected_paths[source_id]
        actual = actual_paths[source_id]
        require(source["product"] == actual["source_product"], f"{source_id}: source product name mismatch")
        require(source["former_name"] == actual["source_former_name"], f"{source_id}: former product name mismatch")
        require(source["version"] == actual["source_version"], f"{source_id}: source version mismatch")
        for field in scalar_fields:
            require(actual[field] == expected[field], f"{source_id}: {field} differs from pinned path")
        require(
            actual["support_boundary"]["end_of_general_support"] == expected["end_of_general_support"],
            f"{source_id}: end-of-support boundary mismatch",
        )
        actual_carried = set(actual["carried_item_ids"])
        actual_recreated = set(actual["recreated_item_ids"])
        actual_abandoned = {item["item_id"] for item in actual["abandoned_items"]}
        require(actual_carried == set(expected["carried_item_ids"]), f"{source_id}: carried items mismatch")
        require(actual_recreated == set(expected["recreated_item_ids"]), f"{source_id}: recreated items mismatch")
        require(actual_abandoned == set(expected["abandoned_item_ids"]), f"{source_id}: abandoned items mismatch")
        require(not (actual_carried & actual_recreated or actual_carried & actual_abandoned or actual_recreated & actual_abandoned), f"{source_id}: item classifications overlap")
        inventory_items = {item["item_id"] for item in source["items"]}
        require(actual_carried | actual_recreated | actual_abandoned == inventory_items, f"{source_id}: every inventoried item must be classified exactly once")

    ops_demand = source_inventory["aria-ops-prod"]["demand"]
    for component_id in ("vcf-operations", "operations-collector"):
        capacity = actual_components[component_id]["capacity"]
        require(capacity["objects"] >= ops_demand["objects"], f"{component_id}: object capacity is too small")
        require(capacity["collected_metrics"] >= ops_demand["collected_metrics"], f"{component_id}: metric capacity is too small")
    logs_demand = source_inventory["aria-logs-prod"]["demand"]
    logs_capacity = actual_components["vcf-operations-for-logs"]["capacity"]
    require(logs_capacity["ingestion_gib_per_day"] >= logs_demand["ingestion_gib_per_day"], "logs ingestion sizing is too small")
    require(logs_capacity["active_connections"] >= logs_demand["active_syslog_connections"], "logs connection sizing is too small")

    actual_steps = plan["steps"]
    expected_steps = snapshot["required_sequence"]
    require([step["order"] for step in actual_steps] == list(range(1, len(expected_steps) + 1)), "steps must be consecutively ordered")
    require(len(actual_steps) == len(expected_steps), "migration sequence length mismatch")
    valid_component_ids = set(actual_components)
    for actual, expected in zip(actual_steps, expected_steps):
        for field in ("order", "step_id", "source_ids", "gate_ids"):
            require(actual[field] == expected[field], f"step {expected['order']}: {field} differs from pinned sequence")
        require(actual["target_component_ids"], f"step {expected['order']}: target components are required")
        require(set(actual["target_component_ids"]) <= valid_component_ids, f"step {expected['order']}: unknown target component")

    required_gate_ids = {gate for step in expected_steps for gate in step["gate_ids"]}
    gate_catalog = keyed(plan["gates"], "gate_id", "gate")
    require(set(gate_catalog) == required_gate_ids, "gate catalog must contain exactly the pinned technical gates")
    forbidden_gate_terms = re.compile(r"approval|eligib|budget|spend|cost|ceiling", re.IGNORECASE)
    for gate_id, gate in gate_catalog.items():
        require(forbidden_gate_terms.search(gate_id + " " + gate["evidence"]) is None, f"{gate_id}: unrequested nontechnical gate")


def verify_stdlib_package(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    package_dir = ROOT / "vcf_arch"
    require(package_dir.is_dir() and not package_dir.is_symlink(), "vcf_arch must be a local package directory")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    require(project.get("project", {}).get("dependencies") == [], "pyproject runtime dependencies must be empty")

    python_files = sorted(package_dir.rglob("*.py"))
    require((package_dir / "__init__.py") in python_files, "vcf_arch must be a regular Python package")
    require((package_dir / "__main__.py") in python_files, "vcf_arch CLI entry point is missing")
    for path in python_files:
        require(not path.is_symlink(), f"package source must not be a symlink: {path.relative_to(ROOT)}")
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                require(name in sys.stdlib_module_names or name == "vcf_arch", f"non-stdlib import {name!r} in {path.name}")

    module = importlib.import_module("vcf_arch")
    builder = getattr(module, "build_plan", None)
    require(callable(builder), "vcf_arch.build_plan is not callable")
    generated = builder(inventory, snapshot)
    require(generated == plan, "build_plan does not reproduce migration_plan.json")

    with tempfile.TemporaryDirectory(prefix=".vcf-verify-", dir=ROOT) as temporary_dir:
        temporary_output = Path(temporary_dir) / "migration_plan.json"
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "vcf_arch",
                "--inventory",
                "fixtures/estate_inventory.json",
                "--compatibility",
                "fixtures/compatibility_snapshot.json",
                "--output",
                str(temporary_output),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
        require(completed.returncode == 0, f"vcf_arch CLI failed: {completed.stderr.strip()}")
        require(load_json(temporary_output) == plan, "vcf_arch CLI output differs from migration_plan.json")


def main() -> int:
    try:
        installer = load_json(ROOT / "installer_spec.json")
        artifact = load_json(ROOT / installer["artifact_path"])

        # Binding order: schema validation is complete before fixtures, snapshot,
        # package source, or architectural semantics are inspected.
        schema = installer["schema"]
        schema_errors = validate_schema(artifact, schema, schema)
        if schema_errors:
            detail = "\n".join(f"  - {error}" for error in schema_errors[:30])
            raise VerificationError(f"artifact does not conform to installer schema:\n{detail}")

        inventory = load_json(ROOT / "fixtures" / "estate_inventory.json")
        snapshot = load_json(ROOT / "fixtures" / "compatibility_snapshot.json")
        verify_semantics(artifact, inventory, snapshot)
        verify_stdlib_package(artifact, inventory, snapshot)
    except (VerificationError, KeyError, TypeError, ValueError, SyntaxError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: schema-valid, offline VCF migration architecture verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
