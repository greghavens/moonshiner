#!/usr/bin/env python3
"""Deterministic acceptance verifier for the VCF brownfield plan seed.

The first substantive validation is deliberately the vendored VCF Installer
SddcSpec.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "architecture" / "migration-plan.json"
INSTALLER_SPEC = (
    ROOT
    / "specifications"
    / "vcf-installer"
    / "vcf-installer-openapi.json"
)
PLAN_SCHEMA = ROOT / "schemas" / "migration-plan.schema.json"
INVENTORY = ROOT / "fixtures" / "estate-inventory.json"
COMPATIBILITY = ROOT / "fixtures" / "compatibility-snapshot.json"
RESEARCH = ROOT / "research" / "consulted-sources.md"


class VerificationError(AssertionError):
    pass


def fail(message: str) -> None:
    raise VerificationError(message)


def load_json(path: Path, label: str) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        fail(f"{label} is missing: {path.relative_to(ROOT)}")
    except json.JSONDecodeError as exc:
        fail(f"{label} is not valid JSON: {exc}")


def resolve_ref(document: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        fail(f"unsupported non-local schema reference: {ref}")
    value: Any = document
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, dict) or part not in value:
            fail(f"unresolvable schema reference: {ref}")
        value = value[part]
    if not isinstance(value, dict):
        fail(f"schema reference does not resolve to an object: {ref}")
    return value


def is_json_type(instance: Any, type_name: str) -> bool:
    checks = {
        "object": lambda value: isinstance(value, dict),
        "array": lambda value: isinstance(value, list),
        "string": lambda value: isinstance(value, str),
        "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
        "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": lambda value: isinstance(value, bool),
        "null": lambda value: value is None,
    }
    return checks[type_name](instance)


def validate_schema(
    instance: Any,
    schema: dict[str, Any],
    document: dict[str, Any],
    path: str = "$",
) -> None:
    if "$ref" in schema:
        validate_schema(instance, resolve_ref(document, schema["$ref"]), document, path)
        return

    if "allOf" in schema:
        for subschema in schema["allOf"]:
            validate_schema(instance, subschema, document, path)

    if "const" in schema and instance != schema["const"]:
        fail(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        fail(f"{path}: value {instance!r} is not in {schema['enum']!r}")

    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        if not any(is_json_type(instance, item) for item in expected_type):
            fail(f"{path}: expected one of JSON types {expected_type!r}")
    elif isinstance(expected_type, str) and not is_json_type(instance, expected_type):
        fail(f"{path}: expected JSON type {expected_type}")

    if isinstance(instance, dict):
        for required in schema.get("required", []):
            if required not in instance:
                fail(f"{path}: missing required property {required!r}")
        properties = schema.get("properties", {})
        for key, value in instance.items():
            if key in properties:
                validate_schema(value, properties[key], document, f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                fail(f"{path}: unexpected property {key!r}")

    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            fail(f"{path}: has fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            fail(f"{path}: has more than {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            serialized = [json.dumps(item, sort_keys=True) for item in instance]
            if len(serialized) != len(set(serialized)):
                fail(f"{path}: array items must be unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, value in enumerate(instance):
                validate_schema(value, item_schema, document, f"{path}[{index}]")

    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            fail(f"{path}: string is shorter than {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            fail(f"{path}: string is longer than {schema['maxLength']}")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            fail(f"{path}: string does not match {schema['pattern']!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            fail(f"{path}: number is below {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            fail(f"{path}: number is above {schema['maximum']}")


def verify_installer_schema_first() -> dict[str, Any]:
    # Nothing from the fixture, snapshot, or package is read before this phase
    # completes.
    artifact = load_json(ARTIFACT, "migration plan artifact")
    installer = load_json(INSTALLER_SPEC, "pinned VCF Installer OpenAPI document")
    try:
        sddc_schema = installer["components"]["schemas"]["SddcSpec"]
    except (KeyError, TypeError):
        fail("pinned installer document does not contain components.schemas.SddcSpec")
    validate_schema(artifact, sddc_schema, installer)
    return artifact


def exact_index(items: list[dict[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        value = item.get(key)
        if not isinstance(value, str) or not value:
            fail(f"{label} has an item without a non-empty {key}")
        if value in result:
            fail(f"{label} contains duplicate {key} {value!r}")
        result[value] = item
    return result


def verify_target_sddc(
    artifact: dict[str, Any],
    inventory: dict[str, Any],
    compatibility: dict[str, Any],
) -> None:
    inputs = inventory["targetSpecInputs"]
    rules_by_id = exact_index(
        compatibility["componentRules"], "componentId", "snapshot rules"
    )
    expected_networks = inputs["networks"]
    expected_dns = {
        "subdomain": inputs["dnsSubdomain"],
        "nameservers": inputs["dnsServers"],
    }
    expected_values = {
        "sddcId": inputs["sddcId"],
        "workflowType": inputs["workflowType"],
        "version": inventory["targetRelease"],
        "dnsSpec": expected_dns,
        "networkSpecs": expected_networks,
    }
    for key, expected in expected_values.items():
        if artifact.get(key) != expected:
            fail(f"target SddcSpec {key} does not match the estate fixture")

    vcenter = artifact.get("vcenterSpec", {})
    if vcenter.get("vcenterHostname") != inputs["vcenterHostname"]:
        fail("target SddcSpec vCenter hostname does not match the estate fixture")
    if vcenter.get("rootVcenterPassword") != inputs["vcenterRootPasswordToken"]:
        fail("target SddcSpec must preserve the fixture's non-secret password token")
    if vcenter.get("version") != rules_by_id["vc-management"]["target"]:
        fail("target SddcSpec must use the snapshot's supported management vCenter target")
    if vcenter.get("useExistingDeployment") is not True:
        fail("target SddcSpec must mark vCenter as an existing deployment")

    manager = artifact.get("sddcManagerSpec", {})
    if manager != {
        "hostname": inputs["sddcManagerHostname"],
        "version": rules_by_id["sddc-manager"]["target"],
        "useExistingDeployment": True,
    }:
        fail("target SddcSpec has an incorrect SDDC Manager architecture")

    nsxt = artifact.get("nsxtSpec", {})
    if nsxt != {
        "vipFqdn": inputs["sharedNsxVipFqdn"],
        "nsxtManagers": [
            {"hostname": hostname}
            for hostname in inputs["sharedNsxManagerHostnames"]
        ],
        "version": rules_by_id["nsx-shared"]["target"],
        "useExistingDeployment": True,
    }:
        fail("target SddcSpec has an incorrect shared NSX architecture")


def verify_release_path(
    artifact: dict[str, Any], inventory: dict[str, Any], compatibility: dict[str, Any]
) -> None:
    if artifact.get("estateId") != inventory["estateId"]:
        fail("estateId does not match the inventory")
    if artifact.get("sourceRelease") != inventory["currentRelease"]:
        fail("sourceRelease does not match the inventory")
    if artifact.get("targetRelease") != inventory["targetRelease"]:
        fail("targetRelease does not match the inventory")
    if artifact.get("targetRelease") != compatibility["targetRelease"]:
        fail("targetRelease does not match the pinned compatibility snapshot")

    path = artifact.get("releasePath")
    if not isinstance(path, list) or path[0] != inventory["currentRelease"]:
        fail("releasePath must start at the inventory release")
    if path[-1] != inventory["targetRelease"]:
        fail("releasePath must end at the requested target release")

    transition_state = {
        (item["from"], item["to"]): item["supported"]
        for item in compatibility["releaseTransitions"]
    }
    for source, target in zip(path, path[1:]):
        state = transition_state.get((source, target))
        if state is not True:
            fail(f"releasePath contains an unsupported or unknown hop: {source} -> {target}")


def verify_components(
    artifact: dict[str, Any], inventory: dict[str, Any], compatibility: dict[str, Any]
) -> None:
    inventory_by_id = exact_index(inventory["components"], "id", "inventory")
    rules_by_id = exact_index(compatibility["componentRules"], "componentId", "snapshot rules")
    plans_by_id = exact_index(artifact["componentPlans"], "componentId", "componentPlans")
    expected_ids = set(inventory_by_id)
    if set(rules_by_id) != expected_ids or set(plans_by_id) != expected_ids:
        fail("componentPlans must name every and only inventory component")

    blocked = {
        (item["componentId"], item["from"], item["target"])
        for item in compatibility["blockedComponentTransitions"]
    }
    known_gates = {item["id"] for item in compatibility["gates"]}
    for component_id in sorted(expected_ids):
        source = inventory_by_id[component_id]
        rule = rules_by_id[component_id]
        plan = plans_by_id[component_id]
        expected = {
            "componentId": component_id,
            "name": source["name"],
            "kind": source["kind"],
            "domain": source["domain"],
            "currentVersion": source["currentVersion"],
            "targetVersion": rule["target"],
            "strategy": rule["strategy"],
            "gates": rule["requiredGates"],
        }
        if plan != expected:
            fail(f"component plan for {component_id} does not match inventory and compatibility authority")
        transition = (component_id, plan["currentVersion"], plan["targetVersion"])
        if transition in blocked:
            fail(f"component plan selects blocked transition for {component_id}")
        unknown_gates = set(plan["gates"]) - known_gates
        if unknown_gates:
            fail(f"component plan for {component_id} has unknown gates: {sorted(unknown_gates)}")

    steps = artifact["steps"]
    if [step["order"] for step in steps] != list(range(1, len(steps) + 1)):
        fail("steps must be listed in contiguous order starting at 1")
    steps_by_id = exact_index(steps, "componentId", "steps")
    if set(steps_by_id) != expected_ids:
        fail("steps must cover every inventory component exactly once")
    for component_id, step in steps_by_id.items():
        plan = plans_by_id[component_id]
        expected = {
            "order": step["order"],
            "componentId": component_id,
            "fromVersion": plan["currentVersion"],
            "toVersion": plan["targetVersion"],
            "strategy": plan["strategy"],
            "gates": plan["gates"],
        }
        if step != expected:
            fail(f"ordered step for {component_id} disagrees with its component plan")

    positions = {step["componentId"]: index for index, step in enumerate(steps)}
    for before, after in compatibility["precedence"]:
        if positions[before] >= positions[after]:
            fail(f"ordered steps violate compatibility precedence: {before} before {after}")


def verify_stdlib_package() -> None:
    package = ROOT / "vcf_plan"
    python_files = sorted(package.rglob("*.py")) if package.is_dir() else []
    if not python_files or not (package / "__main__.py").is_file():
        fail("vcf_plan must be an importable Python package with __main__.py")
    allowed = set(sys.stdlib_module_names)
    for path in python_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            fail(f"Python syntax error in {path.relative_to(ROOT)}: {exc}")
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module.split(".", 1)[0]]
            for module in modules:
                if module not in allowed and module != "vcf_plan":
                    fail(f"non-stdlib import {module!r} in {path.relative_to(ROOT)}")


def verify_research_record() -> None:
    try:
        text = RESEARCH.read_text(encoding="utf-8")
    except FileNotFoundError:
        fail("research source record is missing: research/consulted-sources.md")
    except UnicodeDecodeError as exc:
        fail(f"research source record is not UTF-8: {exc}")

    dates = re.findall(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)", text)
    if not dates:
        fail("research source record must include an ISO access date")
    try:
        for value in dates:
            date.fromisoformat(value)
    except ValueError:
        fail("research source record contains an invalid ISO access date")

    raw_urls = re.findall(r"https://[^\s)>\]]+", text)
    urls = [url.rstrip(".,;:—") for url in raw_urls]
    if len(urls) < 2:
        fail("research source record must describe the Broadcom sources consulted")
    if len(urls) != len(set(urls)):
        fail("research source record contains duplicate URLs")
    for url in urls:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        if hostname != "broadcom.com" and not hostname.endswith(".broadcom.com"):
            fail(f"research URL is not Broadcom-published: {url}")

    prose = re.sub(r"https://[^\s)>\]]+", " ", text)
    prose = re.sub(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)", " ", prose)
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9.-]*", prose)
    if len(words) < 4 * len(urls):
        fail("research source record lacks titles or relevant findings")


def verify_cli_is_deterministic(artifact: dict[str, Any]) -> None:
    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temp_dir:
        output_one = Path(temp_dir) / "one.json"
        output_two = Path(temp_dir) / "two.json"
        base_command = [
            sys.executable,
            "-B",
            "-m",
            "vcf_plan",
            "--inventory",
            str(INVENTORY),
            "--compatibility",
            str(COMPATIBILITY),
            "--output",
        ]
        for output in (output_one, output_two):
            result = subprocess.run(
                [*base_command, str(output)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=15,
                check=False,
            )
            if result.returncode != 0:
                fail(f"vcf_plan CLI failed: {result.stderr.strip() or result.stdout.strip()}")
        if output_one.read_bytes() != output_two.read_bytes():
            fail("vcf_plan CLI output is not deterministic")
        generated = load_json(output_one, "generated migration plan")
        if generated != artifact:
            fail("checked-in migration plan differs from vcf_plan CLI output")


def main() -> int:
    try:
        artifact = verify_installer_schema_first()

        # Phase two begins only after the full artifact passes SddcSpec.
        plan_schema = load_json(PLAN_SCHEMA, "migration plan schema")
        validate_schema(artifact, plan_schema, plan_schema)
        inventory = load_json(INVENTORY, "estate inventory")
        compatibility = load_json(COMPATIBILITY, "compatibility snapshot")
        verify_target_sddc(artifact, inventory, compatibility)
        verify_release_path(artifact, inventory, compatibility)
        verify_components(artifact, inventory, compatibility)
        verify_stdlib_package()
        verify_research_record()
        verify_cli_is_deterministic(artifact)
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: installer schema, migration architecture, compatibility path, and CLI")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
