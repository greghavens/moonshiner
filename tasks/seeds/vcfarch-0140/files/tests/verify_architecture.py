#!/usr/bin/env python3
"""Deterministic acceptance verifier for vcfarch-0140.

This verifier is deliberately offline. Live network calls are a trace requirement;
the resulting research record is checked here without making verification depend
on mutable network availability.
"""

from __future__ import annotations

import ast
from copy import deepcopy
from datetime import date
import hashlib
import importlib
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "architecture.json"
OPENAPI = ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
OUTPUT_SCHEMA = ROOT / "schemas" / "architecture-output.schema.json"
INVENTORY = ROOT / "estate_inventory.json"
SNAPSHOT = ROOT / "compatibility_snapshot.json"
RESEARCH = ROOT / "research.md"


class VerificationError(Exception):
    pass


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise VerificationError(f"missing required file: {path.relative_to(ROOT)}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(
            f"invalid JSON in {path.relative_to(ROOT)}: line {exc.lineno}, column {exc.colno}"
        ) from exc


def resolve_ref(root_schema: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise VerificationError(f"unsupported non-local schema reference: {ref}")
    node: Any = root_schema
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        try:
            node = node[part]
        except (KeyError, TypeError) as exc:
            raise VerificationError(f"unresolvable schema reference: {ref}") from exc
    if not isinstance(node, dict):
        raise VerificationError(f"schema reference does not resolve to an object: {ref}")
    return node


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
    raise VerificationError(f"unsupported JSON Schema type: {expected}")


def validate_schema(
    value: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    path: str,
) -> None:
    if "$ref" in schema:
        validate_schema(value, resolve_ref(root_schema, schema["$ref"]), root_schema, path)
        return

    if "const" in schema and value != schema["const"]:
        raise VerificationError(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise VerificationError(f"{path}: value {value!r} is not in the schema enum")

    expected_type = schema.get("type")
    if expected_type is not None:
        accepted = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(json_type_matches(value, item) for item in accepted):
            raise VerificationError(f"{path}: expected JSON type {expected_type!r}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                raise VerificationError(f"{path}: missing required property {key!r}")
        if "minProperties" in schema and len(value) < schema["minProperties"]:
            raise VerificationError(f"{path}: has fewer than minProperties")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            raise VerificationError(f"{path}: has more than maxProperties")

        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, item in value.items():
            child_path = f"{path}.{key}"
            if key in properties:
                validate_schema(item, properties[key], root_schema, child_path)
            elif additional is False:
                raise VerificationError(f"{path}: unexpected property {key!r}")
            elif isinstance(additional, dict):
                validate_schema(item, additional, root_schema, child_path)

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise VerificationError(f"{path}: has fewer than minItems")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise VerificationError(f"{path}: has more than maxItems")
        if schema.get("uniqueItems"):
            encoded = [
                json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                for item in value
            ]
            if len(encoded) != len(set(encoded)):
                raise VerificationError(f"{path}: array items are not unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                validate_schema(item, item_schema, root_schema, f"{path}[{index}]")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise VerificationError(f"{path}: string is shorter than minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise VerificationError(f"{path}: string is longer than maxLength")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], value)
            except re.error as exc:
                raise VerificationError(f"{path}: invalid pattern in pinned schema") from exc
            if matched is None:
                raise VerificationError(f"{path}: string does not match schema pattern")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise VerificationError(f"{path}: number is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise VerificationError(f"{path}: number is above maximum")


def canonical(value: Any) -> str:
    if isinstance(value, set):
        value = sorted(value, key=repr)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def assert_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise VerificationError(
            f"{label} mismatch\nexpected: {canonical(expected)}\nactual:   {canonical(actual)}"
        )


def validate_sddc_first(artifact: Any, openapi: dict[str, Any]) -> None:
    """The first substantive verification is the upstream SddcSpec schema."""
    if not isinstance(artifact, dict):
        raise VerificationError("artifact root must be a JSON object")
    candidate = artifact.get("greenfield_sddc_spec")
    sddc_schema = openapi.get("components", {}).get("schemas", {}).get("SddcSpec")
    if not isinstance(sddc_schema, dict):
        raise VerificationError("pinned OpenAPI file does not contain components.schemas.SddcSpec")
    validate_schema(candidate, sddc_schema, openapi, "$.greenfield_sddc_spec")


def check_sddc_design(
    sddc: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    assert_equal(sddc.get("version"), snapshot["target_release"], "SddcSpec version")
    assert_equal(sddc.get("workflowType"), "VCF", "SddcSpec workflowType")
    assert_equal(
        sddc.get("clusterSpec", {}).get("clusterName"),
        inventory["management_domain"]["name"],
        "SddcSpec cluster name",
    )

    expected_hosts = sorted(
        item["hostname"] for item in inventory["management_domain"]["management_hosts"]
    )
    actual_hosts = sorted(item.get("hostname") for item in sddc.get("hostSpecs", []))
    assert_equal(actual_hosts, expected_hosts, "SddcSpec management hosts")

    expected_networks = {
        (
            item["network_type"],
            item["subnet"],
            item["gateway"],
            item["vlan_id"],
        )
        for item in inventory["networks"]
        if item["network_type"] != "VSAN_WITNESS"
    }
    actual_networks = {
        (
            item.get("networkType"),
            item.get("subnet"),
            item.get("gateway"),
            item.get("vlanId"),
        )
        for item in sddc.get("networkSpecs", [])
    }
    assert_equal(actual_networks, expected_networks, "SddcSpec site networks")


def check_topology(topology: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    expected_sites = {item["id"]: item for item in inventory["sites"]}
    actual_sites = {item["id"]: item for item in topology["sites"]}
    assert_equal(actual_sites, expected_sites, "topology sites")

    expected_networks = {canonical(item) for item in inventory["networks"]}
    actual_networks = {canonical(item) for item in topology["network_placement"]}
    assert_equal(actual_networks, expected_networks, "topology network placement")

    domain = topology["management_domain"]
    inv_domain = inventory["management_domain"]
    assert_equal(domain["name"], inv_domain["name"], "management-domain name")
    assert_equal(domain["stretched"], True, "stretched management-domain flag")
    assert_equal(
        domain["availability_zone_site_ids"],
        snapshot["topology"]["availability_zone_site_ids"],
        "availability-zone placement",
    )

    expected_placement: dict[str, list[str]] = {
        site_id: [] for site_id in inv_domain["availability_zone_site_ids"]
    }
    for host in inv_domain["management_hosts"]:
        expected_placement[host["site_id"]].append(host["hostname"])
    for hostnames in expected_placement.values():
        hostnames.sort()
    actual_placement = {
        site_id: sorted(hostnames) for site_id, hostnames in domain["host_placement"].items()
    }
    assert_equal(actual_placement, expected_placement, "management host placement")
    required_count = snapshot["topology"]["required_host_count_per_availability_zone"]
    if any(len(hosts) != required_count for hosts in actual_placement.values()):
        raise VerificationError("each availability zone must contain the pinned host count")

    witness = domain["witness"]
    expected_witness = {
        "component_id": "vsan-witness",
        "site_id": snapshot["topology"]["witness_site_id"],
        "failure_domain": snapshot["topology"]["witness_failure_domain"],
        "runs_on_management_domain": snapshot["topology"][
            "witness_runs_on_management_domain"
        ],
    }
    assert_equal(witness, expected_witness, "witness placement")
    if witness["site_id"] in domain["availability_zone_site_ids"]:
        raise VerificationError("witness must be outside both data availability zones")
    data_failure_domains = {
        expected_sites[site_id]["failure_domain"]
        for site_id in domain["availability_zone_site_ids"]
    }
    if witness["failure_domain"] in data_failure_domains:
        raise VerificationError("witness must use an independent third failure domain")


def check_migration_plan(
    plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    assert_equal(plan["estate_id"], inventory["estate_id"], "migration estate")
    assert_equal(plan["from_release"], inventory["current_release"], "source release")
    assert_equal(plan["target_release"], inventory["target_release"], "target release")
    assert_equal(plan["release_route"], snapshot["required_release_route"], "release route")

    expected_avoided = [
        {"release": item["release"], "gate": item["gate_id"]}
        for item in snapshot["forbidden_release_hops"]
    ]
    assert_equal(plan["avoided_release_hops"], expected_avoided, "avoided release hops")
    assert_equal(plan["steps"], snapshot["component_plan"], "ordered component plan")

    inventory_components = {
        item["id"]: (
            item["component_type"],
            item["current_version"],
            item["target_version"],
        )
        for item in inventory["components"]
    }
    plan_components = {
        item["component_id"]: (
            item["component_type"],
            item["from_version"],
            item["target_version"],
        )
        for item in plan["steps"]
    }
    assert_equal(plan_components, inventory_components, "component inventory coverage")
    assert_equal(
        [item["sequence"] for item in plan["steps"]],
        list(range(1, len(plan["steps"]) + 1)),
        "migration step sequence",
    )


def check_research_record() -> None:
    try:
        text = RESEARCH.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise VerificationError("missing required file: research.md") from exc

    source_lines = [line.strip() for line in text.splitlines() if line.lstrip().startswith("- ")]
    if len(source_lines) < 2:
        raise VerificationError("research.md must record at least two consulted sources")

    broadcom_sources = 0
    combined = " ".join(source_lines).lower()
    for index, line in enumerate(source_lines, start=1):
        urls = re.findall(r"https://[^\s)>]+", line)
        if len(urls) != 1:
            raise VerificationError(
                f"research.md source {index} must contain exactly one HTTPS URL"
            )
        url = urls[0].rstrip(".,;:")
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        if not hostname or hostname in {"localhost", "127.0.0.1"} or hostname.endswith(".invalid"):
            raise VerificationError(f"research.md source {index} has a placeholder URL")
        if hostname == "broadcom.com" or hostname.endswith(".broadcom.com"):
            broadcom_sources += 1

        before_url, after_url = line.split(urls[0], 1)
        if not re.search(r"\b(Broadcom|VMware|GitHub)\b", before_url, re.IGNORECASE):
            raise VerificationError(
                f"research.md source {index} must identify its publisher"
            )
        if len(re.findall(r"[A-Za-z0-9]+", before_url)) < 5:
            raise VerificationError(f"research.md source {index} must include a source title")

        dates = re.findall(r"\b(20\d{2}-\d{2}-\d{2})\b", line)
        if len(dates) != 1:
            raise VerificationError(
                f"research.md source {index} must include one ISO access date"
            )
        try:
            date.fromisoformat(dates[0])
        except ValueError as exc:
            raise VerificationError(
                f"research.md source {index} has an invalid access date"
            ) from exc

        claim = after_url.replace(dates[0], "")
        if len(re.findall(r"[A-Za-z0-9]+", claim)) < 8:
            raise VerificationError(
                f"research.md source {index} must state the specific claim used"
            )

    if broadcom_sources < 2:
        raise VerificationError(
            "research.md must include real Broadcom-published compatibility and upgrade guidance"
        )
    if not re.search(r"compatib|interoperab|back-in-time", combined):
        raise VerificationError("research.md does not identify compatibility information used")
    if "upgrade" not in combined or "9.1" not in combined or "5.2" not in combined:
        raise VerificationError("research.md does not identify the applicable upgrade-path guidance")


def check_stdlib_package(artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    package = ROOT / "vcf_architecture"
    required = [package / "__init__.py"]
    for path in required:
        if not path.is_file():
            raise VerificationError(f"missing Python package file: {path.relative_to(ROOT)}")

    allowed_roots = set(sys.stdlib_module_names) | {"vcf_architecture", "__future__"}
    python_files = sorted(package.rglob("*.py"))
    if not python_files:
        raise VerificationError("vcf_architecture package contains no Python modules")
    for path in python_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            raise VerificationError(f"invalid Python syntax in {path.relative_to(ROOT)}") from exc
        for node in ast.walk(tree):
            roots: list[str] = []
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = [node.module.split(".", 1)[0]]
            for root in roots:
                if root not in allowed_roots:
                    raise VerificationError(
                        f"non-stdlib import {root!r} in {path.relative_to(ROOT)}"
                    )

    sys.path.insert(0, str(ROOT))
    try:
        module = importlib.import_module("vcf_architecture")
    except Exception as exc:
        raise VerificationError(f"could not import vcf_architecture: {exc}") from exc
    finally:
        sys.path.pop(0)
    builder = getattr(module, "build_architecture", None)
    if not callable(builder):
        raise VerificationError("vcf_architecture.build_architecture must be callable")
    try:
        generated = builder(inventory, snapshot)
    except Exception as exc:
        raise VerificationError(f"build_architecture failed: {exc}") from exc
    assert_equal(generated, artifact, "package-generated artifact")

    inventory_before = deepcopy(inventory)
    snapshot_before = deepcopy(snapshot)
    assert_equal(builder(inventory, snapshot), generated, "deterministic package output")
    assert_equal(inventory, inventory_before, "inventory input after generation")
    assert_equal(snapshot, snapshot_before, "compatibility input after generation")

    changed_inventory = deepcopy(inventory)
    changed_inventory["management_domain"]["management_hosts"][0]["hostname"] = "chi-a-esx09"
    changed_inventory["networks"][0]["gateway"] = "10.10.10.254"
    try:
        changed = builder(changed_inventory, snapshot)
    except Exception as exc:
        raise VerificationError(f"build_architecture failed for changed inventory: {exc}") from exc
    changed_hosts = {
        item.get("hostname") for item in changed["greenfield_sddc_spec"].get("hostSpecs", [])
    }
    if "chi-a-esx09" not in changed_hosts or inventory_before["management_domain"]["management_hosts"][0]["hostname"] in changed_hosts:
        raise VerificationError("build_architecture does not derive management hosts from inventory")
    changed_networks = changed["greenfield_sddc_spec"].get("networkSpecs", [])
    if not any(
        item.get("networkType") == "MANAGEMENT"
        and item.get("subnet") == "10.10.10.0/24"
        and item.get("gateway") == "10.10.10.254"
        for item in changed_networks
    ):
        raise VerificationError("build_architecture does not derive site networks from inventory")


def main() -> int:
    try:
        artifact = load_json(ARTIFACT)
        openapi = load_json(OPENAPI)

        # Required ordering: validate against the installer specification first.
        validate_sddc_first(artifact, openapi)

        inventory = load_json(INVENTORY)
        snapshot = load_json(SNAPSHOT)
        output_schema = load_json(OUTPUT_SCHEMA)

        actual_digest = hashlib.sha256(OPENAPI.read_bytes()).hexdigest()
        assert_equal(actual_digest, snapshot["installer_spec"]["sha256"], "installer spec digest")
        assert_equal(openapi.get("info", {}).get("version"), "9.1.0.0", "installer spec version")
        validate_schema(artifact, output_schema, output_schema, "$")

        check_sddc_design(artifact["greenfield_sddc_spec"], inventory, snapshot)
        check_topology(artifact["topology"], inventory, snapshot)
        check_migration_plan(artifact["migration_plan"], inventory, snapshot)
        check_research_record()
        check_stdlib_package(artifact, inventory, snapshot)
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: architecture.json is schema-valid, compatible, complete, and reproducible")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
