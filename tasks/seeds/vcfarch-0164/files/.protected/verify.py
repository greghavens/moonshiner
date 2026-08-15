#!/usr/bin/env python3
"""Offline verifier for the VCF management-component migration architecture."""

from __future__ import annotations

import ast
import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "migration-plan.json"
SPEC_PATH = ROOT / "spec" / "installer-spec.json"
SNAPSHOT_PATH = ROOT / "spec" / "compatibility-snapshot.json"
INVENTORY_PATH = ROOT / "fixtures" / "estate.json"
PACKAGE = ROOT / "vcf_migration"
PROJECT = ROOT / "pyproject.toml"
RESEARCH_PATH = ROOT / "research" / "consulted.json"


class VerificationError(AssertionError):
    """Raised for a deterministic verification failure."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> Any:
    try:
        label = path.relative_to(ROOT)
    except ValueError:
        label = Path(path.name)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise VerificationError(f"required file is missing: {label}") from None
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VerificationError(f"invalid JSON in {label}: {error}") from None


# ---------------------------------------------------------------------------
# Focused JSON Schema 2020-12 validator for the keywords used by the installer
# ---------------------------------------------------------------------------


def _is_type(value: Any, expected: str) -> bool:
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
    raise VerificationError(f"installer schema uses unsupported type {expected!r}")


def validate_schema(value: Any, schema: dict[str, Any], path: str = "$") -> None:
    """Validate *value* against the installer schema, failing on unknown keywords."""
    supported = {
        "$schema",
        "type",
        "const",
        "enum",
        "additionalProperties",
        "required",
        "properties",
        "items",
        "minItems",
        "uniqueItems",
        "minLength",
        "minimum",
        "pattern",
    }
    unknown = set(schema) - supported
    require(not unknown, f"installer schema has unsupported keywords at {path}: {sorted(unknown)}")

    if "type" in schema:
        require(_is_type(value, schema["type"]), f"{path} must be {schema['type']}")
    if "const" in schema:
        require(value == schema["const"], f"{path} must equal {schema['const']!r}")
    if "enum" in schema:
        require(value in schema["enum"], f"{path} is not an allowed value")

    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [name for name in required if name not in value]
        require(not missing, f"{path} is missing required properties: {missing}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(properties)
            require(not extra, f"{path} has unexpected properties: {sorted(extra)}")
        for name, child in properties.items():
            if name in value:
                validate_schema(value[name], child, f"{path}.{name}")

    if isinstance(value, list):
        require(len(value) >= schema.get("minItems", 0), f"{path} has too few items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            require(len(encoded) == len(set(encoded)), f"{path} contains duplicate items")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(value):
                validate_schema(item, item_schema, f"{path}[{index}]")

    if isinstance(value, str):
        require(len(value) >= schema.get("minLength", 0), f"{path} is too short")
        if "pattern" in schema:
            require(re.search(schema["pattern"], value) is not None, f"{path} has invalid format")

    if isinstance(value, (int, float)) and not isinstance(value, bool) and "minimum" in schema:
        require(value >= schema["minimum"], f"{path} is below its minimum")


def verify_research_record() -> None:
    """Validate the deterministic artifact left by the required live research."""
    consulted = load_json(RESEARCH_PATH)
    require(isinstance(consulted, list), "research/consulted.json must contain a JSON array")
    require(len(consulted) >= 2, "research/consulted.json must record multiple sources")

    urls: set[str] = set()
    date_pattern = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    for index, source in enumerate(consulted):
        label = f"research/consulted.json[{index}]"
        require(isinstance(source, dict), f"{label} must be an object")
        missing = {"title", "url", "accessed_on", "facts"} - set(source)
        require(not missing, f"{label} is missing fields: {sorted(missing)}")

        title = source["title"]
        url = source["url"]
        accessed_on = source["accessed_on"]
        facts = source["facts"]
        require(isinstance(title, str) and title.strip(), f"{label}.title must be nonempty")
        require(isinstance(url, str) and url.strip(), f"{label}.url must be nonempty")
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        published_by_broadcom = hostname in {"broadcom.com", "vmware.com"} or hostname.endswith(
            (".broadcom.com", ".vmware.com")
        )
        require(
            parsed.scheme == "https" and published_by_broadcom,
            f"{label}.url must be an HTTPS Broadcom-published source",
        )
        require(url not in urls, f"{label}.url duplicates an earlier source")
        urls.add(url)
        require(
            isinstance(accessed_on, str) and date_pattern.fullmatch(accessed_on) is not None,
            f"{label}.accessed_on must use YYYY-MM-DD",
        )
        require(isinstance(facts, list) and facts, f"{label}.facts must be a nonempty array")
        require(
            all(isinstance(fact, str) and fact.strip() for fact in facts),
            f"{label}.facts must contain nonempty strings",
        )


# ---------------------------------------------------------------------------
# Protected fixture and snapshot semantics
# ---------------------------------------------------------------------------


def verify_architecture(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    architecture = plan["architecture"]
    site = inventory["site"]
    platform = snapshot["platform"]
    require(site["count"] == 1, "fixture must remain single-site")
    require(len(site["hosts"]) == site["host_count"] == 4, "fixture must retain four hosts")
    require(architecture["topology"] == site["topology"] == platform["topology"], "topology mismatch")
    require(architecture["site_id"] == site["id"], "site placement mismatch")
    require(architecture["host_count"] == site["host_count"], "plan must hold at four hosts")
    require(
        architecture["minimum_supported_host_count"] == platform["minimum_supported_host_count"] == 4,
        "minimum supported host count mismatch",
    )
    require(architecture["domain_type"] == platform["domain_type"], "domain type mismatch")
    require(architecture["domain_id"] == site["management_domain_id"], "domain placement mismatch")
    require(architecture["cluster_id"] == site["management_cluster_id"], "cluster placement mismatch")
    require(architecture["resource_pool"] == site["management_resource_pool"], "resource-pool mismatch")
    require(architecture["principal_storage"] == site["principal_storage"], "storage mismatch")
    require(architecture["failure_domain"] == platform["failure_domain"], "failure domain mismatch")
    require(architecture["placement_policy"] == platform["placement_policy"], "placement policy mismatch")

    expected_profiles = platform["deployment_profile"]
    actual = architecture["deployments"]
    expected_by_component = {item["component"]: item for item in expected_profiles}
    actual_by_component = {item["component"]: item for item in actual}
    require(len(expected_by_component) == len(expected_profiles), "snapshot target components are not unique")
    require(len(actual_by_component) == len(actual), "plan target components are not unique")
    require(set(actual_by_component) == set(expected_by_component), "plan target components differ from profile")
    node_ids: list[str] = []
    totals = {"vcpus": 0, "memory_gib": 0, "storage_gib": 0}
    for component, expected in expected_by_component.items():
        deployment = actual_by_component[component]
        require(deployment["version"] == expected["version"], f"version mismatch for {component}")
        require(deployment["domain_id"] == site["management_domain_id"], "component outside management domain")
        require(deployment["cluster_id"] == site["management_cluster_id"], "component outside consolidated cluster")
        require(deployment["resource_pool"] == site["management_resource_pool"], "component in wrong resource pool")
        require(deployment["failure_domain"] == "host", "component failure domain must be host")
        require(deployment["anti_affinity"] == "required", "component anti-affinity must be required")
        expected_nodes = {node["role"]: node for node in expected["nodes"]}
        projected_nodes = {
            node["role"]: {
                name: node[name] for name in ("role", "vcpus", "memory_gib", "storage_gib")
            }
            for node in deployment["nodes"]
        }
        require(len(expected_nodes) == len(expected["nodes"]), f"snapshot roles repeat for {component}")
        require(len(projected_nodes) == len(deployment["nodes"]), f"plan roles repeat for {component}")
        require(projected_nodes == expected_nodes, f"sizing mismatch for {component}")
        node_ids.extend(node["id"] for node in deployment["nodes"])
        for node in deployment["nodes"]:
            for name in totals:
                totals[name] += node[name]
    require(len(node_ids) == len(set(node_ids)), "target node ids must be unique")
    require(architecture["capacity"] == totals, "aggregate component capacity is not the node sum")
    require(totals["vcpus"] <= sum(host["cpu_cores"] for host in site["hosts"]), "vCPU plan exceeds fixture cores")
    require(totals["memory_gib"] <= sum(host["memory_gib"] for host in site["hosts"]), "memory plan exceeds fixture capacity")
    require(totals["storage_gib"] <= sum(host["usable_storage_gib"] for host in site["hosts"]), "storage plan exceeds fixture capacity")


def verify_migrations(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    products = inventory["products"]
    paths = snapshot["migration_paths"]
    require(len(products) == len(paths) == len(plan["migrations"]) == 3, "exactly three products must migrate")
    product_by_id = {item["id"]: item for item in products}
    require(len(product_by_id) == len(products), "fixture product ids are not unique")

    expected_support = {
        path["source_id"]: {
            "source_id": path["source_id"],
            "product": path["source_product"],
            "version": path["source_version"],
            "end_of_general_support": path["end_of_general_support"],
        }
        for path in paths
    }
    actual_support = {boundary["source_id"]: boundary for boundary in plan["support_boundaries"]}
    require(len(expected_support) == len(paths), "snapshot support source ids are not unique")
    require(
        len(actual_support) == len(plan["support_boundaries"]),
        "plan support source ids are not unique",
    )
    require(actual_support == expected_support, "support boundaries differ from snapshot")

    all_fixture_content: set[str] = set()
    seen_dispositions: list[str] = []
    for product in products:
        ids = [item["id"] for item in product["content"]]
        require(len(ids) == len(set(ids)), f"duplicate fixture content in {product['id']}")
        require(not all_fixture_content.intersection(ids), "content ids must be estate-wide unique")
        all_fixture_content.update(ids)

    migration_ids = [migration["id"] for migration in plan["migrations"]]
    require(len(migration_ids) == len(set(migration_ids)), "migration ids must be unique")
    migration_by_source = {migration["source"]["id"]: migration for migration in plan["migrations"]}
    require(len(migration_by_source) == len(plan["migrations"]), "migration source ids must be unique")
    require(set(migration_by_source) == set(product_by_id), "migration sources differ from the estate")

    for path in paths:
        migration = migration_by_source[path["source_id"]]
        product = product_by_id[path["source_id"]]
        require(
            migration["source"]
            == {"id": product["id"], "product": product["product"], "version": product["version"]},
            "migration does not name the exact fixture source",
        )
        require(product["product"] == path["source_product"], "snapshot source product mismatch")
        require(product["version"] == path["source_version"], "snapshot source version mismatch")
        require(
            migration["target"]
            == {"component": path["target_component"], "version": path["target_version"]},
            "migration target mismatch",
        )
        require(migration["method"] == path["method"], "unsupported migration method")
        require(
            set(migration["prerequisites"]) == set(path["prerequisites"]),
            "migration prerequisites mismatch",
        )
        carry = {
            item["content_id"]: {"content_id": item["content_id"], "handling": item["handling"]}
            for item in path["content_dispositions"]
            if item["outcome"] == "carry"
        }
        abandoned = {
            item["content_id"]: {"content_id": item["content_id"], "handling": item["handling"]}
            for item in path["content_dispositions"]
            if item["outcome"] == "abandon"
        }
        actual_carry = {item["content_id"]: item for item in migration["carry_forward"]}
        actual_abandoned = {item["content_id"]: item for item in migration["abandoned"]}
        require(
            len(actual_carry) == len(migration["carry_forward"]),
            "carry-forward content ids must be unique",
        )
        require(
            len(actual_abandoned) == len(migration["abandoned"]),
            "abandoned content ids must be unique",
        )
        require(actual_carry == carry, "carry-forward mapping differs from snapshot")
        require(actual_abandoned == abandoned, "abandonment mapping differs from snapshot")
        seen_dispositions.extend([*actual_carry, *actual_abandoned])

    require(len(seen_dispositions) == len(set(seen_dispositions)), "a content item has multiple dispositions")
    require(set(seen_dispositions) == all_fixture_content, "not every inventoried content item has a disposition")


def verify_steps(plan: dict[str, Any], snapshot: dict[str, Any]) -> None:
    expected = snapshot["execution_steps"]
    actual = plan["steps"]
    require([step["order"] for step in actual] == list(range(1, len(actual) + 1)), "step order is not contiguous")
    require(len(actual) == len(expected), "step count differs from the pinned sequence")
    for step, expected_step in zip(actual, expected, strict=True):
        require(
            (step["id"], step["order"]) == (expected_step["id"], expected_step["order"]),
            "ordered step ids differ from the pinned sequence",
        )
        require(set(step["source_ids"]) == set(expected_step["source_ids"]), "step sources differ")
        require(
            set(step["target_components"]) == set(expected_step["target_components"]),
            "step targets differ",
        )
        require(set(step["gates"]) == set(expected_step["gates"]), "step gates differ")
        require(step["action"].strip(), f"{step['id']} has no architectural action")
        require(all(item.strip() for item in step["exit_evidence"]), f"{step['id']} has blank exit evidence")


# ---------------------------------------------------------------------------
# Package and deterministic CLI checks
# ---------------------------------------------------------------------------


def imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def verify_package_shape() -> None:
    required = [PACKAGE / "__init__.py", PACKAGE / "__main__.py", PACKAGE / "planner.py"]
    for path in required:
        require(path.is_file(), f"package file missing: {path.relative_to(ROOT)}")
    require(PROJECT.is_file(), "pyproject.toml is missing")
    project_text = PROJECT.read_text(encoding="utf-8")
    require("dependencies = []" in project_text, "project must have no dependencies")
    for path in required:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        external = imported_roots(tree) - sys.stdlib_module_names - {"vcf_migration"}
        require(not external, f"non-stdlib imports in {path.name}: {sorted(external)}")


def verify_builder_and_cli(
    checked_plan: dict[str, Any],
    schema: dict[str, Any],
    inventory: dict[str, Any],
    snapshot: dict[str, Any],
    installer_spec: dict[str, Any],
) -> None:
    sys.path.insert(0, str(ROOT))
    try:
        module = importlib.import_module("vcf_migration")
        built = module.build_plan(inventory, snapshot, installer_spec)
    finally:
        sys.path.pop(0)
    validate_schema(built, schema)
    require(built == checked_plan, "build_plan output differs from migration-plan.json")

    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temp_dir:
        temp_root = Path(temp_dir)
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"

        def invoke_cli(inventory_path: Path, snapshot_path: Path, output_path: Path) -> dict[str, Any]:
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "vcf_migration",
                    "--inventory",
                    str(inventory_path),
                    "--snapshot",
                    str(snapshot_path),
                    "--spec",
                    str(SPEC_PATH),
                    "--output",
                    str(output_path),
                ],
                cwd=ROOT,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=20,
                check=False,
            )
            require(result.returncode == 0, f"CLI failed: {result.stderr.strip()}")
            return load_json(output_path)

        cli_plan = invoke_cli(INVENTORY_PATH, SNAPSHOT_PATH, temp_root / "plan.json")
        validate_schema(cli_plan, schema)
        require(cli_plan == checked_plan, "CLI output is not the checked deterministic artifact")

        # Distinct fixture identifiers prove that both the builder and CLI read their inputs.
        variant_inventory = json.loads(json.dumps(inventory))
        variant_inventory["estate_id"] = "input-sensitive-estate"
        variant_inventory["site"].update(
            {
                "id": "input-sensitive-site",
                "management_domain_id": "input-sensitive-domain",
                "management_cluster_id": "input-sensitive-cluster",
                "management_resource_pool": "input-sensitive-pool",
                "principal_storage": "input-sensitive-storage",
            }
        )
        variant_snapshot = json.loads(json.dumps(snapshot))
        variant_snapshot["snapshot_id"] = "input-sensitive-snapshot"
        variant_inventory_path = temp_root / "inventory.json"
        variant_snapshot_path = temp_root / "snapshot.json"
        variant_inventory_path.write_text(json.dumps(variant_inventory), encoding="utf-8")
        variant_snapshot_path.write_text(json.dumps(variant_snapshot), encoding="utf-8")

        variant_built = module.build_plan(variant_inventory, variant_snapshot, installer_spec)
        validate_schema(variant_built, schema)
        require(variant_built["estate_id"] == variant_inventory["estate_id"], "builder ignores estate id")
        require(variant_built["snapshot_id"] == variant_snapshot["snapshot_id"], "builder ignores snapshot id")
        variant_architecture = variant_built["architecture"]
        expected_placement = {
            "site_id": variant_inventory["site"]["id"],
            "domain_id": variant_inventory["site"]["management_domain_id"],
            "cluster_id": variant_inventory["site"]["management_cluster_id"],
            "resource_pool": variant_inventory["site"]["management_resource_pool"],
            "principal_storage": variant_inventory["site"]["principal_storage"],
        }
        require(
            {name: variant_architecture[name] for name in expected_placement} == expected_placement,
            "builder ignores inventory placement",
        )
        variant_cli = invoke_cli(
            variant_inventory_path,
            variant_snapshot_path,
            temp_root / "variant-plan.json",
        )
        validate_schema(variant_cli, schema)
        require(variant_cli == variant_built, "CLI does not build from the supplied input files")


def main() -> int:
    # Mandatory first phase: load the installer schema and validate the artifact.
    # No fixture, snapshot, package, or research path is touched before it passes.
    installer_spec = load_json(SPEC_PATH)
    plan = load_json(PLAN_PATH)
    schema = installer_spec.get("artifact_schema")
    require(isinstance(schema, dict), "installer specification has no artifact_schema")
    validate_schema(plan, schema)

    # Stay offline while grading the artifact produced by the required live research.
    verify_research_record()

    # Only a schema-valid artifact reaches protected semantic verification.
    inventory = load_json(INVENTORY_PATH)
    snapshot = load_json(SNAPSHOT_PATH)
    require(plan["schema_version"] == "1.0.0", "artifact schema version mismatch")
    require(plan["estate_id"] == inventory["estate_id"], "estate id mismatch")
    require(plan["snapshot_id"] == snapshot["snapshot_id"], "snapshot id mismatch")
    verify_architecture(plan, inventory, snapshot)
    verify_migrations(plan, inventory, snapshot)
    verify_steps(plan, snapshot)
    verify_package_shape()
    verify_builder_and_cli(plan, schema, inventory, snapshot, installer_spec)
    print("verification passed: research, schema, architecture, compatibility, ordering, and CLI")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as error:
        print(f"verification failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
