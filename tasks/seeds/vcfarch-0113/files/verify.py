#!/usr/bin/env python3
"""Protected, offline acceptance verifier for the VCF architecture artifact."""

from __future__ import annotations

import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent
PLAN_PATH = ROOT / "migration-plan.json"
INSTALLER_SPEC_PATH = (
    ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
)


class ValidationError(AssertionError):
    pass


def fail(message: str) -> None:
    raise ValidationError(message)


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"missing required file: {path.relative_to(ROOT)}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {path.relative_to(ROOT)}: {exc}")


def json_pointer(document, pointer: str):
    if not pointer.startswith("#/"):
        fail(f"only local schema references are supported: {pointer}")
    value = document
    for raw in pointer[2:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        try:
            value = value[token]
        except (KeyError, TypeError):
            fail(f"unresolved schema reference: {pointer}")
    return value


class SchemaValidator:
    """Small deterministic validator for the JSON Schema subset used here."""

    def __init__(self, document):
        self.document = document

    def validate(self, value, schema, path="$" ) -> None:
        if "$ref" in schema:
            self.validate(value, json_pointer(self.document, schema["$ref"]), path)
            return

        if value is None and schema.get("nullable"):
            return

        for child in schema.get("allOf", []):
            self.validate(value, child, path)

        if "anyOf" in schema:
            if not any(self._accepts(value, child, path) for child in schema["anyOf"]):
                fail(f"{path}: does not match any allowed schema")
        if "oneOf" in schema:
            matches = sum(self._accepts(value, child, path) for child in schema["oneOf"])
            if matches != 1:
                fail(f"{path}: matches {matches} oneOf alternatives, expected exactly one")

        if "const" in schema and value != schema["const"]:
            fail(f"{path}: expected constant {schema['const']!r}, got {value!r}")
        if "enum" in schema and value not in schema["enum"]:
            fail(f"{path}: {value!r} is not in {schema['enum']!r}")

        expected_type = schema.get("type")
        if expected_type:
            self._validate_type(value, expected_type, path)

        if isinstance(value, dict):
            required = schema.get("required", [])
            missing = [name for name in required if name not in value]
            if missing:
                fail(f"{path}: missing required properties {missing}")
            properties = schema.get("properties", {})
            for name, child_value in value.items():
                if name in properties:
                    self.validate(child_value, properties[name], f"{path}.{name}")
                elif schema.get("additionalProperties") is False:
                    fail(f"{path}: unexpected property {name!r}")
                elif isinstance(schema.get("additionalProperties"), dict):
                    self.validate(
                        child_value,
                        schema["additionalProperties"],
                        f"{path}.{name}",
                    )

        if isinstance(value, list):
            if len(value) < schema.get("minItems", 0):
                fail(f"{path}: has fewer than {schema['minItems']} items")
            if "maxItems" in schema and len(value) > schema["maxItems"]:
                fail(f"{path}: has more than {schema['maxItems']} items")
            if schema.get("uniqueItems"):
                encoded = [json.dumps(item, sort_keys=True) for item in value]
                if len(encoded) != len(set(encoded)):
                    fail(f"{path}: items are not unique")
            if isinstance(schema.get("items"), dict):
                for index, item in enumerate(value):
                    self.validate(item, schema["items"], f"{path}[{index}]")

        if isinstance(value, str):
            if len(value) < schema.get("minLength", 0):
                fail(f"{path}: string is shorter than {schema['minLength']}")
            if "maxLength" in schema and len(value) > schema["maxLength"]:
                fail(f"{path}: string is longer than {schema['maxLength']}")
            if "pattern" in schema and re.search(schema["pattern"], value) is None:
                fail(f"{path}: {value!r} does not match {schema['pattern']!r}")

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema and value < schema["minimum"]:
                fail(f"{path}: {value} is below minimum {schema['minimum']}")
            if "maximum" in schema and value > schema["maximum"]:
                fail(f"{path}: {value} is above maximum {schema['maximum']}")

    def _accepts(self, value, schema, path):
        try:
            self.validate(value, schema, path)
            return True
        except ValidationError:
            return False

    @staticmethod
    def _validate_type(value, expected, path):
        choices = expected if isinstance(expected, list) else [expected]
        checks = {
            "object": lambda item: isinstance(item, dict),
            "array": lambda item: isinstance(item, list),
            "string": lambda item: isinstance(item, str),
            "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "number": lambda item: isinstance(item, (int, float))
            and not isinstance(item, bool),
            "boolean": lambda item: isinstance(item, bool),
            "null": lambda item: item is None,
        }
        if not any(checks[kind](value) for kind in choices):
            fail(f"{path}: expected type {expected!r}, got {type(value).__name__}")


def assert_equal(actual, expected, label):
    if actual != expected:
        fail(f"{label}: expected {expected!r}, got {actual!r}")


def validate_installer_schema_first():
    """Load only the artifact and installer contract, then perform the first check."""
    plan = load_json(PLAN_PATH)
    installer = load_json(INSTALLER_SPEC_PATH)
    try:
        sddc_schema = installer["components"]["schemas"]["SddcSpec"]
    except KeyError:
        fail("bundled installer specification does not contain SddcSpec")
    SchemaValidator(installer).validate(plan, sddc_schema)
    print("PASS installer SddcSpec validation (checked first)")
    return plan, installer


def validate_fixed_plan_schema(plan):
    schema = load_json(ROOT / "schemas" / "migration-plan.schema.json")
    SchemaValidator(schema).validate(plan, schema)
    print("PASS migration-plan schema")


def validate_research_record():
    research = load_json(ROOT / "research" / "consulted-sources.json")
    researched_at = research.get("researchedAt")
    if not isinstance(researched_at, str) or re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", researched_at
    ) is None:
        fail("research.researchedAt must be a UTC ISO-8601 timestamp ending in Z")
    try:
        parsed_time = datetime.fromisoformat(researched_at.replace("Z", "+00:00"))
    except ValueError:
        fail("research.researchedAt is not a valid timestamp")
    if parsed_time.tzinfo != timezone.utc:
        fail("research.researchedAt must identify UTC")

    sources = research.get("sources")
    if not isinstance(sources, list) or not sources:
        fail("research.sources must be a nonempty array")

    reserved_hosts = {"localhost", "example.com", "example.org", "example.net"}
    reserved_suffixes = (".invalid", ".localhost", ".test", ".example")
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            fail(f"research.sources[{index}] must be an object")
        for field in ("url", "title", "claimsUsed"):
            value = source.get(field)
            if not isinstance(value, str) or not value.strip():
                fail(f"research.sources[{index}].{field} must be a nonempty string")

        url = source["url"]
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if (
            parsed.scheme not in {"http", "https"}
            or not hostname
            or "." not in hostname
            or hostname in reserved_hosts
            or hostname.endswith(reserved_suffixes)
        ):
            fail(f"research.sources[{index}].url is not a public HTTP(S) source")
    print("PASS consulted-source research record")


def validate_installer_identity(installer, snapshot):
    assert_equal(installer.get("openapi"), "3.0.1", "installer OpenAPI version")
    assert_equal(installer.get("info", {}).get("version"), "9.1.0.0", "installer tag")
    pinned = snapshot["installerSpecification"]
    assert_equal(pinned["repository"], "vmware/vcf-api-specs", "spec repository")
    assert_equal(pinned["tag"], "9.1.0.0", "specification snapshot tag")
    actual_hash = hashlib.sha256(INSTALLER_SPEC_PATH.read_bytes()).hexdigest()
    assert_equal(actual_hash, pinned["sha256"], "installer specification SHA-256")
    assert_equal(pinned["schema"], "SddcSpec", "specification root schema")


def validate_target_sddc(plan, inventory, snapshot):
    design = inventory["targetDesign"]
    assert_equal(plan["sddcId"], design["sddcId"], "sddcId")
    assert_equal(plan["workflowType"], "VCF_COMPLETE", "workflowType")
    assert_equal(plan["version"], snapshot["targetBundle"], "SddcSpec version")
    expected_vcenter = {
        "vcenterHostname": design["vcenterHostname"],
        "rootVcenterPassword": design["rootVcenterPassword"],
        "version": snapshot["components"]["VCENTER"]["targetVersion"],
        "useExistingDeployment": False,
    }
    assert_equal(plan["vcenterSpec"], expected_vcenter, "vcenterSpec")
    assert_equal(plan["dnsSpec"], design["dns"], "dnsSpec")
    assert_equal(plan["networkSpecs"], design["networks"], "networkSpecs")
    assert_equal(plan["estateId"], inventory["estateId"], "estateId")
    assert_equal(plan["targetBundle"], inventory["targetBundle"], "targetBundle")
    assert_equal(plan["strategy"], "parallel-vcf-replacement", "migration strategy")
    assert_equal(plan["fleetEndState"], snapshot["fleetEndState"], "fleet end state")


def validate_components_and_gates(plan, inventory, snapshot):
    inventory_by_id = {item["id"]: item for item in inventory["components"]}
    planned_by_id = {item["id"]: item for item in plan["components"]}
    if len(planned_by_id) != len(plan["components"]):
        fail("components: duplicate component id")
    assert_equal(set(planned_by_id), set(inventory_by_id), "component inventory parity")

    expected_gate_ids = set()
    for component_id, source in inventory_by_id.items():
        planned = planned_by_id[component_id]
        authority = snapshot["components"][source["type"]]
        expected = {
            "id": source["id"],
            "type": source["type"],
            "name": source["name"],
            "currentVersion": f"{source['version']} build {source['build']}",
            "targetName": authority["targetName"],
            "targetVersion": authority["targetVersion"],
            "migrationMode": authority["requiredMode"],
            "gatedBy": authority["requiredGates"],
            "finalManagement": snapshot["fleetEndState"],
        }
        assert_equal(planned, expected, f"component {component_id}")
        if authority["directTransition"].startswith("prohibited"):
            if planned["migrationMode"] != "parallel-replacement":
                fail(f"{component_id}: prohibited direct transition was not avoided")
        expected_gate_ids.update(authority["requiredGates"])

    gates = {item["id"]: item for item in plan["gates"]}
    if len(gates) != len(plan["gates"]):
        fail("gates: duplicate gate id")
    assert_equal(set(gates), expected_gate_ids, "technical gate set")
    print("PASS inventory, targets, transition modes, and technical gates")


def validate_steps(plan, inventory, snapshot):
    steps = plan["steps"]
    expected_ids = snapshot["requiredStepOrder"]
    assert_equal([step["id"] for step in steps], expected_ids, "step order")
    sequences = [step["sequence"] for step in steps]
    if any(right <= left for left, right in zip(sequences, sequences[1:])):
        fail("steps: sequence numbers must be strictly increasing")

    all_ids = {item["id"] for item in inventory["components"]}
    known_gates = {item["id"] for item in plan["gates"]}
    seen_steps = set()
    mentioned_components = set()
    by_id = {}
    for step in steps:
        by_id[step["id"]] = step
        unknown_components = set(step["components"]) - all_ids
        if unknown_components:
            fail(f"{step['id']}: unknown components {sorted(unknown_components)}")
        unknown_gates = set(step["requiresGates"]) - known_gates
        if unknown_gates:
            fail(f"{step['id']}: unknown gates {sorted(unknown_gates)}")
        if not set(step["dependsOn"]).issubset(seen_steps):
            fail(f"{step['id']}: dependency does not precede the step")
        assert_equal(
            step["requiresGates"],
            snapshot["requiredStepGates"][step["id"]],
            f"{step['id']} gates",
        )
        seen_steps.add(step["id"])
        mentioned_components.update(step["components"])

    assert_equal(mentioned_components, all_ids, "components named by ordered plan")
    assert_equal(set(by_id["join-fleet"]["components"]), all_ids, "join-fleet coverage")

    core_ids = {
        item["id"]
        for item in inventory["components"]
        if snapshot["components"][item["type"]]["requiredPhase"] == "deploy-target"
    }
    recovery_ids = all_ids - core_ids
    assert_equal(set(by_id["deploy-target"]["components"]), core_ids, "target deployment")
    assert_equal(
        set(by_id["remove-legacy-replication"]["components"]),
        recovery_ids,
        "legacy recovery removal",
    )
    assert_equal(
        set(by_id["transition-recovery"]["components"]),
        recovery_ids,
        "unified recovery transition",
    )
    assert_equal(set(by_id["migrate-workloads"]["components"]), core_ids, "workload migration")
    print("PASS ordered dependency-consistent migration and fleet convergence")


def validate_stdlib_package():
    package_files = sorted((ROOT / "vcf_arch").glob("*.py"))
    if len(package_files) < 2:
        fail("vcf_arch must be a Python package, not only a generated JSON file")
    stdlib = set(sys.stdlib_module_names)
    for path in package_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                if name not in stdlib and name != "vcf_arch":
                    fail(f"{path.name}: non-stdlib import {name!r}")

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    if not re.search(r"(?m)^dependencies\s*=\s*\[\s*\]\s*$", pyproject):
        fail("pyproject.toml must declare no runtime dependencies")
    print("PASS stdlib-only package")


def validate_reproducible_cli(plan):
    with tempfile.TemporaryDirectory(prefix="vcf-arch-verify-") as temp_dir:
        generated = Path(temp_dir) / "migration-plan.json"
        command = [
            sys.executable,
            "-m",
            "vcf_arch",
            "--inventory",
            "inventory/estate.json",
            "--compatibility",
            "compatibility/vcf-9.1.0.0-snapshot.json",
            "--output",
            str(generated),
        ]
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=20,
            check=False,
        )
        if result.returncode != 0:
            fail(f"generator command failed ({result.returncode}): {result.stdout.strip()}")
        regenerated = load_json(generated)
        assert_equal(regenerated, plan, "deterministic CLI output")
    print("PASS deterministic CLI regeneration")


def main():
    try:
        # Binding requirement: installer SddcSpec validation is the first check.
        plan, installer = validate_installer_schema_first()

        validate_fixed_plan_schema(plan)
        validate_research_record()
        inventory = load_json(ROOT / "inventory" / "estate.json")
        snapshot = load_json(ROOT / "compatibility" / "vcf-9.1.0.0-snapshot.json")
        validate_installer_identity(installer, snapshot)
        validate_target_sddc(plan, inventory, snapshot)
        validate_components_and_gates(plan, inventory, snapshot)
        validate_steps(plan, inventory, snapshot)
        validate_stdlib_package()
        validate_reproducible_cli(plan)
    except (ValidationError, KeyError, TypeError, ValueError, SyntaxError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
