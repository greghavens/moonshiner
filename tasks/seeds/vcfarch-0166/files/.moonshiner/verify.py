#!/usr/bin/env python3
"""Deterministic offline acceptance verifier for the VCF architecture artifact."""

from __future__ import annotations

import ast
import datetime as dt
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[1]


class VerificationError(Exception):
    pass


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise VerificationError(f"missing required file: {path.name}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(f"invalid JSON in {path.name}: {exc}") from exc


def json_equal(left: Any, right: Any) -> bool:
    return json.dumps(left, sort_keys=True, separators=(",", ":")) == json.dumps(
        right, sort_keys=True, separators=(",", ":")
    )


def resolve_ref(root_schema: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise VerificationError(f"unsupported schema reference: {ref}")
    value: Any = root_schema
    for token in ref[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        value = value[token]
    if not isinstance(value, dict):
        raise VerificationError(f"schema reference does not resolve to an object: {ref}")
    return value


def type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return False


def validate_schema(
    value: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    path: str = "$",
) -> list[str]:
    """Validate the JSON-Schema subset used by installer_spec.json."""
    if "$ref" in schema:
        return validate_schema(value, resolve_ref(root_schema, schema["$ref"]), root_schema, path)

    errors: list[str] = []
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
        return errors
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value {value!r} is not in {schema['enum']!r}")
        return errors

    expected_type = schema.get("type")
    if expected_type and not type_matches(value, expected_type):
        errors.append(f"{path}: expected {expected_type}, got {type(value).__name__}")
        return errors

    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                errors.append(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    errors.append(f"{path}: unexpected property {key!r}")
        for key, child_schema in properties.items():
            if key in value:
                errors.extend(
                    validate_schema(value[key], child_schema, root_schema, f"{path}.{key}")
                )

    if isinstance(value, list):
        minimum = schema.get("minItems")
        if minimum is not None and len(value) < minimum:
            errors.append(f"{path}: expected at least {minimum} items")
        if schema.get("uniqueItems"):
            canonical = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(canonical) != len(set(canonical)):
                errors.append(f"{path}: items must be unique")
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(value):
                errors.extend(validate_schema(item, item_schema, root_schema, f"{path}[{index}]"))

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path}: string is shorter than minLength")
        pattern = schema.get("pattern")
        if pattern and re.fullmatch(pattern, value) is None:
            errors.append(f"{path}: string does not match {pattern!r}")
        if schema.get("format") == "date":
            try:
                dt.date.fromisoformat(value)
            except ValueError:
                errors.append(f"{path}: expected an ISO-8601 calendar date")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: value is below minimum {schema['minimum']}")

    return errors


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def by_unique(items: list[dict[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        identity = item[key]
        require(identity not in result, f"duplicate {label}: {identity}")
        result[identity] = item
    return result


def verify_mappings(
    plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    sources = by_unique(inventory["source_products"], "id", "inventory source id")
    mappings = by_unique(plan["source_mappings"], "source_id", "source mapping")
    require(set(mappings) == set(sources), "source_mappings must cover every inventory source exactly once")

    steps = by_unique(plan["migration_steps"], "step_id", "step id")
    content_rules = snapshot["content_rules"]
    for source_id, source in sources.items():
        mapping = mappings[source_id]
        rule = snapshot["transition_rules"][source_id]
        require(mapping["source_product"] == source["product"], f"{source_id}: wrong source product")
        require(mapping["source_version"] == source["version"], f"{source_id}: wrong source version")
        require(source["version"] in rule["source_versions"], f"{source_id}: source version not pinned")
        require(mapping["target_component"] == rule["target_component"], f"{source_id}: wrong target")
        require(mapping["target_version"] == rule["target_version"], f"{source_id}: wrong target version")
        require(mapping["transition_mode"] == rule["mode"], f"{source_id}: unsupported transition mode")
        require(
            mapping["transition_mode"] not in rule["prohibited_modes"],
            f"{source_id}: prohibited transition mode",
        )

        expected_boundary = snapshot["support_boundaries"][source_id]
        boundary = mapping["support_boundary"]
        require(boundary["product_line"] == expected_boundary["product_line"], f"{source_id}: product line")
        require(
            boundary["status_on_snapshot_date"] == expected_boundary["status_on_effective_date"],
            f"{source_id}: support status",
        )
        require(
            boundary["end_of_general_support"] == expected_boundary["end_of_general_support"],
            f"{source_id}: support boundary",
        )

        inventory_content = by_unique(source["content"], "id", f"{source_id} content id")
        decisions = by_unique(mapping["content_decisions"], "inventory_id", f"{source_id} decision")
        require(
            set(decisions) == set(inventory_content),
            f"{source_id}: content_decisions must classify every inventory item exactly once",
        )
        for item_id, item in inventory_content.items():
            require(item["type"] in content_rules, f"{source_id}: no pinned rule for {item['type']}")
            expected = content_rules[item["type"]]
            decision = decisions[item_id]
            require(decision["disposition"] == expected["disposition"], f"{item_id}: wrong disposition")
            require(decision["method"] == expected["method"], f"{item_id}: wrong handling method")
            require(decision["step_id"] in steps, f"{item_id}: responsible step does not exist")
            responsible = steps[decision["step_id"]]
            require(source_id in responsible["source_ids"], f"{item_id}: step omits source")
            require(responsible["stage"] == expected["stage"], f"{item_id}: wrong responsible stage")


def verify_steps(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    steps = plan["migration_steps"]
    require([step["sequence"] for step in steps] == list(range(1, len(steps) + 1)), "step sequence")
    indexed = by_unique(steps, "step_id", "step id")
    sequence_of = {step_id: step["sequence"] for step_id, step in indexed.items()}
    source_ids = {source["id"] for source in inventory["source_products"]}
    targets = {component["name"] for component in inventory["target_bundle"]["components"]}

    for step in steps:
        require(set(step["source_ids"]) <= source_ids, f"{step['step_id']}: unknown source")
        require(set(step["target_components"]) <= targets, f"{step['step_id']}: unknown target")
        require(
            len(re.findall(r"[A-Za-z0-9][A-Za-z0-9+./_-]*", step["action"])) >= 10,
            f"{step['step_id']}: action is not concrete",
        )
        for gate in step["gates"]:
            require(
                len(re.findall(r"[A-Za-z0-9][A-Za-z0-9+./_-]*", gate["condition"])) >= 8,
                f"{step['step_id']}: gate condition is not concrete",
            )
            require(
                len(re.findall(r"[A-Za-z0-9][A-Za-z0-9+./_-]*", gate["evidence"])) >= 6,
                f"{step['step_id']}: gate evidence is not concrete",
            )
            require(
                gate["condition"].strip().casefold() != gate["evidence"].strip().casefold(),
                f"{step['step_id']}: gate evidence must identify an artifact",
            )
        for dependency in step["depends_on"]:
            require(dependency in indexed, f"{step['step_id']}: unknown dependency {dependency}")
            require(
                sequence_of[dependency] < step["sequence"],
                f"{step['step_id']}: dependency must precede the step",
            )

    for source_id, rule in snapshot["transition_rules"].items():
        source_steps = [step for step in steps if source_id in step["source_ids"]]
        by_stage: dict[str, list[dict[str, Any]]] = {}
        for step in source_steps:
            by_stage.setdefault(step["stage"], []).append(step)
        for stage in rule["required_stages"]:
            require(
                len(by_stage.get(stage, [])) == 1,
                f"{source_id}: stage {stage} must occur exactly once",
            )
            require(
                rule["target_component"] in by_stage[stage][0]["target_components"],
                f"{source_id}: stage {stage} omits its target component",
            )
        ordered = [by_stage[stage][0] for stage in rule["required_stages"]]
        require(
            [step["sequence"] for step in ordered] == sorted(step["sequence"] for step in ordered),
            f"{source_id}: required stages are out of order",
        )
        for previous, current in zip(ordered, ordered[1:]):
            require(
                previous["step_id"] in current["depends_on"],
                f"{source_id}: {current['step_id']} must directly depend on {previous['step_id']}",
            )
        if rule["mode"] in {"greenfield_export_import", "parallel_fresh_deploy"}:
            require("upgrade" not in by_stage, f"{source_id}: direct upgrade is not supported")


def projected(current: int, growth: int) -> int:
    return math.ceil(current * (100 + growth) / 100)


def verify_topology(
    plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    placements = by_unique(plan["target_topology"]["placements"], "component", "placement")
    target_versions = {
        component["name"]: component["version"]
        for component in inventory["target_bundle"]["components"]
    }
    require(set(placements) == set(target_versions), "one placement is required for every target component")

    domain = inventory["management_domain"]
    clusters = by_unique(domain["clusters"], "id", "cluster")
    datastores = by_unique(domain["datastores"], "id", "datastore")
    networks = by_unique(domain["networks"], "id", "network")
    sources_by_target = {
        snapshot["transition_rules"][source["id"]]["target_component"]: source
        for source in inventory["source_products"]
    }
    growth = inventory["design_requirements"]["growth_headroom_percent"]
    required_metrics = {
        "VCF Operations": ["active_objects", "collected_metrics"],
        "VCF Automation": ["active_deployments", "peak_daily_requests"],
        "VCF Operations for Logs": ["ingestion_gb_per_day", "peak_events_per_second"],
    }
    totals: dict[str, dict[str, float]] = {}
    datastore_usage: dict[str, float] = {}

    for component, placement in placements.items():
        rule = snapshot["placement_rules"][component]
        source = sources_by_target[component]
        require(placement["version"] == target_versions[component], f"{component}: wrong version")
        require(placement["cluster_id"] in clusters, f"{component}: unknown cluster")
        cluster = clusters[placement["cluster_id"]]
        require(placement["datastore_id"] in datastores, f"{component}: unknown datastore")
        datastore = datastores[placement["datastore_id"]]
        require(datastore["cluster_id"] == placement["cluster_id"], f"{component}: datastore placement")
        require(set(placement["network_ids"]) <= set(networks), f"{component}: unknown network")
        for network_id in placement["network_ids"]:
            require(networks[network_id]["cluster_id"] == placement["cluster_id"], f"{component}: network")
        require(set(rule["required_networks"]) <= set(placement["network_ids"]), f"{component}: networks")
        require(placement["node_count"] >= rule["minimum_nodes"], f"{component}: too few nodes")
        require(placement["profile"] in rule["profiles"], f"{component}: unknown profile")
        profile = rule["profiles"][placement["profile"]]
        expected_resources = {
            "vcpu": profile["vcpu_per_node"],
            "memory_gb": profile["memory_gb_per_node"],
            "data_disk_tb": profile["data_disk_tb_per_node"],
        }
        require(placement["per_node"] == expected_resources, f"{component}: profile resources do not match")
        availability = placement["availability"]
        require(availability["mode"] == rule["availability"], f"{component}: availability mode")
        require(availability["anti_affinity"] is True, f"{component}: anti-affinity is required")
        require(
            len(availability["fault_domains"]) >= placement["node_count"],
            f"{component}: insufficient fault domains",
        )
        require(
            set(availability["fault_domains"]) <= set(cluster["host_ids"]),
            f"{component}: unknown fault domain",
        )

        sizing = by_unique(placement["sizing_inputs"], "metric", f"{component} sizing metric")
        require(set(sizing) == set(required_metrics[component]), f"{component}: sizing inputs")
        for metric in required_metrics[component]:
            entry = sizing[metric]
            current = source["load"][metric]
            require(entry["current"] == current, f"{component}: current {metric}")
            require(entry["growth_percent"] == growth, f"{component}: growth headroom")
            require(entry["projected"] == projected(current, growth), f"{component}: projected {metric}")

        nodes = placement["node_count"]
        if component == "VCF Operations":
            factor = rule["ha_capacity_factor"]
            require(
                nodes * profile["objects_per_node"] * factor >= sizing["active_objects"]["projected"],
                "VCF Operations: object capacity is undersized",
            )
            require(
                nodes * profile["metrics_per_node"] * factor >= sizing["collected_metrics"]["projected"],
                "VCF Operations: metric capacity is undersized",
            )
        elif component == "VCF Automation":
            require(
                profile["max_active_deployments"] >= sizing["active_deployments"]["projected"],
                "VCF Automation: deployment capacity is undersized",
            )
            require(
                profile["max_peak_daily_requests"] >= sizing["peak_daily_requests"]["projected"],
                "VCF Automation: request capacity is undersized",
            )
        else:
            require(
                nodes * profile["ingestion_gb_per_day_per_node"]
                >= sizing["ingestion_gb_per_day"]["projected"],
                "VCF Operations for Logs: daily ingestion is undersized",
            )
            require(
                nodes * profile["events_per_second_per_node"]
                >= sizing["peak_events_per_second"]["projected"],
                "VCF Operations for Logs: EPS capacity is undersized",
            )

        total = totals.setdefault(placement["cluster_id"], {"vcpu": 0.0, "memory": 0.0})
        total["vcpu"] += nodes * profile["vcpu_per_node"]
        total["memory"] += nodes * profile["memory_gb_per_node"]
        datastore_usage[placement["datastore_id"]] = datastore_usage.get(placement["datastore_id"], 0) + (
            nodes * profile["data_disk_tb_per_node"]
        )

    for cluster_id, used in totals.items():
        cluster = clusters[cluster_id]
        require(used["vcpu"] <= cluster["available_vcpu"], f"{cluster_id}: insufficient vCPU")
        require(used["memory"] <= cluster["available_memory_gb"], f"{cluster_id}: insufficient memory")
    for datastore_id, used_tb in datastore_usage.items():
        require(used_tb <= datastores[datastore_id]["free_tb"], f"{datastore_id}: insufficient storage")


def verify_stdlib_package(package_dir: Path) -> None:
    require(package_dir.is_dir(), "missing vcf_architect package")
    python_files = sorted(package_dir.rglob("*.py"))
    require(bool(python_files), "vcf_architect package contains no Python files")
    allowed = set(sys.stdlib_module_names) | {"vcf_architect"}
    for path in python_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            raise VerificationError(f"syntax error in {path.relative_to(ROOT)}: {exc}") from exc
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = [node.module.split(".", 1)[0]]
            else:
                continue
            for imported in roots:
                require(imported in allowed, f"non-stdlib import {imported!r} in {path.relative_to(ROOT)}")


def verify_consulted_sources(root: Path) -> None:
    sources = load_json(root / "consulted_sources.json")
    require(isinstance(sources, list) and bool(sources), "consulted_sources.json must be a non-empty array")

    required = {"title", "publisher", "url", "consulted_on", "topics", "finding"}
    coverage: set[str] = set()
    products: set[str] = set()
    coverage_terms = {
        "migration": ("migrat", "upgrad", "path", "transition"),
        "compatibility": ("compatib", "content", "configur", "integration", "agent"),
        "sizing": ("siz", "capacit", "placement", "profile", "scale"),
        "support": ("support", "lifecycle", "eogs", "end-of-general-support"),
    }

    for index, source in enumerate(sources):
        label = f"consulted_sources[{index}]"
        require(isinstance(source, dict), f"{label} must be an object")
        require(required <= set(source), f"{label} is missing required fields")

        title = source["title"]
        publisher = source["publisher"]
        url = source["url"]
        consulted_on = source["consulted_on"]
        topics = source["topics"]
        finding = source["finding"]
        require(isinstance(title, str) and bool(title.strip()), f"{label}: invalid title")
        require(
            isinstance(publisher, str) and "broadcom" in publisher.casefold(),
            f"{label}: publisher must identify Broadcom",
        )
        require(isinstance(url, str), f"{label}: url must be a string")
        parsed = urlsplit(url)
        host = (parsed.hostname or "").casefold()
        require(parsed.scheme == "https", f"{label}: source URL must use HTTPS")
        require(
            host == "broadcom.com" or host.endswith(".broadcom.com"),
            f"{label}: source is not Broadcom-published",
        )
        require(bool(parsed.path and parsed.path != "/"), f"{label}: URL is not a specific page")
        query_keys = {key.casefold() for key in parse_qs(parsed.query)}
        require(
            "/search" not in parsed.path.casefold()
            and not ({"q", "query", "search"} & query_keys),
            f"{label}: search-result URLs are not research sources",
        )
        require(isinstance(consulted_on, str), f"{label}: consulted_on must be a string")
        try:
            dt.date.fromisoformat(consulted_on)
        except ValueError as exc:
            raise VerificationError(f"{label}: consulted_on must be an ISO-8601 calendar date") from exc
        require(
            isinstance(topics, list)
            and bool(topics)
            and all(isinstance(topic, str) and topic.strip() for topic in topics),
            f"{label}: topics must be a non-empty array of strings",
        )
        require(
            isinstance(finding, str)
            and len(re.findall(r"[A-Za-z0-9][A-Za-z0-9+./_-]*", finding)) >= 3,
            f"{label}: finding is not substantive",
        )

        text = " ".join([title, finding, *topics]).casefold()
        for area, terms in coverage_terms.items():
            if any(term in text for term in terms):
                coverage.add(area)
        if "automation" in text:
            products.add("automation")
        if "logs" in text or "log insight" in text:
            products.add("logs")
        if "operations" in text and "logs" not in text and "log insight" not in text:
            products.add("operations")

    require(coverage == set(coverage_terms), "research must cover migration, compatibility, sizing, and support")
    require(
        products == {"operations", "automation", "logs"},
        "research must cover Operations, Automation, and Operations for Logs",
    )


def regenerate_and_compare(
    plan: dict[str, Any], schema: dict[str, Any], spec: dict[str, Any]
) -> None:
    package_name = spec["package_name"]
    package_dir = ROOT / package_name
    verify_stdlib_package(package_dir)
    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temporary:
        build_root = Path(temporary)
        shutil.copytree(package_dir, build_root / package_name)
        for input_name in ("estate_inventory.json", "compatibility_snapshot.json", "installer_spec.json"):
            shutil.copy2(ROOT / input_name, build_root / input_name)
        generated_path = build_root / "migration_plan.json"
        second_path = build_root / "migration_plan-second.json"
        command = [
            sys.executable,
            "-m",
            package_name,
            spec["build_subcommand"],
            "--inventory",
            "estate_inventory.json",
            "--compatibility",
            "compatibility_snapshot.json",
            "--spec",
            "installer_spec.json",
            "--output",
            str(generated_path),
        ]
        result = subprocess.run(command, cwd=build_root, text=True, capture_output=True, timeout=20)
        require(result.returncode == 0, f"package CLI failed: {result.stderr.strip()}")
        generated = load_json(generated_path)
        errors = validate_schema(generated, schema, schema)
        require(not errors, "generated artifact schema errors: " + "; ".join(errors[:10]))
        require(json_equal(generated, plan), "committed artifact differs from deterministic package output")
        command[-1] = str(second_path)
        second = subprocess.run(command, cwd=build_root, text=True, capture_output=True, timeout=20)
        require(second.returncode == 0, f"second package CLI run failed: {second.stderr.strip()}")
        require(
            generated_path.read_bytes() == second_path.read_bytes(),
            "package output is not byte-for-byte deterministic",
        )


def main() -> int:
    try:
        # Validate the executable artifact before checking its separate research record.
        spec = load_json(ROOT / "installer_spec.json")
        artifact_path = ROOT / spec["artifact_file"]
        plan = load_json(artifact_path)
        schema = spec["plan_schema"]
        schema_errors = validate_schema(plan, schema, schema)
        if schema_errors:
            print("SCHEMA VALIDATION FAILED")
            for error in schema_errors[:50]:
                print(f"- {error}")
            return 1

        inventory = load_json(ROOT / "estate_inventory.json")
        snapshot = load_json(ROOT / "compatibility_snapshot.json")
        require(plan["estate_id"] == inventory["estate_id"], "wrong estate_id")
        require(
            plan["compatibility_snapshot_id"] == snapshot["snapshot_id"],
            "wrong compatibility snapshot id",
        )
        require(plan["target_bundle"] == snapshot["target_bundle"], "wrong target bundle")
        require(snapshot["target_bundle"]["version"] == inventory["target_bundle"]["version"], "fixture drift")
        verify_steps(plan, inventory, snapshot)
        verify_mappings(plan, inventory, snapshot)
        verify_topology(plan, inventory, snapshot)
        verify_consulted_sources(ROOT)
        regenerate_and_compare(plan, schema, spec)
    except VerificationError as exc:
        print(f"VERIFICATION FAILED: {exc}")
        return 1
    except (KeyError, TypeError) as exc:
        print(f"VERIFICATION FAILED: malformed protected input or artifact: {exc}")
        return 1

    print("VERIFICATION PASSED: schema, architecture, sizing, ordering, and package output are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
