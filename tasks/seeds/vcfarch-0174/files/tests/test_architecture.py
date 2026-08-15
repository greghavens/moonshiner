#!/usr/bin/env python3
"""Protected, deterministic acceptance verifier for the VCF architecture seed."""

from __future__ import annotations

import ast
import copy
import datetime as dt
import importlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "migration_plan.json"
INVENTORY_PATH = ROOT / "estate_inventory.json"
SNAPSHOT_PATH = ROOT / "compatibility_snapshot.json"
SCHEMA_PATH = ROOT / "migration_plan.schema.json"
PACKAGE = ROOT / "vcf_architecture"


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def schema_errors(value, schema, root=None, path="$", errors=None):
    """Validate the JSON-Schema features used by the protected plan schema."""
    if root is None:
        root = schema
    if errors is None:
        errors = []
    if "$ref" in schema:
        target = root
        for part in schema["$ref"].removeprefix("#/").split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        return schema_errors(value, target, root, path, errors)

    json_types = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }
    expected = schema.get("type")
    if expected is not None:
        expected_types = expected if isinstance(expected, list) else [expected]
        if not any(json_types[name](value) for name in expected_types):
            errors.append(f"{path} has the wrong JSON type")
            return errors
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path} does not equal its required constant")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path} is not an allowed value")

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path} is shorter than minLength")
        if schema.get("format") == "date":
            try:
                dt.date.fromisoformat(value)
            except ValueError:
                errors.append(f"{path} is not an ISO date")
        if schema.get("format") == "uri" and not re.match(
            r"^[A-Za-z][A-Za-z0-9+.-]*:[^\s]+$", value
        ):
            errors.append(f"{path} is not an absolute URI")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path} is below minimum")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path} has too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path} has too many items")
        if "items" in schema:
            for index, item in enumerate(value):
                schema_errors(item, schema["items"], root, f"{path}[{index}]", errors)
    if isinstance(value, dict):
        if len(value) < schema.get("minProperties", 0):
            errors.append(f"{path} has too few properties")
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{path} is missing required property {name}")
        if schema.get("additionalProperties") is False:
            for name in sorted(set(value) - set(properties)):
                errors.append(f"{path} has unexpected property {name}")
        for name, item in value.items():
            if name in properties:
                schema_errors(item, properties[name], root, f"{path}.{name}", errors)
            elif isinstance(schema.get("additionalProperties"), dict):
                schema_errors(
                    item,
                    schema["additionalProperties"],
                    root,
                    f"{path}.{name}",
                    errors,
                )
    return errors


def research_errors(plan):
    """Check only durable, artifact-visible research requirements."""
    errors = []
    research = plan.get("research")
    if not isinstance(research, dict):
        return ["research must be an object"]
    sources = research.get("sources")
    if not isinstance(sources, list):
        return ["research.sources must be an array"]
    topics = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        url = source.get("url")
        if isinstance(url, str):
            try:
                hostname = urlsplit(url).hostname
            except ValueError:
                hostname = None
            if hostname != "broadcom.com" and not (
                isinstance(hostname, str) and hostname.endswith(".broadcom.com")
            ):
                errors.append("research sources must be Broadcom-published URLs")
        consulted_for = source.get("consulted_for")
        if isinstance(consulted_for, list):
            topics.extend(item.lower() for item in consulted_for if isinstance(item, str))
    topic_text = " ".join(topics)
    required_topic_groups = {
        "transition paths": ("migration", "upgrade", "transition", "import"),
        "content compatibility": ("content", "configuration", "compatibility"),
        "sizing": ("sizing", "capacity", "resource", "system requirement"),
        "support boundaries": ("support", "lifecycle", "eogs", "end of general"),
    }
    for label, needles in required_topic_groups.items():
        if not any(needle in topic_text for needle in needles):
            errors.append(f"research does not identify a source consulted for {label}")
    return errors


def keyed(items, key, label, errors):
    result = {}
    if not isinstance(items, list):
        errors.append(f"{label} must be an array")
        return result
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get(key), str):
            errors.append(f"each {label} entry needs string {key}")
            continue
        value = item[key]
        if value in result:
            errors.append(f"duplicate {label} {key}: {value}")
        result[value] = item
    return result


def independent_errors(plan, inventory, snapshot):
    """Check the submitted artifact without importing candidate validation code."""
    errors = []
    if not isinstance(plan, dict):
        return ["migration plan must be a JSON object"]

    expected_top = {
        "schema_version",
        "plan_id",
        "estate_id",
        "compatibility_snapshot_id",
        "research",
        "placement",
        "migrations",
        "gates",
        "steps",
    }
    missing = sorted(expected_top - set(plan))
    if missing:
        errors.append("missing top-level fields: " + ", ".join(missing))
    if plan.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0")
    if plan.get("estate_id") != inventory["estate_id"]:
        errors.append("estate_id does not match estate_inventory.json")
    if plan.get("compatibility_snapshot_id") != snapshot["snapshot_id"]:
        errors.append("compatibility_snapshot_id does not match pinned snapshot")

    errors.extend(research_errors(plan))

    placement = plan.get("placement")
    target_domain = inventory["fleet"]["new_workload_domain"]
    placement_rules = snapshot["placement_rules"]
    if not isinstance(placement, dict):
        errors.append("placement must be an object")
        placement = {}
    if placement.get("domain_id") != placement_rules["required_domain_id"]:
        errors.append("target components must be placed in the new workload domain")
    if placement.get("cluster_id") != placement_rules["required_cluster_id"]:
        errors.append("target cluster does not match the new workload domain fixture")
    if placement.get("domain_id") == placement_rules["forbidden_domain_id"]:
        errors.append("management domain placement is forbidden")
    if placement.get("management_domain_changes") != []:
        errors.append("management_domain_changes must stay empty")
    if placement.get("host_count") != target_domain["host_count"]:
        errors.append("placement host_count contradicts the workload-domain inventory")
    if placement.get("failures_to_tolerate") != target_domain["failures_to_tolerate"]:
        errors.append("placement failures_to_tolerate contradicts the inventory")
    if placement.get("storage_policy") != target_domain["storage_policy"]:
        errors.append("placement storage_policy contradicts the inventory")
    ftt = placement.get("failures_to_tolerate")
    host_count = placement.get("host_count")
    if isinstance(ftt, int) and isinstance(host_count, int):
        required_hosts = (
            placement_rules["raid1_hosts_per_failure"] * ftt
            + placement_rules["raid1_witness_hosts"]
        )
        if placement.get("required_hosts_for_ftt") != required_hosts:
            errors.append("required_hosts_for_ftt does not equal 2*failures_to_tolerate+1")
        if host_count < required_hosts:
            errors.append(
                "host_count cannot satisfy stated failures_to_tolerate under vSAN RAID-1"
            )
    if placement.get("anti_affinity_scope") != placement_rules["anti_affinity_scope"]:
        errors.append("anti_affinity_scope does not match pinned placement rule")

    components = keyed(
        placement.get("components"), "component", "placement components", errors
    )
    if set(components) != set(snapshot["sizing_profiles"]):
        errors.append("placement components must cover every and only target component")
    reservation = {"vcpu": 0, "memory_gib": 0, "storage_tib": 0}
    demand_map = {
        "VCF Operations": "operations",
        "VCF Automation": "automation",
        "VCF Operations for Logs": "logs",
    }
    demand_fields = {
        "VCF Operations": {
            "objects": "objects",
            "collected_metrics": "collected_metrics",
            "availability": "availability",
        },
        "VCF Automation": {
            "active_deployments": "active_deployments",
            "concurrent_users": "concurrent_users",
            "availability": "availability",
        },
        "VCF Operations for Logs": {
            "events_per_second": "events_per_second",
            "ingestion_gib_per_day": "ingestion_gib_per_day",
            "active_tcp_connections": "active_tcp_connections",
            "availability": "availability",
        },
    }
    for name, sizing in snapshot["sizing_profiles"].items():
        component = components.get(name)
        if component is None:
            errors.append(f"missing placement sizing for {name}")
            continue
        for field in ("version", "profile"):
            if component.get(field) != sizing[field]:
                errors.append(f"{name} {field} does not match pinned sizing profile")
        if component.get("node_count") != sizing["minimum_nodes"]:
            errors.append(f"{name} node_count does not match pinned HA/cluster size")
        resources = component.get("resources_per_node")
        if not isinstance(resources, dict):
            errors.append(f"{name} resources_per_node must be an object")
            continue
        for plan_field, snapshot_field in (
            ("vcpu", "vcpu_each"),
            ("memory_gib", "memory_gib_each"),
            ("storage_tib", "storage_tib_each"),
        ):
            if resources.get(plan_field) != sizing[snapshot_field]:
                errors.append(f"{name} {plan_field} does not match pinned size")
        if component.get("anti_affinity") is not True:
            errors.append(f"{name} must use within-component anti-affinity")
        count = component.get("node_count")
        if isinstance(count, int):
            for field in reservation:
                value = resources.get(field)
                if isinstance(value, (int, float)):
                    reservation[field] += count * value
        stated_demand = component.get("demand_satisfied")
        source_demand = inventory["demand"][demand_map[name]]
        if not isinstance(stated_demand, dict):
            errors.append(f"{name} demand_satisfied must be an object")
        else:
            for output_field, inventory_field in demand_fields[name].items():
                if stated_demand.get(output_field) != source_demand[inventory_field]:
                    errors.append(f"{name} demand_satisfied.{output_field} is wrong")
        capacity = sizing["effective_capacity"]
        for metric in sorted(set(capacity).intersection(source_demand)):
            if isinstance(capacity[metric], (int, float)) and capacity[metric] < source_demand[metric]:
                errors.append(f"pinned {name} size cannot satisfy {metric}")

    stated_reservation = placement.get("target_reservation")
    if stated_reservation != reservation:
        errors.append("target_reservation is not the sum of component resources")
    available = target_domain["available_capacity"]
    expected_headroom = {
        field: available[field] - reservation[field] for field in reservation
    }
    if placement.get("capacity_headroom") != expected_headroom:
        errors.append("capacity_headroom is not available capacity minus reservation")
    if any(value < 0 for value in expected_headroom.values()):
        errors.append("target component reservation exceeds workload-domain capacity")

    gates = keyed(plan.get("gates"), "id", "gates", errors)
    for gate_id, gate in gates.items():
        if gate.get("kind") not in ("technical", "data"):
            errors.append(f"gate {gate_id} must be technical or data")
        for field in ("condition", "evidence", "on_failure"):
            if not isinstance(gate.get(field), str) or not gate[field]:
                errors.append(f"gate {gate_id} needs non-empty {field}")

    migrations = keyed(plan.get("migrations"), "product_id", "migrations", errors)
    inventory_products = {item["id"]: item for item in inventory["source_products"]}
    if set(migrations) != set(inventory_products):
        errors.append("migrations must cover every and only inventoried source product")
    for product_id, source_product in inventory_products.items():
        rule = snapshot["product_rules"][product_id]
        migration = migrations.get(product_id)
        if migration is None:
            continue
        if migration.get("source") != {
            "product": source_product["product"],
            "version": source_product["version"],
        }:
            errors.append(f"{product_id} source product/version is wrong")
        if migration.get("target") != {
            "product": rule["target_component"],
            "version": rule["target_version"],
        }:
            errors.append(f"{product_id} target component/version is wrong")
        for field in (
            "transition_method",
            "version_path",
            "direct_in_place_supported",
            "transition_parameters",
        ):
            if migration.get(field) != rule[field]:
                errors.append(f"{product_id} {field} contradicts pinned compatibility")
        boundary = migration.get("support_boundary")
        if not isinstance(boundary, dict) or boundary.get("source_eogs") != rule["source_eogs"]:
            errors.append(f"{product_id} source EOGS boundary is wrong")
        elif not isinstance(boundary.get("planning_effect"), str) or not boundary["planning_effect"]:
            errors.append(f"{product_id} support boundary needs a planning effect")
        migration_gates = migration.get("gate_ids")
        if not isinstance(migration_gates, list):
            errors.append(f"{product_id} gate_ids must be an array")
            migration_gates = []
        for required_gate in rule["required_gate_ids"]:
            if required_gate not in migration_gates:
                errors.append(f"{product_id} missing required gate {required_gate}")
        for gate_id in migration_gates:
            if isinstance(gate_id, str) and gate_id not in gates:
                errors.append(f"{product_id} references unknown gate {gate_id}")
        migration_gate_kinds = {
            gates[gate_id].get("kind")
            for gate_id in migration_gates
            if isinstance(gate_id, str)
            and gate_id in gates
            and isinstance(gates[gate_id].get("kind"), str)
        }
        if not {"technical", "data"}.issubset(migration_gate_kinds):
            errors.append(f"{product_id} must have both technical and data gates")

        dispositions = keyed(
            migration.get("content_dispositions"),
            "item_id",
            f"{product_id} content_dispositions",
            errors,
        )
        expected_items = {item["id"] for item in source_product["items"]}
        if set(dispositions) != expected_items:
            errors.append(f"{product_id} content_dispositions must partition all inventory items")
        for item_id, expected in rule["item_dispositions"].items():
            disposition = dispositions.get(item_id)
            if disposition is None:
                continue
            if disposition.get("disposition") != expected:
                errors.append(f"{item_id} disposition contradicts pinned compatibility")
            for field in ("mechanism", "reason"):
                if not isinstance(disposition.get(field), str) or not disposition[field]:
                    errors.append(f"{item_id} needs non-empty {field}")

    steps = plan.get("steps")
    step_map = keyed(steps, "id", "steps", errors)
    if isinstance(steps, list):
        orders = [step.get("order") for step in steps if isinstance(step, dict)]
        if orders != list(range(1, len(steps) + 1)):
            errors.append("step order must be contiguous and match array order")
        positions = {step_id: index for index, step_id in enumerate(step_map)}
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            step_id = step.get("id", f"index-{index}")
            gate_ids = step.get("gate_ids")
            if not isinstance(gate_ids, list) or not gate_ids:
                errors.append(f"step {step_id} must be gated")
            else:
                for gate_id in gate_ids:
                    if isinstance(gate_id, str) and gate_id not in gates:
                        errors.append(f"step {step_id} references unknown gate {gate_id}")
            dependencies = step.get("depends_on")
            if not isinstance(dependencies, list):
                errors.append(f"step {step_id} depends_on must be an array")
            else:
                for dependency in dependencies:
                    if isinstance(dependency, str) and (
                        dependency not in positions or positions[dependency] >= index
                    ):
                        errors.append(f"step {step_id} dependency {dependency} is not earlier")
            product_id = step.get("product_id")
            if product_id is not None and (
                not isinstance(product_id, str) or product_id not in inventory_products
            ):
                errors.append(f"step {step_id} references unknown product_id")
            if not isinstance(step.get("action"), str) or not step["action"]:
                errors.append(f"step {step_id} needs an action")
            if not isinstance(step.get("produces"), list) or not step["produces"]:
                errors.append(f"step {step_id} needs machine-readable outputs")
        milestone_positions = []
        for milestone in snapshot["required_milestones"]:
            if milestone not in positions:
                errors.append(f"missing required migration milestone {milestone}")
            else:
                milestone_positions.append(positions[milestone])
        if milestone_positions != sorted(milestone_positions):
            errors.append("required migration milestones are out of order")

    return errors


def check_stdlib_only():
    errors = []
    if not PACKAGE.is_dir():
        return ["missing vcf_architecture package"]
    python_files = sorted(PACKAGE.rglob("*.py"))
    if not python_files:
        return ["vcf_architecture package contains no Python files"]
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    forbidden = {"http.client", "http.server", "socket", "ssl", "urllib.request", "requests"}
    for path in python_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            errors.append(f"syntax error in {path.relative_to(ROOT)}: {exc}")
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                top = name.split(".", 1)[0]
                if name in forbidden or any(name.startswith(item + ".") for item in forbidden):
                    errors.append(f"validation package must stay offline; forbidden import {name}")
                elif top != "vcf_architecture" and top not in stdlib:
                    errors.append(f"third-party import is not allowed: {name}")
    return errors


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    required = [PLAN_PATH, INVENTORY_PATH, SNAPSHOT_PATH, SCHEMA_PATH]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
    if missing:
        print("FAIL: missing required artifact(s): " + ", ".join(missing))
        return 1

    plan = load_json(PLAN_PATH)
    inventory = load_json(INVENTORY_PATH)
    snapshot = load_json(SNAPSHOT_PATH)
    schema = load_json(SCHEMA_PATH)
    require(schema.get("title") == "VCF Aria estate migration architecture", "wrong schema")

    errors = schema_errors(plan, schema)
    errors.extend(independent_errors(plan, inventory, snapshot))
    errors.extend(check_stdlib_only())
    if errors:
        print("FAIL: submitted architecture is invalid")
        for error in errors:
            print(" - " + error)
        return 1

    sys.path.insert(0, str(ROOT))
    package = importlib.import_module("vcf_architecture")
    require(callable(getattr(package, "validate_plan", None)), "validate_plan is missing")
    require(callable(getattr(package, "validate_files", None)), "validate_files is missing")
    require(package.validate_plan(plan, inventory, snapshot) == [], "package rejects valid plan")
    require(
        package.validate_files(PLAN_PATH, INVENTORY_PATH, SNAPSHOT_PATH) == [],
        "validate_files rejects valid plan",
    )

    # Regression: a four-host RAID-1 placement cannot truthfully claim FTT=2.
    bad_ftt = copy.deepcopy(plan)
    bad_ftt["placement"]["host_count"] = 4
    verifier_ftt_errors = independent_errors(bad_ftt, inventory, snapshot)
    require(
        any("failures_to_tolerate" in error for error in verifier_ftt_errors),
        "protected verifier did not reject contradictory host count/FTT",
    )
    require(
        any(
            "failures_to_tolerate" in error
            for error in package.validate_plan(bad_ftt, inventory, snapshot)
        ),
        "package validator did not reject contradictory host count/FTT",
    )

    bad_method = copy.deepcopy(plan)
    next(
        migration
        for migration in bad_method["migrations"]
        if migration["product_id"] == "aria-ops-prod"
    )["transition_method"] = "in_place_upgrade"
    require(
        independent_errors(bad_method, inventory, snapshot),
        "verifier accepted unsupported Operations upgrade method",
    )
    require(
        package.validate_plan(bad_method, inventory, snapshot),
        "package accepted unsupported Operations upgrade method",
    )

    bad_content = copy.deepcopy(plan)
    next(
        migration
        for migration in bad_content["migrations"]
        if migration["product_id"] == "aria-logs-prod"
    )["content_dispositions"].pop()
    require(
        independent_errors(bad_content, inventory, snapshot),
        "verifier accepted an unaccounted inventory item",
    )
    require(
        package.validate_plan(bad_content, inventory, snapshot),
        "package accepted an unaccounted inventory item",
    )

    bad_gates = copy.deepcopy(plan)
    bad_gates["steps"][0]["gate_ids"] = []
    require(independent_errors(bad_gates, inventory, snapshot), "verifier accepted ungated step")
    require(
        package.validate_plan(bad_gates, inventory, snapshot),
        "package accepted ungated step",
    )

    bad_schema = copy.deepcopy(plan)
    bad_schema["research"]["sources"][0]["unexpected"] = True
    require(schema_errors(bad_schema, schema), "verifier accepted a schema-invalid plan")
    require(
        package.validate_plan(bad_schema, inventory, snapshot),
        "package validator accepted a schema-invalid plan",
    )

    bad_demand = copy.deepcopy(plan)
    next(
        component
        for component in bad_demand["placement"]["components"]
        if component["component"] == "VCF Operations"
    )["demand_satisfied"]["objects"] = 1
    require(independent_errors(bad_demand, inventory, snapshot), "verifier accepted false demand")
    require(
        package.validate_plan(bad_demand, inventory, snapshot),
        "package validator accepted false stated demand",
    )

    bad_scope = copy.deepcopy(plan)
    bad_scope["placement"]["anti_affinity_scope"] = "none"
    require(independent_errors(bad_scope, inventory, snapshot), "verifier accepted bad affinity")
    require(
        package.validate_plan(bad_scope, inventory, snapshot),
        "package validator accepted bad anti-affinity scope",
    )

    bad_components = copy.deepcopy(plan)
    bad_components["placement"]["components"].append(
        {
            "component": "Uninventoried Component",
            "version": "9.0.2",
            "profile": "none",
            "node_count": 1,
            "resources_per_node": {"vcpu": 0, "memory_gib": 0, "storage_tib": 0},
            "anti_affinity": True,
            "demand_satisfied": {"availability": "none"},
        }
    )
    require(
        independent_errors(bad_components, inventory, snapshot),
        "verifier accepted an uninventoried target component",
    )
    require(
        package.validate_plan(bad_components, inventory, snapshot),
        "package validator accepted an uninventoried target component",
    )

    bad_boundary = copy.deepcopy(plan)
    bad_boundary["migrations"][0]["support_boundary"]["planning_effect"] = ""
    require(independent_errors(bad_boundary, inventory, snapshot), "verifier accepted bad EOGS")
    require(
        package.validate_plan(bad_boundary, inventory, snapshot),
        "package validator accepted incomplete support boundary",
    )

    bad_gate_reference = copy.deepcopy(plan)
    bad_gate_reference["migrations"][0]["gate_ids"].append("missing.gate")
    require(
        independent_errors(bad_gate_reference, inventory, snapshot),
        "verifier accepted an unknown migration gate",
    )
    require(
        package.validate_plan(bad_gate_reference, inventory, snapshot),
        "package validator accepted an unknown migration gate",
    )

    bad_gate_kinds = copy.deepcopy(plan)
    first_gate_ids = set(bad_gate_kinds["migrations"][0]["gate_ids"])
    for gate in bad_gate_kinds["gates"]:
        if gate["id"] in first_gate_ids:
            gate["kind"] = "data"
    require(
        independent_errors(bad_gate_kinds, inventory, snapshot),
        "verifier accepted a product without a technical gate",
    )
    require(
        package.validate_plan(bad_gate_kinds, inventory, snapshot),
        "package validator accepted a product without both gate kinds",
    )

    bad_outputs = copy.deepcopy(plan)
    bad_outputs["steps"][0]["produces"] = []
    require(independent_errors(bad_outputs, inventory, snapshot), "verifier accepted no outputs")
    require(
        package.validate_plan(bad_outputs, inventory, snapshot),
        "package validator accepted a step without outputs",
    )

    bad_step_shape = copy.deepcopy(plan)
    bad_step_shape["steps"][0]["depends_on"] = None
    require(
        package.validate_plan(bad_step_shape, inventory, snapshot),
        "package validator did not return errors for a malformed dependency list",
    )

    with tempfile.TemporaryDirectory(prefix="vcf-architecture-test-") as temp_dir:
        invalid_path = Path(temp_dir) / "invalid-plan.json"
        invalid_path.write_text(json.dumps(bad_method), encoding="utf-8")
        require(
            package.validate_files(invalid_path, INVENTORY_PATH, SNAPSHOT_PATH),
            "validate_files accepted an invalid plan",
        )
        invalid_cli = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "vcf_architecture",
                "validate",
                str(invalid_path),
                "--inventory",
                str(INVENTORY_PATH),
                "--snapshot",
                str(SNAPSHOT_PATH),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=20,
            check=False,
        )
        require(invalid_cli.returncode != 0, "CLI accepted an invalid plan")

    completed = subprocess.run(
        [sys.executable, "-B", "-m", "vcf_architecture", "validate", "migration_plan.json"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=20,
        check=False,
    )
    require(completed.returncode == 0, "CLI rejected valid plan: " + completed.stdout)
    require("VALID" in completed.stdout, "CLI did not print a validation result")
    print("PASS: VCF migration architecture and stdlib validator satisfy the pinned contract")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, json.JSONDecodeError, OSError, ImportError) as exc:
        print(f"FAIL: {exc}")
        raise SystemExit(1)
