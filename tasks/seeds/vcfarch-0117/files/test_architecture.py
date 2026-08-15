#!/usr/bin/env python3
"""Protected, deterministic acceptance verifier for the VCF architecture."""

from __future__ import annotations

import ast
import importlib
import json
import re
import subprocess
import sys
import tempfile
from collections import defaultdict, deque
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent
def load_json(path: Path) -> Any:
    assert path.is_file(), f"required file is missing: {path.name}"
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


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
    return True


def resolve_pointer(document: Any, pointer: str) -> Any:
    if not pointer.startswith("#/"):
        raise AssertionError(f"external schema reference is not supported: {pointer}")
    node = document
    for raw_part in pointer[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        node = node[part]
    return node


def validate_schema(
    value: Any,
    schema: dict[str, Any],
    document: dict[str, Any],
    path: str = "$",
) -> list[str]:
    """Validate the JSON-Schema/OpenAPI subset used by the protected contracts."""
    errors: list[str] = []
    if "$ref" in schema:
        return validate_schema(value, resolve_pointer(document, schema["$ref"]), document, path)
    if value is None and schema.get("nullable"):
        return errors
    if "allOf" in schema:
        for branch in schema["allOf"]:
            errors.extend(validate_schema(value, branch, document, path))
    if "anyOf" in schema and not any(
        not validate_schema(value, branch, document, path) for branch in schema["anyOf"]
    ):
        errors.append(f"{path}: does not match anyOf")
    if "oneOf" in schema:
        matches = sum(
            not validate_schema(value, branch, document, path) for branch in schema["oneOf"]
        )
        if matches != 1:
            errors.append(f"{path}: matches {matches} oneOf branches")
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: not in enum")

    expected = schema.get("type")
    if expected:
        allowed = expected if isinstance(expected, list) else [expected]
        if not any(json_type_matches(value, item) for item in allowed):
            errors.append(f"{path}: expected type {expected}, got {type(value).__name__}")
            return errors

    if isinstance(value, dict):
        required = schema.get("required", [])
        for name in required:
            if name not in value:
                errors.append(f"{path}: missing required property {name!r}")
        properties = schema.get("properties", {})
        for name, child_schema in properties.items():
            if name in value:
                errors.extend(
                    validate_schema(value[name], child_schema, document, f"{path}.{name}")
                )
        additional = schema.get("additionalProperties", True)
        if additional is False:
            for name in value.keys() - properties.keys():
                errors.append(f"{path}: unexpected property {name!r}")
        elif isinstance(additional, dict):
            for name in value.keys() - properties.keys():
                errors.extend(
                    validate_schema(value[name], additional, document, f"{path}.{name}")
                )

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path}: fewer than minItems")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: more than maxItems")
        if schema.get("uniqueItems"):
            canonical = [json.dumps(item, sort_keys=True) for item in value]
            if len(canonical) != len(set(canonical)):
                errors.append(f"{path}: array items are not unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(
                    validate_schema(item, item_schema, document, f"{path}[{index}]")
                )

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path}: shorter than minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            errors.append(f"{path}: does not match pattern {schema['pattern']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above maximum")
    return errors


def find_path(
    start: tuple[str, str],
    target: tuple[str, str],
    edges: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    adjacency: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        adjacency[(edge["from_product"], edge["from_version"])].append(edge)
    queue: deque[tuple[tuple[str, str], list[dict[str, Any]]]] = deque([(start, [])])
    seen = {start}
    while queue:
        state, path = queue.popleft()
        if state == target:
            return path
        for edge in adjacency[state]:
            next_state = (edge["to_product"], edge["to_version"])
            if next_state not in seen:
                seen.add(next_state)
                queue.append((next_state, [*path, edge]))
    return None


def check_installer_schema_first() -> tuple[dict[str, Any], dict[str, Any]]:
    artifact = load_json(ROOT / "architecture.json")
    openapi = load_json(
        ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
    )
    sddc_schema = openapi["components"]["schemas"]["SddcSpec"]
    errors = validate_schema(artifact, sddc_schema, openapi)
    assert not errors, "architecture failed the installer SddcSpec: " + "; ".join(errors)
    return artifact, openapi


def check_plan_contract(artifact: dict[str, Any]) -> None:
    schema = load_json(ROOT / "contracts" / "migration-plan.schema.json")
    errors = validate_schema(artifact, schema, schema)
    assert not errors, "architecture failed migration-plan schema: " + "; ".join(errors)


def check_topology(
    artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    topology = artifact["selected_topology"]
    entitlement = inventory["entitlement"]
    target = inventory["target"]
    assert topology["topology_id"] == entitlement["permitted_topology"]
    assert topology["fleet_id"] == target["fleet_id"]
    assert topology["vcf_instance_id"] == target["vcf_instance_id"]
    assert topology["management_domain"] == target["management_domain"]
    assert topology["workload_domains"] == target["workload_domains"]
    topology_authority = {item["topology_id"]: item for item in snapshot["topologies"]}
    selected = topology_authority[topology["topology_id"]]
    assert selected["compatible"] is True
    assert selected["entitlement_class"] == entitlement["class"]
    rejected = {item["topology_id"]: item for item in topology["rejected_topologies"]}
    excluded = entitlement["excluded_topology"]
    assert topology_authority[excluded]["compatible"] is True
    assert rejected[excluded]["blocked_by"] == entitlement["id"]


def check_sddc_semantics(
    artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    spec = artifact
    target = inventory["target"]
    components = inventory["components"]
    management_vcenter = next(
        item for item in components if item["site"] == "prod" and item["product"] == "vcenter"
    )
    management_nsx = next(
        item for item in components if item["site"] == "prod" and item["product"] == "nsx"
    )
    management_vsan = next(
        item for item in components if item["site"] == "prod" and item["product"] == "vsan"
    )
    management_site = next(site for site in inventory["sites"] if site["site_id"] == "prod")
    assert spec["sddcId"] == target["management_domain"]
    assert spec["workflowType"] == "VCF"
    assert spec["version"] == snapshot["target_release"] == target["release"]
    assert spec["vcfInstanceName"] == target["vcf_instance_id"]
    assert spec["dnsSpec"]["subdomain"] == target["dns_subdomain"]
    assert spec["dnsSpec"]["nameservers"] == target["nameservers"]
    assert spec["ntpServers"] == target["ntp_servers"]
    assert spec["clusterSpec"]["clusterName"] == target["management_domain"]
    assert {item["hostname"] for item in spec["hostSpecs"]} == set(management_site["hosts"])
    assert len(spec["hostSpecs"]) == len(management_site["hosts"])
    assert spec["datastoreSpec"]["existingDatastoreName"] == management_vsan["datastore"]
    management_networks = [
        item for item in spec["networkSpecs"] if item["networkType"] == "MANAGEMENT"
    ]
    assert len(management_networks) == 1
    assert management_networks[0]["vlanId"] == management_site["management_vlan"]
    assert spec["vcenterSpec"]["vcenterHostname"] == management_vcenter["fqdn"]
    assert spec["vcenterSpec"]["useExistingDeployment"] is True
    assert spec["vcenterSpec"]["version"] == snapshot["targets"]["vcenter"]["version"]
    assert spec["nsxtSpec"]["useExistingDeployment"] is True
    assert spec["nsxtSpec"]["version"] == snapshot["targets"]["nsx"]["version"]
    manager_names = {item["hostname"] for item in spec["nsxtSpec"]["nsxtManagers"]}
    assert management_nsx["fqdn"] in manager_names
    assert spec["sddcManagerSpec"]["version"] == snapshot["target_release"]
    assert spec["vcfOperationsSpec"]["version"] == snapshot["target_release"]
    assert spec["licenseServerSpec"]["version"] == snapshot["target_release"]


def check_paths_and_gates(
    artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    components = {item["component_id"]: item for item in inventory["components"]}
    states = {
        component_id: (item["product"], item["version"])
        for component_id, item in components.items()
    }
    initial_products = {
        component_id: item["product"] for component_id, item in components.items()
    }
    edge_index = {
        (
            edge["from_product"],
            edge["from_version"],
            edge["to_product"],
            edge["to_version"],
        ): edge
        for edge in snapshot["upgrade_edges"]
    }
    known_gates = set(snapshot["gates"])
    seen_components: set[str] = set()
    gate_orders: dict[tuple[str, str], list[int]] = defaultdict(list)
    steps = artifact["steps"]
    assert [step["order"] for step in steps] == list(range(1, len(steps) + 1))

    for step in steps:
        component_id = step["component_id"]
        assert component_id in components, f"unknown component {component_id}"
        component = components[component_id]
        assert step["site"] == component["site"]
        assert (step["source_product"], step["source_version"]) == states[component_id]
        key = (
            step["source_product"],
            step["source_version"],
            step["target_product"],
            step["target_version"],
        )
        assert key in edge_index, f"unsupported transition for {component_id}: {key}"
        edge = edge_index[key]
        assert step["action"] == edge["action"]
        assert set(edge["required_gates"]).issubset(step["gates"])
        assert set(step["gates"]).issubset(known_gates)
        for gate in step["gates"]:
            gate_orders[(gate, step["site"])].append(step["order"])
        states[component_id] = (step["target_product"], step["target_version"])
        seen_components.add(component_id)

    assert seen_components == set(components), "every inventory component must have a plan step"
    for component_id, state in states.items():
        expected = snapshot["targets"][initial_products[component_id]]
        assert state == (expected["product"], expected["version"]), (
            f"{component_id} ends at {state}, expected {expected}"
        )

    sites = [site["site_id"] for site in inventory["sites"]]
    for rule in snapshot["ordering_rules"]:
        scopes = sites if rule["scope"] == "site" else ["*"]
        for scope in scopes:
            if scope == "*":
                before = [
                    order
                    for (gate, _site), orders in gate_orders.items()
                    if gate == rule["before_gate"]
                    for order in orders
                ]
                after = [
                    order
                    for (gate, _site), orders in gate_orders.items()
                    if gate == rule["after_gate"]
                    for order in orders
                ]
            else:
                before = gate_orders[(rule["before_gate"], scope)]
                after = gate_orders[(rule["after_gate"], scope)]
            if before and after:
                assert max(before) < min(after), f"ordering rule violated: {rule} at {scope}"

    for component_id, component in components.items():
        target_data = snapshot["targets"][component["product"]]
        assert find_path(
            (component["product"], component["version"]),
            (target_data["product"], target_data["version"]),
            snapshot["upgrade_edges"],
        ) is not None


def check_stdlib_only() -> None:
    local_roots = {"vcf_architect"}
    stdlib = set(sys.stdlib_module_names)
    for source_path in sorted((ROOT / "vcf_architect").rglob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                names = [(node.module or "").split(".", 1)[0]]
            else:
                continue
            for name in names:
                assert name in stdlib or name in local_roots, (
                    f"non-stdlib import {name!r} in {source_path.name}"
                )


def check_research_log(artifact: dict[str, Any]) -> None:
    records = artifact.get("research_log")
    assert isinstance(records, list) and records, "research_log must be a non-empty array"
    searchable: list[str] = []
    for index, record in enumerate(records):
        assert isinstance(record, dict), f"research_log[{index}] must be an object"
        for field in ("url", "title", "accessed", "decision"):
            assert isinstance(record.get(field), str) and record[field].strip(), (
                f"research_log[{index}].{field} must be a non-empty string"
            )
        parsed = urlsplit(record["url"])
        host = (parsed.hostname or "").lower()
        is_broadcom = host == "broadcom.com" or host.endswith(".broadcom.com")
        is_tagged_spec = (
            host in {"github.com", "api.github.com", "raw.githubusercontent.com"}
            and "vmware/vcf-api-specs" in record["url"].lower()
            and "9.1.0.0" in record["url"]
            and "specifications/vcf-installer/vcf-installer-openapi.json"
            in record["url"]
        )
        assert parsed.scheme == "https" and (is_broadcom or is_tagged_spec), (
            f"research_log[{index}].url is not an allowed live source"
        )
        date.fromisoformat(record["accessed"])
        searchable.append(
            " ".join((record["title"], record["url"], record["decision"])).lower()
        )

    requirements = {
        "product interoperability": lambda text: (
            "interoperability" in text or "interopmatrix" in text
        ),
        "upgrade path": lambda text: "upgrade path" in text or "/upgrade" in text,
        "VCF 9.1 brownfield convergence": lambda text: (
            "9.1" in text
            and any(marker in text for marker in ("convert/import", "brownfield", "converge"))
        ),
        "cross-product upgrade sequence": lambda text: (
            "upgrade sequence" in text or "update sequence" in text
        ),
        "unified recovery transition": lambda text: (
            "recovery" in text
            and any(marker in text for marker in ("unified", "protection and recovery", "converg"))
        ),
        "tagged SddcSpec": lambda text: (
            "9.1.0.0" in text and ("sddcspec" in text or "vcf-api-specs" in text)
        ),
    }
    for category, matches in requirements.items():
        assert any(matches(text) for text in searchable), (
            f"research_log does not identify {category} material"
        )


def check_package(artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    module = importlib.import_module("vcf_architect")
    built = module.build_architecture(inventory, snapshot)
    assert built == artifact
    with tempfile.TemporaryDirectory(prefix="vcfarch-") as temp_dir:
        output_one = Path(temp_dir) / "one.json"
        output_two = Path(temp_dir) / "two.json"
        base_command = [
            sys.executable,
            "-m",
            "vcf_architect",
            "--inventory",
            str(ROOT / "fixtures" / "estate.json"),
            "--compatibility",
            str(ROOT / "fixtures" / "compatibility-snapshot.json"),
        ]
        for output_path in (output_one, output_two):
            completed = subprocess.run(
                [*base_command, "--output", str(output_path)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert completed.returncode == 0, completed.stderr or completed.stdout
        generated_one = load_json(output_one)
        generated_two = load_json(output_two)
        assert generated_one == generated_two == artifact
        assert output_one.read_bytes() == output_two.read_bytes()
        assert output_one.read_bytes() == (ROOT / "architecture.json").read_bytes()


def main() -> int:
    # This installer-schema validation is intentionally the first artifact check.
    artifact, openapi = check_installer_schema_first()
    assert openapi["info"]["version"] == "9.1.0.0"
    check_plan_contract(artifact)
    inventory = load_json(ROOT / "fixtures" / "estate.json")
    snapshot = load_json(ROOT / "fixtures" / "compatibility-snapshot.json")
    assert artifact["estate_id"] == inventory["estate_id"]
    check_topology(artifact, inventory, snapshot)
    check_sddc_semantics(artifact, inventory, snapshot)
    check_paths_and_gates(artifact, inventory, snapshot)
    check_research_log(artifact)
    check_stdlib_only()
    check_package(artifact, inventory, snapshot)
    print("PASS: VCF architecture satisfies the installer, estate, and pinned compatibility contracts")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
