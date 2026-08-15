#!/usr/bin/env python3
"""Deterministic acceptance verifier for vcfarch-0150.

The verifier is deliberately offline. Live research belongs to the solving
agent's trace; acceptance is based only on the pinned inputs and produced
architecture.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
OPENAPI_PATH = ROOT / "specifications/vcf-installer/vcf-installer-openapi.json"

PROTECTED_SHA256 = {
    "TestMain.java": "411533385f4af4294574b8ca352613568c47aa9b3681c9638de2e2b2ad617384",
    "estate-inventory.json": "a5d060221b6eaa84730fca90c5c8caaa4e3c360c459d3a2d9f096d95a16b8050",
    "compatibility-snapshot.json": "70d2843aac253b006d9c338c6d1de2a3293403957f9398b88b40c024d6bae49a",
    "architecture-plan.schema.json": "77b125ab2608a447b3bd337bcf2f6597347856c6a8519b2439f31dc4a647b0ba",
    "specifications/vcf-installer/vcf-installer-openapi.json":
        "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d",
    "VCF_API_SPECS_LICENSE.txt":
        "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
    "VCF_API_SPECS_NOTICE.md":
        "3a64f85b457fbc22ef9cd0ffc74ec69f47b10f11ef3501bef34efec9a5dc6771",
}


class VerificationError(AssertionError):
    pass


class SchemaValidationError(VerificationError):
    pass


def fail(message: str) -> None:
    raise VerificationError(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError(f"cannot load {path.name}: {exc}") from exc


def resolve_pointer(document: Any, reference: str) -> Any:
    if not reference.startswith("#/"):
        raise SchemaValidationError(f"only local schema references are supported: {reference}")
    value = document
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        try:
            value = value[int(part)] if isinstance(value, list) else value[part]
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise SchemaValidationError(f"unresolvable schema reference: {reference}") from exc
    return value


def is_json_type(value: Any, expected: str) -> bool:
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
    raise SchemaValidationError(f"unsupported schema type {expected!r}")


def validate_schema(instance: Any, schema: Any, root: Any, path: str = "$") -> None:
    """Validate the JSON Schema/OpenAPI subset exercised by the pinned specs."""
    if isinstance(schema, bool):
        if not schema:
            raise SchemaValidationError(f"{path}: rejected by false schema")
        return
    if not isinstance(schema, dict):
        raise SchemaValidationError(f"{path}: malformed schema")

    if "$ref" in schema:
        validate_schema(instance, resolve_pointer(root, schema["$ref"]), root, path)
        siblings = {key: value for key, value in schema.items() if key != "$ref"}
        if siblings:
            validate_schema(instance, siblings, root, path)
        return

    for branch in schema.get("allOf", []):
        validate_schema(instance, branch, root, path)

    if "anyOf" in schema:
        if not any(_branch_valid(instance, branch, root, path) for branch in schema["anyOf"]):
            raise SchemaValidationError(f"{path}: does not satisfy anyOf")

    if "oneOf" in schema:
        matches = sum(_branch_valid(instance, branch, root, path) for branch in schema["oneOf"])
        if matches != 1:
            raise SchemaValidationError(f"{path}: expected exactly one oneOf match, got {matches}")

    if "const" in schema and instance != schema["const"]:
        raise SchemaValidationError(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        raise SchemaValidationError(f"{path}: value is not in enum")

    expected_type = schema.get("type")
    if expected_type is not None:
        expected_types = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(is_json_type(instance, item) for item in expected_types):
            raise SchemaValidationError(f"{path}: expected type {expected_type!r}")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        missing = [name for name in required if name not in instance]
        if missing:
            raise SchemaValidationError(f"{path}: missing required properties {missing}")
        properties = schema.get("properties", {})
        for name, subschema in properties.items():
            if name in instance:
                validate_schema(instance[name], subschema, root, f"{path}.{name}")
        additional = schema.get("additionalProperties", True)
        for name, value in instance.items():
            if name in properties:
                continue
            if additional is False:
                raise SchemaValidationError(f"{path}: additional property {name!r} is forbidden")
            if isinstance(additional, dict):
                validate_schema(value, additional, root, f"{path}.{name}")
        if "minProperties" in schema and len(instance) < schema["minProperties"]:
            raise SchemaValidationError(f"{path}: too few properties")
        if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
            raise SchemaValidationError(f"{path}: too many properties")

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            raise SchemaValidationError(f"{path}: too few items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            raise SchemaValidationError(f"{path}: too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(encoded) != len(set(encoded)):
                raise SchemaValidationError(f"{path}: duplicate array items")
        if "items" in schema:
            for index, item in enumerate(instance):
                validate_schema(item, schema["items"], root, f"{path}[{index}]")

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            raise SchemaValidationError(f"{path}: string is too short")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            raise SchemaValidationError(f"{path}: string is too long")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], instance)
            except re.error as exc:
                raise SchemaValidationError(f"{path}: invalid pinned pattern: {exc}") from exc
            if matched is None:
                raise SchemaValidationError(f"{path}: string does not match pattern")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            raise SchemaValidationError(f"{path}: value is below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            raise SchemaValidationError(f"{path}: value is above maximum")


def _branch_valid(instance: Any, schema: Any, root: Any, path: str) -> bool:
    try:
        validate_schema(instance, schema, root, path)
        return True
    except SchemaValidationError:
        return False


def produce_artifact() -> dict[str, Any]:
    source = ROOT / "ArchitectureClient.java"
    if not source.is_file():
        fail("ArchitectureClient.java is missing")
    with tempfile.TemporaryDirectory(prefix="vcfarch-0150-") as temp:
        compile_result = subprocess.run(
            ["javac", "-encoding", "UTF-8", "-d", temp, str(source), str(ROOT / "TestMain.java")],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
        if compile_result.returncode != 0:
            fail(f"javac failed:\n{compile_result.stderr}")
        run_result = subprocess.run(
            [
                "java",
                "-cp",
                temp,
                "TestMain",
                str(ROOT / "estate-inventory.json"),
                str(ROOT / "compatibility-snapshot.json"),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
        if run_result.returncode != 0:
            fail(f"TestMain failed:\n{run_result.stderr}")
        try:
            artifact = json.loads(run_result.stdout)
        except json.JSONDecodeError as exc:
            fail(f"TestMain stdout is not one JSON artifact: {exc}")
        if not isinstance(artifact, dict):
            fail("architecture artifact must be a JSON object")
        return artifact


def verify_protected_files() -> None:
    for relative, expected in PROTECTED_SHA256.items():
        path = ROOT / relative
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            fail(f"protected file is missing: {relative}: {exc}")
        if actual != expected:
            fail(f"protected file changed: {relative}")


def flatten_inventory(inventory: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    components: dict[str, dict[str, Any]] = {}
    roles: dict[str, str] = {}
    for domain in inventory["domains"]:
        domain_id = domain["domainId"]
        roles[domain_id] = domain["role"]
        for raw in domain["components"]:
            component_id = raw["componentId"]
            if component_id in components:
                fail(f"fixture contains duplicate component {component_id}")
            components[component_id] = {**raw, "domainId": domain_id}
    return components, roles


def semantic_checks(
    artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    for source in artifact["researchSources"]:
        parsed = urllib.parse.urlsplit(source["url"])
        host = (parsed.hostname or "").lower()
        broadcom_host = host == "broadcom.com" or host.endswith(".broadcom.com")
        vmware_publisher_host = host == "vmware.github.io"
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or not (broadcom_host or vmware_publisher_host)
        ):
            fail(
                "research source URLs must identify HTTPS Broadcom-published sources"
            )

    context = inventory["installerSpecContext"]
    for key, expected in context.items():
        if artifact.get(key) != expected:
            fail(f"SddcSpec field {key!r} does not match installerSpecContext")

    exact_root_fields = {
        "scenario": inventory["scenario"],
        "estateId": inventory["estateId"],
        "targetRelease": inventory["targetRelease"],
        "snapshotId": snapshot["snapshotId"],
    }
    for key, expected in exact_root_fields.items():
        if artifact.get(key) != expected:
            fail(f"artifact {key!r} must be {expected!r}")
    if snapshot["targetRelease"] != inventory["targetRelease"]:
        fail("pinned inputs disagree about target release")

    expected_components, domain_roles = flatten_inventory(inventory)
    actual_list = artifact["components"]
    actual_components = {item["componentId"]: item for item in actual_list}
    if len(actual_components) != len(actual_list):
        fail("component plan contains duplicate component IDs")
    if set(actual_components) != set(expected_components):
        fail("component plan must name every and only inventoried component")

    declared_gate_ids = {item["gateId"] for item in artifact["migrationPlan"]["gates"]}
    if len(declared_gate_ids) != len(artifact["migrationPlan"]["gates"]):
        fail("migration plan declares a gate more than once")
    expected_gate_ids = {item["gateId"] for item in snapshot["gates"]}
    if declared_gate_ids != expected_gate_ids:
        fail("migration plan must declare every and only pinned gate")

    for component_id, source in expected_components.items():
        actual = actual_components[component_id]
        role = domain_roles[source["domainId"]]
        target = (
            source["version"]
            if role == "MANAGEMENT"
            else snapshot["componentTargets"][source["product"]]
        )
        expected = {
            "componentId": component_id,
            "domainId": source["domainId"],
            "product": source["product"],
            "currentVersion": source["version"],
            "targetVersion": target,
            "disposition": "PRESERVE" if role == "MANAGEMENT" else "MIGRATE",
            "gateIds": snapshot["componentGateIds"][component_id],
        }
        if actual != expected:
            fail(f"component plan does not match pinned authority for {component_id}")
        if not set(actual["gateIds"]).issubset(declared_gate_ids):
            fail(f"component {component_id} refers to an undeclared gate")

    management_domain = next(
        domain for domain in inventory["domains"] if domain["role"] == "MANAGEMENT"
    )
    candidate_domain = next(
        domain for domain in inventory["domains"] if domain["role"] == "IMPORT_CANDIDATE"
    )
    expected_invariant = {
        "domainId": management_domain["domainId"],
        "disposition": "PRESERVE",
        "gateId": "GATE-MANAGEMENT-IMMUTABLE",
    }
    if artifact["managementDomainInvariant"] != expected_invariant:
        fail("managementDomainInvariant does not preserve the management domain")

    migration = artifact["migrationPlan"]
    if migration["strategy"] != "IMPORT_THEN_LIFECYCLE":
        fail("migration strategy must route through import then lifecycle management")
    if migration["workloadDomainId"] != candidate_domain["domainId"]:
        fail("migration plan targets the wrong workload domain")

    actual_gates = {item["gateId"]: item for item in migration["gates"]}
    for pinned in snapshot["gates"]:
        expected = {
            "gateId": pinned["gateId"],
            "status": "SATISFIED_BY_PLAN",
            "satisfiedBySequence": pinned["satisfiedBySequence"],
        }
        if actual_gates[pinned["gateId"]] != expected:
            fail(f"gate resolution is wrong for {pinned['gateId']}")

    steps = migration["steps"]
    if [step["sequence"] for step in steps] != list(range(1, len(steps) + 1)):
        fail("migration step sequence must be contiguous and ordered")
    if steps != snapshot["requiredSequence"]:
        fail("migration steps do not match the pinned required sequence")

    management_ids = {
        item["componentId"] for item in management_domain["components"]
    }
    for step in steps:
        if management_ids.intersection(step["componentIds"]):
            fail("a migration action disturbs a management-domain component")
        if not set(step["gateIds"]).issubset(declared_gate_ids):
            fail(f"step {step['sequence']} refers to an undeclared gate")

    for forbidden in snapshot["forbiddenTransitions"]:
        upgrade_sequence = next(
            step["sequence"]
            for step in steps
            if forbidden["componentId"] in step["componentIds"]
            and step["targetVersions"].get(forbidden["componentId"])
            == forbidden["targetVersion"]
        )
        blocker_sequence = next(
            step["sequence"]
            for step in steps
            if step["targetVersions"].get(forbidden["whileComponentId"])
            == snapshot["componentTargets"]["NSX_MANAGER"]
        )
        if blocker_sequence >= upgrade_sequence:
            fail("forbidden vCenter-first transition was not routed around")
        vcenter_step = next(step for step in steps if step["sequence"] == upgrade_sequence)
        if forbidden["resolvedByGateId"] not in vcenter_step["gateIds"]:
            fail("vCenter transition does not name its interoperability gate")


def main() -> int:
    artifact = produce_artifact()

    # First acceptance check: validate the artifact itself as SddcSpec using the
    # schema loaded from the pinned upstream installer OpenAPI document.
    openapi = load_json(OPENAPI_PATH)
    sddc_spec_schema = openapi["components"]["schemas"]["SddcSpec"]
    validate_schema(artifact, sddc_spec_schema, openapi)

    verify_protected_files()
    plan_schema = load_json(ROOT / "architecture-plan.schema.json")
    validate_schema(artifact, plan_schema, plan_schema)
    inventory = load_json(ROOT / "estate-inventory.json")
    snapshot = load_json(ROOT / "compatibility-snapshot.json")
    semantic_checks(artifact, inventory, snapshot)
    print("PASS: schema-valid VCF workload-domain architecture matches the pinned estate")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (VerificationError, KeyError, TypeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
