#!/usr/bin/env python3
"""Offline verifier for the VCF architecture and research artifacts.

The plan schema is evaluated before inventory, compatibility, package, or
semantic checks. Source reachability remains a live-research responsibility.
"""

from __future__ import annotations

import ast
from datetime import date
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent


class VerificationError(Exception):
    pass


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VerificationError(f"missing required file: {path.name}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(
            f"invalid JSON in {path.name}: line {exc.lineno}, column {exc.colno}"
        ) from exc


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
    return False


class SchemaValidator:
    """Small, deterministic validator for the JSON Schema keywords in the spec."""

    def __init__(self, root_schema: dict[str, Any]) -> None:
        self.root_schema = root_schema

    def resolve(self, reference: str) -> dict[str, Any]:
        if not reference.startswith("#/"):
            raise VerificationError(f"unsupported non-local schema reference: {reference}")
        current: Any = self.root_schema
        for raw_part in reference[2:].split("/"):
            part = raw_part.replace("~1", "/").replace("~0", "~")
            if not isinstance(current, dict) or part not in current:
                raise VerificationError(f"unresolvable schema reference: {reference}")
            current = current[part]
        if not isinstance(current, dict):
            raise VerificationError(f"schema reference is not an object: {reference}")
        return current

    def validate(self, instance: Any) -> list[str]:
        errors: list[str] = []
        self._validate(instance, self.root_schema, "$", errors)
        return errors

    def _validate(
        self,
        instance: Any,
        schema: dict[str, Any],
        path: str,
        errors: list[str],
    ) -> None:
        if "$ref" in schema:
            self._validate(instance, self.resolve(schema["$ref"]), path, errors)
            return

        if "const" in schema and instance != schema["const"]:
            errors.append(f"{path}: expected constant {schema['const']!r}")
        if "enum" in schema and instance not in schema["enum"]:
            errors.append(f"{path}: value is not in the allowed enum")

        expected_type = schema.get("type")
        if expected_type is not None:
            allowed = expected_type if isinstance(expected_type, list) else [expected_type]
            if not any(json_type_matches(instance, item) for item in allowed):
                errors.append(f"{path}: expected type {expected_type!r}")
                return

        if isinstance(instance, dict):
            required = schema.get("required", [])
            for key in required:
                if key not in instance:
                    errors.append(f"{path}: missing required property {key!r}")
            properties = schema.get("properties", {})
            if schema.get("additionalProperties") is False:
                for key in instance:
                    if key not in properties:
                        errors.append(f"{path}: unexpected property {key!r}")
            for key, subschema in properties.items():
                if key in instance:
                    self._validate(instance[key], subschema, f"{path}.{key}", errors)

        if isinstance(instance, list):
            if len(instance) < schema.get("minItems", 0):
                errors.append(f"{path}: fewer than minItems")
            if "maxItems" in schema and len(instance) > schema["maxItems"]:
                errors.append(f"{path}: more than maxItems")
            if schema.get("uniqueItems"):
                encoded = [
                    json.dumps(item, sort_keys=True, separators=(",", ":"))
                    for item in instance
                ]
                if len(encoded) != len(set(encoded)):
                    errors.append(f"{path}: array items are not unique")
            item_schema = schema.get("items")
            if isinstance(item_schema, dict):
                for index, item in enumerate(instance):
                    self._validate(item, item_schema, f"{path}[{index}]", errors)

        if isinstance(instance, str):
            if len(instance) < schema.get("minLength", 0):
                errors.append(f"{path}: shorter than minLength")
            pattern = schema.get("pattern")
            if pattern and re.search(pattern, instance) is None:
                errors.append(f"{path}: does not match required pattern")

        if isinstance(instance, (int, float)) and not isinstance(instance, bool):
            if "minimum" in schema and instance < schema["minimum"]:
                errors.append(f"{path}: below minimum")
            if "maximum" in schema and instance > schema["maximum"]:
                errors.append(f"{path}: above maximum")


def select_tier(value: int, tiers: list[dict[str, Any]], limit_key: str) -> dict[str, Any]:
    for tier in tiers:
        if value <= tier[limit_key]:
            return tier
    raise VerificationError(f"fixture demand {value} exceeds the pinned sizing snapshot")


def expected_components(
    inventory: dict[str, Any], snapshot: dict[str, Any]
) -> list[dict[str, Any]]:
    products = {product["id"]: product for product in inventory["products"]}
    destination = inventory["destination"]
    placement_rule = snapshot["placement"]
    components = []
    for rule in snapshot["component_sizing"]:
        product = products[rule["inventory_ref"]]
        value = product["sizing"][rule["metric"]]
        tier = select_tier(value, rule["tiers"], "max")
        placement = {
            "fleet": placement_rule["fleet"],
            "domain": placement_rule["domain"],
            "cluster": placement_rule["cluster"],
            "network": placement_rule["network"],
            "failure_domains": destination["failure_domains"][: tier["nodes"]],
        }
        components.append(
            {
                "id": rule["id"],
                "name": rule["name"],
                "version": rule["version"],
                "placement": placement,
                "sizing": {
                    "basis_metric": rule["metric"],
                    "basis_value": value,
                    "profile": tier["profile"],
                    "nodes": tier["nodes"],
                    "vcpu_per_node": tier["vcpu_per_node"],
                    "memory_gib_per_node": tier["memory_gib_per_node"],
                    "storage_gib_per_node": tier["storage_gib_per_node"],
                },
            }
        )
    return components


def expected_edge(inventory: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    demand = inventory["network_requirements"]
    rule = snapshot["edge_sizing"]
    tier = select_tier(demand["north_south_burst_gbps"], rule["tiers"], "max_burst_gbps")
    uplinks = []
    for pinned in rule["uplinks"]:
        uplinks.append(
            {
                "name": pinned["name"],
                "speed_gbps": rule["uplink_speed_gbps"],
                "tor": pinned["tor"],
                "traffic": pinned["traffic"],
                "vlans": pinned["vlans"],
                "bgp": pinned["bgp"],
                "teaming": pinned["teaming"],
            }
        )
    return {
        "sizing_basis": {
            "sustained_gbps": demand["north_south_sustained_gbps"],
            "burst_gbps": demand["north_south_burst_gbps"],
            "minimum_after_single_uplink_failure_gbps": demand[
                "minimum_after_single_uplink_failure_gbps"
            ],
        },
        "form_factor": tier["form_factor"],
        "nodes": rule["nodes"],
        "ha_mode": rule["ha_mode"],
        "capacity_per_node_gbps": tier["capacity_per_node_gbps"],
        "host_nics_required": rule["required_host_nics"],
        "dedicated_uplinks_per_host": rule["dedicated_uplinks_per_host"],
        "minimum_capacity_after_uplink_failure_gbps": rule["uplink_speed_gbps"],
        "uplinks": uplinks,
    }


def expected_migrations(
    inventory: dict[str, Any], snapshot: dict[str, Any]
) -> list[dict[str, Any]]:
    products = {product["id"]: product for product in inventory["products"]}
    output = []
    for rule in snapshot["migrations"]:
        product = products[rule["inventory_ref"]]
        content = []
        for item in product["content"]:
            pinned = rule["content"][item["id"]]
            content.append(
                {
                    "content_id": item["id"],
                    "kind": item["kind"],
                    "disposition": pinned["disposition"],
                    "target_action": pinned["target_action"],
                }
            )
        output.append(
            {
                "source": {
                    "inventory_ref": product["id"],
                    "product": product["product"],
                    "version": product["version"],
                },
                "target_component": rule["target_component"],
                "target_version": rule["target_version"],
                "method": rule["method"],
                "version_path": rule["version_path"],
                "content": content,
            }
        )
    return output


def expected_boundaries(
    inventory: dict[str, Any], snapshot: dict[str, Any]
) -> list[dict[str, Any]]:
    products = {product["id"]: product for product in inventory["products"]}
    output = []
    for rule in snapshot["migrations"]:
        product = products[rule["inventory_ref"]]
        output.append(
            {
                "inventory_ref": product["id"],
                "product": product["product"],
                "version": product["version"],
                "eogs": rule["eogs"],
                "status_at_assessment": rule["status"],
                "action": rule["boundary_action"],
            }
        )
    return output


def expected_steps(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    output = []
    for order, step in enumerate(snapshot["steps"], start=1):
        output.append(
            {
                "order": order,
                "id": step["id"],
                "scope": step["scope"],
                "action": step["action"],
                "from_version": step["from_version"],
                "to_version": step["to_version"],
                "gates": step["gates"],
                "rollback": "stop, keep the current source authoritative, and restore the validated pre-step backup or snapshot before retrying",
            }
        )
    return output


def expected_gates(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    output = []
    for step in snapshot["steps"]:
        for gate_id in step["gates"]:
            check, expected = snapshot["gates"][gate_id]
            output.append(
                {
                    "id": gate_id,
                    "before_step": step["id"],
                    "check": check,
                    "expected": expected,
                    "failure_action": "stop before the step, remediate the failed check, and rerun the gate",
                }
            )
    return output


def check_equal(errors: list[str], label: str, actual: Any, expected: Any) -> None:
    if actual != expected:
        errors.append(f"{label} does not match the fixture and pinned snapshot")


def semantic_checks(
    artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    check_equal(errors, "inputs.inventory_id", artifact["inputs"]["inventory_id"], inventory["inventory_id"])
    check_equal(
        errors,
        "inputs.compatibility_snapshot_id",
        artifact["inputs"]["compatibility_snapshot_id"],
        snapshot["snapshot_id"],
    )
    check_equal(errors, "inputs.assessment_date", artifact["inputs"]["assessment_date"], inventory["assessment_date"])
    check_equal(errors, "architecture release", artifact["architecture"]["vcf_release"], snapshot["target_release"])
    check_equal(
        errors,
        "placement principle",
        artifact["architecture"]["placement_principle"],
        snapshot["placement"]["principle"],
    )

    components = expected_components(inventory, snapshot)
    check_equal(errors, "target component placement and sizing", artifact["architecture"]["components"], components)
    edge = expected_edge(inventory, snapshot)
    check_equal(errors, "Edge throughput design", artifact["architecture"]["edge"], edge)
    check_equal(errors, "migration mappings", artifact["migrations"], expected_migrations(inventory, snapshot))
    check_equal(errors, "ordered migration steps", artifact["steps"], expected_steps(snapshot))
    check_equal(errors, "gate catalog", artifact["gate_catalog"], expected_gates(snapshot))
    check_equal(errors, "support boundaries", artifact["support_boundaries"], expected_boundaries(inventory, snapshot))

    expected_plan_id = f"{inventory['inventory_id']}-to-vcf-{snapshot['target_release']}"
    check_equal(errors, "plan_id", artifact["plan_id"], expected_plan_id)

    totals = {"vcpu": 0, "memory_gib": 0, "storage_gib": 0}
    for component in artifact["architecture"]["components"]:
        sizing = component["sizing"]
        totals["vcpu"] += sizing["nodes"] * sizing["vcpu_per_node"]
        totals["memory_gib"] += sizing["nodes"] * sizing["memory_gib_per_node"]
        totals["storage_gib"] += sizing["nodes"] * sizing["storage_gib_per_node"]
    capacity = inventory["destination"]["available_capacity"]
    for resource, total in totals.items():
        if total > capacity[resource]:
            errors.append(f"target component {resource} demand exceeds fixture capacity")

    demand = inventory["network_requirements"]
    edge_actual = artifact["architecture"]["edge"]
    if edge_actual["capacity_per_node_gbps"] < demand["north_south_burst_gbps"]:
        errors.append("a surviving Edge node cannot carry burst demand")
    if (
        edge_actual["minimum_capacity_after_uplink_failure_gbps"]
        < demand["minimum_after_single_uplink_failure_gbps"]
    ):
        errors.append("uplink layout misses the single-uplink failure requirement")
    host_nics = demand["edge_host_nics"]
    if host_nics["count"] < edge_actual["host_nics_required"]:
        errors.append("Edge design requires more host NICs than the fixture provides")
    for uplink in edge_actual["uplinks"]:
        if uplink["speed_gbps"] > host_nics["speed_gbps"]:
            errors.append("Edge uplink speed exceeds fixture NIC speed")
    return errors


def stdlib_checks(package_dir: Path) -> list[str]:
    errors: list[str] = []
    required = [package_dir / "__init__.py", package_dir / "__main__.py"]
    for path in required:
        if not path.is_file():
            errors.append(f"missing package file {path.relative_to(ROOT)}")
    python_files = sorted(package_dir.rglob("*.py")) if package_dir.is_dir() else []
    if not python_files:
        errors.append("vcf_architecture is not an implemented Python package")
        return errors
    local_modules = {path.stem for path in python_files} | {package_dir.name}
    local_modules.update(
        path.relative_to(package_dir).parts[0]
        for path in python_files
        if len(path.relative_to(package_dir).parts) > 1
    )
    allowed = set(sys.stdlib_module_names) | local_modules
    network_modules = {
        "ftplib",
        "http",
        "imaplib",
        "poplib",
        "smtplib",
        "socket",
        "telnetlib",
        "urllib",
    }
    for path in python_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            errors.append(f"cannot parse {path.name}: {exc}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = [node.module.split(".", 1)[0]]
            else:
                roots = []
            for root in roots:
                if root not in allowed:
                    errors.append(f"non-stdlib import {root!r} in {path.name}")
                if root in network_modules:
                    errors.append(f"network-capable import {root!r} in {path.name}")
    return errors


def _valid_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def research_checks(path: Path) -> list[str]:
    """Validate the deterministic research record without fetching the network."""

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ["missing required research record: research.md"]
    except OSError as exc:
        return [f"cannot read research.md: {exc}"]

    errors: list[str] = []
    lowered = text.lower()
    dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text)
    if not dates:
        errors.append("research.md must record an access date")
    elif not any(_valid_date(item) for item in dates):
        errors.append("research.md access date is not a valid calendar date")

    urls = [
        raw.rstrip(".,;:'\"]}")
        for raw in re.findall(r"https://[^\s<>()]+", text)
    ]
    distinct_urls = list(dict.fromkeys(urls))
    if not distinct_urls:
        errors.append("research.md must identify the consulted Broadcom pages")
    for url in distinct_urls:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (
            hostname == "broadcom.com" or hostname.endswith(".broadcom.com")
        ):
            errors.append(f"research.md contains a non-Broadcom source URL: {url}")

    coverage = {
        "VCF Operations migration": (
            "vcf operations",
            "aria operations",
            "vrealize operations",
        ),
        "VCF Automation migration": (
            "vcf automation",
            "aria automation",
            "vrealize automation",
        ),
        "VCF Operations for Logs migration": (
            "operations for logs",
            "operations – logs",
            "operations - logs",
            "log data transfer",
            "log insight",
        ),
        "content compatibility": (
            "content",
            "management pack",
            "integration",
            "identity",
            "workflow",
            "dashboard",
            "policy",
            "template",
        ),
        "support boundaries": (
            "eogs",
            "end of general support",
            "support boundary",
            "general support",
            "lifecycle",
        ),
        "NSX Edge and uplinks": (
            "nsx",
            "edge",
            "uplink",
            "bgp",
            "virtual networking",
        ),
    }
    for topic, terms in coverage.items():
        if not any(term in lowered for term in terms):
            errors.append(f"research.md does not cover {topic}")
    return errors


def run_entrypoint(
    spec: dict[str, Any], working_dir: Path, generated_path: Path
) -> tuple[Any | None, list[str]]:
    entrypoint = spec["package"]["entrypoint"]
    command = [part.replace("{output}", str(generated_path)) for part in entrypoint]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        result = subprocess.run(
            command,
            cwd=working_dir,
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, [f"package entrypoint could not run: {exc}"]
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        return None, [f"package entrypoint failed: {detail[:400]}"]
    try:
        return read_json(generated_path), []
    except VerificationError as exc:
        return None, [f"package did not generate valid output: {exc}"]


def reproducibility_check(
    spec: dict[str, Any],
    artifact: dict[str, Any],
    inventory: dict[str, Any],
    snapshot: dict[str, Any],
) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temp_dir:
        temp_root = Path(temp_dir)
        generated_path = temp_root / "migration_plan.json"
        generated, errors = run_entrypoint(spec, ROOT, generated_path)
        if errors:
            return errors
        if generated != artifact:
            return ["checked-in artifact is not reproducible from the package and fixtures"]

        mutated_inventory = json.loads(json.dumps(inventory))
        mutated_snapshot = json.loads(json.dumps(snapshot))
        mutated_inventory["inventory_id"] = "mutation-estate"
        mutated_inventory["assessment_date"] = "2030-01-02"
        mutated_inventory["destination"]["failure_domains"] = [
            "mutation-fd-01",
            "mutation-fd-02",
            "mutation-fd-03",
            "mutation-fd-04",
        ]
        mutated_inventory["products"][0]["sizing"]["monitored_objects"] = 4000
        mutated_inventory["products"][0]["content"][0]["kind"] = (
            "mutated_custom_dashboard"
        )
        mutated_inventory["products"][1]["sizing"]["active_deployments"] = 400
        mutated_inventory["products"][2]["sizing"]["events_per_second"] = 10000
        mutated_inventory["network_requirements"]["north_south_sustained_gbps"] = 14
        mutated_inventory["network_requirements"]["north_south_burst_gbps"] = 18
        mutated_inventory["network_requirements"][
            "minimum_after_single_uplink_failure_gbps"
        ] = 10

        mutated_snapshot["snapshot_id"] = "mutation-snapshot"
        mutated_snapshot["placement"].update(
            {
                "fleet": "mutation-fleet",
                "domain": "mutation-domain",
                "cluster": "mutation-cluster",
                "network": "mutation-network",
                "principle": "mutation placement principle",
            }
        )
        mutated_snapshot["migrations"][1]["content"]["auto-projects"][
            "target_action"
        ] = "mutation target action"
        mutated_snapshot["steps"][0]["action"] = "mutation lifecycle action"
        mutated_snapshot["gates"]["lcm-health-green"][1] = "mutation-green"

        (temp_root / "estate_inventory.json").write_text(
            json.dumps(mutated_inventory), encoding="utf-8"
        )
        (temp_root / "compatibility_snapshot.json").write_text(
            json.dumps(mutated_snapshot), encoding="utf-8"
        )
        mutated_output = temp_root / "mutated_plan.json"
        mutated, errors = run_entrypoint(spec, temp_root, mutated_output)
        if errors:
            return [f"mutated-fixture check: {error}" for error in errors]
        schema_errors = SchemaValidator(spec["artifact"]["schema"]).validate(mutated)
        if schema_errors:
            return [
                "package output for mutated fixtures failed schema validation: "
                + schema_errors[0]
            ]
        mutation_errors = semantic_checks(
            mutated, mutated_inventory, mutated_snapshot
        )
        if mutation_errors:
            return [
                "package does not derive its output from the supplied inputs: "
                + mutation_errors[0]
            ]
    return []


def main() -> int:
    try:
        spec = read_json(ROOT / "installer_spec.json")
        artifact_path = ROOT / spec["artifact"]["path"]
        artifact = read_json(artifact_path)
        schema_errors = SchemaValidator(spec["artifact"]["schema"]).validate(artifact)
        if schema_errors:
            print("FAIL: artifact schema validation failed before semantic checks")
            for error in schema_errors:
                print(f" - {error}")
            return 1

        inventory = read_json(ROOT / "estate_inventory.json")
        snapshot = read_json(ROOT / "compatibility_snapshot.json")
        errors = semantic_checks(artifact, inventory, snapshot)
        errors.extend(stdlib_checks(ROOT / spec["package"]["name"]))
        errors.extend(research_checks(ROOT / "research.md"))
        if not errors:
            errors.extend(reproducibility_check(spec, artifact, inventory, snapshot))
        if errors:
            print("FAIL: verification errors")
            for error in errors:
                print(f" - {error}")
            return 1
        print(
            "PASS: schema, research, architecture, migration, capacity, "
            "and package checks succeeded"
        )
        return 0
    except (KeyError, TypeError, VerificationError) as exc:
        print(f"FAIL: verifier input error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
