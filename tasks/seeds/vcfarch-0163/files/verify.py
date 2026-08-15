#!/usr/bin/env python3
"""Deterministic verifier for the VCF migration architecture artifact."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent
ARTIFACT = ROOT / "migration_plan.json"
PROTECTED_HASHES = {
    "estate_inventory.json": "b98377fcb0ceb89c4ee9f0fab450dc017074794d773ebab3696d8f432032039b",
    "compatibility_snapshot.json": "77fe758cfb0bcf7caa6c73b13ba47d94b6820a6c6ae356acf21b89ba8cec4bd1",
    "installer_spec.json": "460d785b64e1b79205b4512b23f04abe57d2ae588da25d3de13b80d110bd8afd",
}


class VerificationError(Exception):
    pass


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VerificationError(f"missing required file: {path.name}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(f"invalid JSON in {path.name}: {exc}") from exc


def json_type_matches(value, expected: str) -> bool:
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


def validate_schema(value, schema: dict, path: str = "$") -> list[str]:
    """Validate the JSON-Schema subset used by installer_spec.json."""
    errors: list[str] = []
    expected_type = schema.get("type")
    if expected_type and not json_type_matches(value, expected_type):
        return [f"{path}: expected {expected_type}, got {type(value).__name__}"]

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value is not in the allowed enum")

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path}: string is shorter than minLength")
        pattern = schema.get("pattern")
        if pattern and re.search(pattern, value) is None:
            errors.append(f"{path}: string does not match {pattern!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: value is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: value is above maximum")

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path}: array has too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: array has too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True) for item in value]
            if len(encoded) != len(set(encoded)):
                errors.append(f"{path}: array items are not unique")
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(value):
                errors.extend(validate_schema(item, item_schema, f"{path}[{index}]"))

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
                errors.extend(validate_schema(value[key], child_schema, f"{path}.{key}"))
    return errors


def assert_protected_inputs() -> None:
    for name, expected in PROTECTED_HASHES.items():
        path = ROOT / name
        if not path.is_file():
            raise VerificationError(f"protected input missing: {name}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise VerificationError(f"protected input was modified: {name}")


def inventory_sources(inventory: dict) -> dict:
    return {item["id"]: item for item in inventory["migration_sources"]}


def validate_research(artifact: dict) -> None:
    """Check that the recorded research satisfies the prompt's artifact contract."""
    research = artifact["research"]
    urls: set[str] = set()
    claims: list[str] = []
    for index, source in enumerate(research):
        publisher = source["publisher"].casefold()
        if "broadcom" not in publisher:
            raise VerificationError(f"research source {index} is not Broadcom-published")

        parsed = urlsplit(source["url"])
        hostname = (parsed.hostname or "").casefold()
        if (
            parsed.scheme != "https"
            or not hostname
            or not (hostname == "broadcom.com" or hostname.endswith(".broadcom.com"))
            or not parsed.path.strip("/")
        ):
            raise VerificationError(
                f"research source {index} must use a full HTTPS URL on a Broadcom domain"
            )
        normalized_url = source["url"].rstrip("/")
        if normalized_url in urls:
            raise VerificationError("research sources must use distinct URLs")
        urls.add(normalized_url)
        claims.extend(claim.casefold() for claim in source["claims"])

    topic_rules = {
        "8.x-to-9.0 migration paths": (
            (
                "upgrade",
                "migration",
                "migrate",
                "transition",
                "in-place",
                "fresh",
                "greenfield",
                "parallel",
            ),
            ("9.0", "9.0.x", "9.x"),
        ),
        "inventoried-content compatibility": (
            (
                "compatib",
                "content",
                "plugin",
                "management pack",
                "integration",
                "supported",
                "unsupported",
                "preserve",
                "retire",
            ),
        ),
        "end-of-general-support boundaries": (
            (
                "eogs",
                "end of general support",
                "general support",
                "support ends",
                "support boundary",
                "lifecycle date",
            ),
        ),
        "placement and sizing constraints": (
            (
                "sizing",
                "vcpu",
                "cpu",
                "memory",
                "ram",
                "capacity",
                "storage",
                "latency",
                "witness",
                "fault domain",
                "node",
            ),
        ),
    }
    for topic, groups in topic_rules.items():
        if not any(all(any(token in claim for token in group) for group in groups) for claim in claims):
            raise VerificationError(f"recorded research does not cover {topic}")


def validate_support_boundaries(artifact: dict, snapshot: dict) -> None:
    actual = {item["source_id"]: item for item in artifact["support_boundaries"]}
    if set(actual) != set(snapshot["products"]):
        raise VerificationError("support boundaries must cover each migration source exactly once")
    for source_id, rule in snapshot["products"].items():
        item = actual[source_id]
        expected = {
            "source_id": source_id,
            "source_product": rule["source_product"],
            "source_version": rule["source_version"],
            **rule["support_boundary"],
        }
        for key, value in expected.items():
            if item[key] != value:
                raise VerificationError(f"incorrect {key} in support boundary for {source_id}")


def expected_nodes(rows: list[list]) -> list[dict]:
    keys = ["id", "role", "site_id", "fault_domain", "vcpu", "memory_gib", "storage_gib"]
    return [dict(zip(keys, row)) for row in rows]


def expected_capacity(rows: list[list]) -> list[dict]:
    keys = ["metric", "required", "designed", "unit"]
    return [dict(zip(keys, row)) for row in rows]


def validate_architecture(artifact: dict, snapshot: dict) -> None:
    actual_arch = artifact["target_architecture"]
    expected_arch = snapshot["target_architecture"]
    actual_md = actual_arch["management_domain"]
    expected_md = expected_arch["management_domain"]
    for key in ("topology", "data_sites", "witness"):
        if actual_md[key] != expected_md[key]:
            raise VerificationError(f"management-domain {key} violates the pinned design")

    components = {item["id"]: item for item in actual_arch["components"]}
    if set(components) != set(expected_arch["components"]):
        raise VerificationError("target architecture must contain exactly the three target components")
    for component_id, expected in expected_arch["components"].items():
        actual = components[component_id]
        for key in ("name", "version", "deployment_model", "size_profile"):
            if actual[key] != expected[key]:
                raise VerificationError(f"incorrect {key} for {component_id}")
        if sorted(actual["nodes"], key=lambda item: item["id"]) != sorted(
            expected_nodes(expected["nodes"]), key=lambda item: item["id"]
        ):
            raise VerificationError(f"incorrect node placement or sizing for {component_id}")
        if sorted(actual["capacity"], key=lambda item: item["metric"]) != sorted(
            expected_capacity(expected["capacity"]), key=lambda item: item["metric"]
        ):
            raise VerificationError(f"incorrect capacity design for {component_id}")
        if any(item["designed"] < item["required"] for item in actual["capacity"]):
            raise VerificationError(f"undersized capacity for {component_id}")


def validate_migration_plan(artifact: dict, inventory: dict, snapshot: dict) -> None:
    steps = artifact["migration_plan"]
    if [step["order"] for step in steps] != [1, 2, 3]:
        raise VerificationError("migration plan must be ordered 1, 2, 3")
    if [step["source_id"] for step in steps] != snapshot["sequence"]:
        raise VerificationError("migration sequence does not match the pinned compatibility order")

    sources = inventory_sources(inventory)
    for step in steps:
        source_id = step["source_id"]
        if source_id not in sources:
            raise VerificationError(f"unknown migration source: {source_id}")
        source = sources[source_id]
        rule = snapshot["products"][source_id]
        expected_fields = {
            "source_product": source["product"],
            "source_version": source["version"],
            "target_component": rule["target_component"],
            "target_version": rule["target_version"],
            "method": rule["migration_method"],
        }
        for key, value in expected_fields.items():
            if step[key] != value:
                raise VerificationError(f"incorrect {key} for migration step {source_id}")

        actual_dispositions: dict[str, tuple[str, str]] = {}
        for item in step["carry_forward"]:
            inventory_id = item["inventory_id"]
            if inventory_id in actual_dispositions:
                raise VerificationError(f"duplicate content disposition: {inventory_id}")
            actual_dispositions[inventory_id] = ("carry", item["handling"])
        for item in step["abandoned"]:
            inventory_id = item["inventory_id"]
            if inventory_id in actual_dispositions:
                raise VerificationError(f"duplicate content disposition: {inventory_id}")
            actual_dispositions[inventory_id] = ("abandon", item["handling"])
        expected_ids = {item["id"] for item in source["content"]}
        if set(actual_dispositions) != expected_ids:
            missing = sorted(expected_ids - set(actual_dispositions))
            extra = sorted(set(actual_dispositions) - expected_ids)
            raise VerificationError(
                f"content partition mismatch for {source_id}; missing={missing}, extra={extra}"
            )
        expected_dispositions = {
            key: tuple(value) for key, value in rule["content_dispositions"].items()
        }
        if actual_dispositions != expected_dispositions:
            raise VerificationError(f"content compatibility decisions are incorrect for {source_id}")

        gates = {gate["gate_id"]: gate for gate in step["gates"]}
        expected_gates = rule["required_gates"]
        if len(gates) != len(step["gates"]):
            raise VerificationError(f"duplicate gate IDs in {source_id}")
        if set(gates) != set(expected_gates):
            raise VerificationError(f"gate set is incomplete for {source_id}")
        for gate_id, token_rules in expected_gates.items():
            gate = gates[gate_id]
            for field, token_key in (
                ("condition", "condition_tokens"),
                ("evidence", "evidence_tokens"),
                ("rollback", "rollback_tokens"),
            ):
                haystack = gate[field].casefold()
                for token in token_rules[token_key]:
                    if token.casefold() not in haystack:
                        raise VerificationError(
                            f"gate {gate_id} {field} omits required fact {token!r}"
                        )


def validate_stdlib_package() -> None:
    package = ROOT / "vcf_migration"
    python_files = sorted(package.rglob("*.py"))
    if not python_files:
        raise VerificationError("vcf_migration package is missing")
    stdlib = set(sys.stdlib_module_names)
    for path in python_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            raise VerificationError(f"invalid Python in {path.relative_to(ROOT)}: {exc}") from exc
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                top = name.split(".", 1)[0]
                if top not in stdlib and top != "vcf_migration":
                    raise VerificationError(
                        f"non-stdlib import {name!r} in {path.relative_to(ROOT)}"
                    )
    forbidden = ("requirements.txt", "Pipfile", "poetry.lock", "uv.lock")
    for name in forbidden:
        if (ROOT / name).exists():
            raise VerificationError(f"external dependency manifest is not allowed: {name}")


def validate_reproducible_output(artifact: dict) -> None:
    with tempfile.TemporaryDirectory(prefix="vcfarch-") as directory:
        output = Path(directory) / "migration_plan.json"
        result = subprocess.run(
            [sys.executable, "-m", "vcf_migration", "--output", str(output)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise VerificationError(f"package entry point failed: {detail[:500]}")
        generated = load_json(output)
        if generated != artifact:
            raise VerificationError("package output does not reproduce migration_plan.json")


def main() -> int:
    try:
        spec = load_json(ROOT / "installer_spec.json")
        artifact = load_json(ARTIFACT)

        # Contract requirement: schema validation is the first validation phase.
        schema_errors = validate_schema(artifact, spec["artifact_schema"])
        if schema_errors:
            preview = "; ".join(schema_errors[:12])
            raise VerificationError(f"artifact schema validation failed: {preview}")

        assert_protected_inputs()
        inventory = load_json(ROOT / "estate_inventory.json")
        snapshot = load_json(ROOT / "compatibility_snapshot.json")
        if artifact["estate_id"] != inventory["estate_id"]:
            raise VerificationError("artifact estate_id does not match the fixture")
        if artifact["target_release"] != snapshot["target_release"]:
            raise VerificationError("artifact target release does not match the snapshot")
        validate_research(artifact)
        validate_support_boundaries(artifact, snapshot)
        validate_architecture(artifact, snapshot)
        validate_migration_plan(artifact, inventory, snapshot)
        validate_stdlib_package()
        validate_reproducible_output(artifact)
        assert_protected_inputs()
    except (VerificationError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: VCF migration architecture is schema-valid and matches the pinned authority")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
