#!/usr/bin/env python3
"""Protected verifier for vcfarch-0143 (standard library only)."""

from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import urlparse


EXPECTED_SPEC_SHA256 = "9295f4d07b46343600da2e4a609e166ec48feabcf2189bc20c2f90c9f4174b72"
EXPECTED_FIXTURE_SHA256 = "0b06e5c94fad9da3e6d23cb0bea2278375c848bbbe52a626e4397dccabd2f0be"
EXPECTED_SNAPSHOT_SHA256 = "f25bfd69d8a6df87f22084494d303e08309627e0748bd104c67e16f588495d43"
EXPECTED_PLAN_SCHEMA_SHA256 = "fcc92097729a5d1647f7fba6ea447c2c34cfdebcbd55c867181e6766208064da"


class VerificationError(Exception):
    pass


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError(f"cannot read JSON {path}: {exc}") from exc


def json_pointer(document: Any, pointer: str) -> Any:
    if not pointer.startswith("#/"):
        raise VerificationError(f"only local JSON references are supported: {pointer}")
    value = document
    for raw_part in pointer[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        try:
            value = value[int(part)] if isinstance(value, list) else value[part]
        except (IndexError, KeyError, ValueError, TypeError) as exc:
            raise VerificationError(f"unresolvable JSON reference: {pointer}") from exc
    return value


def type_matches(instance: Any, expected: str) -> bool:
    if expected == "null":
        return instance is None
    if expected == "object":
        return isinstance(instance, dict)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "string":
        return isinstance(instance, str)
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    return True


def schema_errors(instance: Any, schema: Any, document: Any, path: str = "$") -> list[str]:
    """Validate the JSON Schema/OpenAPI keywords used by the pinned specifications."""
    if isinstance(schema, bool):
        return [] if schema else [f"{path}: rejected by false schema"]
    if not isinstance(schema, dict):
        return [f"{path}: malformed schema"]

    if "$ref" in schema:
        return schema_errors(instance, json_pointer(document, schema["$ref"]), document, path)

    errors: list[str] = []
    if "allOf" in schema:
        for subschema in schema["allOf"]:
            errors.extend(schema_errors(instance, subschema, document, path))
    if "anyOf" in schema:
        branches = [schema_errors(instance, item, document, path) for item in schema["anyOf"]]
        if all(branch for branch in branches):
            errors.append(f"{path}: does not match any allowed schema")
    if "oneOf" in schema:
        matches = sum(not schema_errors(instance, item, document, path) for item in schema["oneOf"])
        if matches != 1:
            errors.append(f"{path}: matches {matches} oneOf branches, expected exactly one")
    if "not" in schema and not schema_errors(instance, schema["not"], document, path):
        errors.append(f"{path}: matches a forbidden schema")

    if instance is None and schema.get("nullable") is True:
        return errors

    declared_type = schema.get("type")
    if declared_type is not None:
        allowed_types = declared_type if isinstance(declared_type, list) else [declared_type]
        if not any(type_matches(instance, expected) for expected in allowed_types):
            return errors + [f"{path}: expected type {allowed_types}, got {type(instance).__name__}"]

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: value {instance!r} is not in the enum")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                errors.append(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        for key, value in instance.items():
            child_path = f"{path}.{key}"
            if key in properties:
                errors.extend(schema_errors(value, properties[key], document, child_path))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected property {key!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                errors.extend(
                    schema_errors(value, schema["additionalProperties"], document, child_path)
                )
        if "minProperties" in schema and len(instance) < schema["minProperties"]:
            errors.append(f"{path}: fewer than minProperties")
        if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
            errors.append(f"{path}: more than maxProperties")

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: fewer than minItems")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: more than maxItems")
        if schema.get("uniqueItems"):
            normalized = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(normalized) != len(set(normalized)):
                errors.append(f"{path}: array items are not unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, value in enumerate(instance):
                errors.extend(schema_errors(value, item_schema, document, f"{path}[{index}]"))

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength")
        if "pattern" in schema:
            try:
                if re.search(schema["pattern"], instance) is None:
                    errors.append(f"{path}: does not match pattern {schema['pattern']!r}")
            except re.error as exc:
                raise VerificationError(f"invalid pattern in pinned schema at {path}: {exc}") from exc

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: less than minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: greater than maximum")
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: not greater than exclusiveMinimum")
        if "exclusiveMaximum" in schema and instance >= schema["exclusiveMaximum"]:
            errors.append(f"{path}: not less than exclusiveMaximum")

    return errors


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def matching_path(snapshot: dict[str, Any], component_id: str, source: Any, target: str) -> dict[str, Any] | None:
    matches = [
        edge
        for edge in snapshot["upgradePaths"]
        if edge["componentId"] == component_id
        and edge["fromVersion"] == source
        and edge["toVersion"] == target
    ]
    require(len(matches) <= 1, f"pinned snapshot has duplicate path for {component_id} to {target}")
    return matches[0] if matches else None


def bundle_is_supported(bundle: dict[str, Any], inventory: dict[str, dict[str, Any]], snapshot: dict[str, Any]) -> bool:
    for component_id, target in bundle["bom"].items():
        component = inventory.get(component_id)
        if component is None:
            return False
        edge = matching_path(snapshot, component_id, component["version"], target)
        if edge is None or edge["supported"] is not True:
            return False
    relations = [item for item in snapshot["interoperability"] if item["bundle"] == bundle["version"]]
    if not relations or any(item["supported"] is not True for item in relations):
        return False
    for relation in relations:
        if bundle["bom"].get(relation["leftComponent"]) != relation["leftVersion"]:
            return False
        if bundle["bom"].get(relation["rightComponent"]) != relation["rightVersion"]:
            return False
    return True


def selected_bundle(estate: dict[str, Any], snapshot: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    inventory = {item["id"]: item for item in estate["components"]}
    bundles = sorted(snapshot["bundles"], key=lambda item: item["preference"])
    requested = next((item for item in bundles if item["version"] == estate["requestedBundle"]), None)
    require(requested is not None, "requested bundle is absent from pinned snapshot")
    eligible = [item for item in bundles if item["preference"] >= requested["preference"]]
    avoided: list[str] = []
    for bundle in eligible:
        if bundle_is_supported(bundle, inventory, snapshot):
            return bundle, avoided
        avoided.append(bundle["version"])
    raise VerificationError("pinned snapshot contains no supported destination bundle")


def canonical_set(items: list[Any]) -> set[str]:
    return {json.dumps(item, sort_keys=True, separators=(",", ":")) for item in items}


def expected_sddc_fields(estate: dict[str, Any], bundle: dict[str, Any]) -> dict[str, Any]:
    infra = estate["infrastructure"]
    bom = bundle["bom"]
    return {
        "sddcId": estate["sddcId"],
        "workflowType": "VCF",
        "version": bundle["version"],
        "vcenterSpec": {
            "vcenterHostname": infra["vcenter"]["hostname"],
            "rootVcenterPassword": infra["vcenter"]["rootPasswordRef"],
            "version": bom["vcenter"],
            "useExistingDeployment": True,
            "sslThumbprint": infra["vcenter"]["sslThumbprint"],
        },
        "clusterSpec": infra["cluster"],
        "dvsSpecs": infra["dvsSpecs"],
        "nsxtSpec": {
            "nsxtManagers": [{"hostname": hostname} for hostname in infra["nsx"]["managerHostnames"]],
            "vipFqdn": infra["nsx"]["vipFqdn"],
            "version": bom["nsx"],
            "useExistingDeployment": True,
            "sslThumbprint": infra["nsx"]["sslThumbprint"],
        },
        "networkSpecs": infra["networkSpecs"],
        "dnsSpec": infra["dns"],
        "ntpServers": infra["ntpServers"],
        "sddcManagerSpec": {
            "hostname": infra["sddcManager"]["hostname"],
            "version": bom["sddc_manager"],
            "useExistingDeployment": True,
            "sslThumbprint": infra["sddcManager"]["sslThumbprint"],
        },
    }


def verify_plan(architecture: dict[str, Any], estate: dict[str, Any], snapshot: dict[str, Any]) -> None:
    bundle, avoided = selected_bundle(estate, snapshot)
    plan = architecture["migrationPlan"]
    require(avoided, "scenario must route around at least one unsupported candidate")
    require(architecture["selectedBundle"] == bundle["version"], "wrong selectedBundle")
    require(architecture["avoidedBundles"] == avoided, "avoidedBundles must list skipped candidates in order")
    require(architecture["inventorySha256"] == EXPECTED_FIXTURE_SHA256, "wrong inventorySha256")
    require(plan["estateId"] == estate["estateId"], "wrong migrationPlan.estateId")
    require(plan["sourceBundle"] == estate["sourceBundle"], "wrong sourceBundle")
    require(plan["requestedBundle"] == estate["requestedBundle"], "wrong requestedBundle")
    require(plan["selectedBundle"] == bundle["version"], "wrong migrationPlan.selectedBundle")

    inventory = {item["id"]: item for item in estate["components"]}
    steps = plan["steps"]
    require([item["order"] for item in steps] == list(range(1, len(steps) + 1)), "step orders must be consecutive")
    by_component = {item["componentId"]: item for item in steps}
    require(len(by_component) == len(steps), "each component must appear in exactly one step")
    require(set(by_component) == set(bundle["bom"]), "steps must cover every selected-bundle component exactly once")

    constraints = [item for item in snapshot["orderingConstraints"] if item["bundle"] == bundle["version"]]
    position = {item["componentId"]: item["order"] for item in steps}
    for constraint in constraints:
        require(
            position[constraint["before"]] < position[constraint["after"]],
            f"ordering constraint {constraint['gateId']} is not satisfied",
        )

    used_gate_ids: set[str] = set()
    for component_id, target in bundle["bom"].items():
        source = inventory[component_id]["version"]
        step = by_component[component_id]
        edge = matching_path(snapshot, component_id, source, target)
        require(edge is not None and edge["supported"] is True, f"unsupported transition for {component_id}")
        required_gates = {edge["gateId"]}
        required_gates.update(item["gateId"] for item in constraints if item["after"] == component_id)
        expected_action = "INSTALL" if inventory[component_id]["status"] == "absent" else "UPGRADE"
        require(step["componentName"] == inventory[component_id]["name"], f"wrong name for {component_id}")
        require(step["currentVersion"] == source, f"wrong currentVersion for {component_id}")
        require(step["targetVersion"] == target, f"wrong targetVersion for {component_id}")
        require(step["action"] == expected_action, f"wrong action for {component_id}")
        require(set(step["gates"]) == required_gates, f"wrong gates for {component_id}")
        used_gate_ids.update(required_gates)

    catalog = {item["id"]: item for item in snapshot["gateCatalog"]}
    gate_objects = {item["id"]: item for item in plan["gates"]}
    require(len(gate_objects) == len(plan["gates"]), "migrationPlan.gates contains duplicate ids")
    require(set(gate_objects) == used_gate_ids, "migrationPlan.gates must define exactly the gates used by steps")
    for gate_id in used_gate_ids:
        require(gate_id in catalog, f"gate {gate_id} is absent from the pinned catalog")
        require(gate_objects[gate_id] == catalog[gate_id], f"gate definition differs from snapshot: {gate_id}")

    expected_relations = [
        {
            "leftComponent": item["leftComponent"],
            "leftVersion": item["leftVersion"],
            "rightComponent": item["rightComponent"],
            "rightVersion": item["rightVersion"],
        }
        for item in snapshot["interoperability"]
        if item["bundle"] == bundle["version"] and item["supported"] is True
    ]
    require(
        canonical_set(plan["finalInteroperability"]) == canonical_set(expected_relations),
        "finalInteroperability does not match the pinned selected-bundle relations",
    )

    for field, expected in expected_sddc_fields(estate, bundle).items():
        require(architecture.get(field) == expected, f"target SddcSpec field differs from estate/snapshot: {field}")


def verify_stdlib_package(solution_root: Path) -> None:
    package = solution_root / "vcf_architecture"
    require(package.is_dir(), "missing vcf_architecture package")
    require((package / "__init__.py").is_file(), "package must contain __init__.py")
    require((package / "__main__.py").is_file(), "package must contain __main__.py")
    modules = sorted(package.rglob("*.py"))
    require(bool(modules), "package contains no Python modules")
    stdlib = set(sys.stdlib_module_names)
    local_roots = {path.stem for path in modules} | {"vcf_architecture"}
    for module_path in modules:
        try:
            tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            raise VerificationError(f"cannot parse {module_path}: {exc}") from exc
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = [node.module.split(".", 1)[0]]
            else:
                continue
            for root in roots:
                require(root in stdlib or root in local_roots, f"third-party import {root!r} in {module_path}")


def verify_research(solution_root: Path) -> None:
    research_path = solution_root / "research.md"
    try:
        research = research_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise VerificationError(f"cannot read research.md: {exc}") from exc

    require(bool(re.search(r"\b20\d{2}-\d{2}-\d{2}\b", research)), "research.md must record an ISO access date")
    source_lines = [line.strip() for line in research.splitlines() if "https://" in line]
    require(len(source_lines) >= 2, "research.md must document at least two consulted pages")
    urls: list[str] = []
    for line in source_lines:
        matches = re.findall(r"https://[^\s)>]+", line)
        require(len(matches) == 1, "each research source entry must contain one page URL")
        require(bool(re.search(r"\b20\d{2}-\d{2}-\d{2}\b", line)), "each research source must include its access date")
        url = matches[0].rstrip(".,;:")
        hostname = (urlparse(url).hostname or "").lower()
        require(hostname == "broadcom.com" or hostname.endswith(".broadcom.com"), "research sources must be Broadcom-published pages")
        before_url, after_url = line.split(matches[0], 1)
        require(len(re.sub(r"[^A-Za-z0-9]+", "", before_url)) >= 8, "each research source must include its title")
        require(len(re.sub(r"[^A-Za-z0-9]+", "", after_url)) >= 20, "each research source must state the design conclusion it informed")
        urls.append(url)
    require(len(urls) == len(set(urls)), "research.md contains duplicate source URLs")
    require("9.0.2.0" in research and "9.1" in research, "research.md must explain the requested and selected bundle conclusions")


def run_cli(solution_root: Path, estate_path: Path, snapshot_path: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="vcfarch-0143-") as temp_dir:
        output_path = Path(temp_dir) / "architecture.json"
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(solution_root)
        command = [
            sys.executable,
            "-m",
            "vcf_architecture",
            "--estate",
            str(estate_path),
            "--compatibility",
            str(snapshot_path),
            "--output",
            str(output_path),
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=solution_root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise VerificationError(f"package CLI could not run: {exc}") from exc
        require(completed.returncode == 0, f"package CLI failed: {completed.stderr.strip()}")
        require(output_path.is_file(), "package CLI did not create its output")
        return load_json(output_path)


def main() -> int:
    solution_root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd().resolve()
    protected = Path(__file__).resolve().parent
    artifact_path = solution_root / "architecture.json"
    spec_path = protected / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"

    # The installer schema validation is deliberately the first verification stage.
    architecture = load_json(artifact_path)
    openapi = load_json(spec_path)
    sddc_schema = openapi["components"]["schemas"]["SddcSpec"]
    errors = schema_errors(architecture, sddc_schema, openapi)
    if errors:
        raise VerificationError("installer SddcSpec validation failed:\n  " + "\n  ".join(errors[:30]))

    require(digest(spec_path) == EXPECTED_SPEC_SHA256, "pinned installer specification was modified")
    estate_path = protected / "estate.json"
    snapshot_path = protected / "compatibility-snapshot.json"
    plan_schema_path = protected / "migration-plan-schema.json"
    require(digest(estate_path) == EXPECTED_FIXTURE_SHA256, "estate fixture was modified")
    require(digest(snapshot_path) == EXPECTED_SNAPSHOT_SHA256, "compatibility snapshot was modified")
    require(digest(plan_schema_path) == EXPECTED_PLAN_SCHEMA_SHA256, "migration plan schema was modified")

    estate = load_json(estate_path)
    snapshot = load_json(snapshot_path)
    plan_schema = load_json(plan_schema_path)
    errors = schema_errors(architecture, plan_schema, plan_schema)
    if errors:
        raise VerificationError("migration plan schema validation failed:\n  " + "\n  ".join(errors[:30]))

    allowed_root_keys = set(sddc_schema.get("properties", {})) | {
        "architectureKind",
        "inventorySha256",
        "selectedBundle",
        "avoidedBundles",
        "migrationPlan",
    }
    require(set(architecture) <= allowed_root_keys, "architecture.json contains non-architectural root fields")
    verify_plan(architecture, estate, snapshot)
    verify_stdlib_package(solution_root)
    verify_research(solution_root)
    regenerated = run_cli(solution_root, estate_path, snapshot_path)
    regenerated_errors = schema_errors(regenerated, sddc_schema, openapi)
    require(not regenerated_errors, "CLI output is not a valid SddcSpec")
    require(regenerated == architecture, "committed architecture.json differs from deterministic CLI output")
    print("PASS: SddcSpec, migration architecture, compatibility route, gates, and CLI are valid")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
