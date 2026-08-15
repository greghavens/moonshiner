#!/usr/bin/env python3
"""Offline acceptance verifier for the VCF architecture seed."""

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


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "architecture.json"
OPENAPI = ROOT / "specifications/vcf-installer/vcf-installer-openapi.json"
INVENTORY = ROOT / "fixtures/estate.json"
SNAPSHOT = ROOT / "compatibility_snapshot.json"
PLAN_SCHEMA = ROOT / "schemas/migration-plan.schema.json"
PLANNER = ROOT / "vcf_architect/planner.py"


class VerificationError(AssertionError):
    pass


def fail(message: str) -> None:
    raise VerificationError(message)


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        fail(f"missing required file: {path.relative_to(ROOT)}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {path.relative_to(ROOT)}: {exc}")


def pointer(document: Any, ref: str) -> Any:
    if not ref.startswith("#/"):
        fail(f"unsupported non-local schema reference: {ref}")
    node = document
    for raw in ref[2:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        try:
            node = node[token]
        except (KeyError, TypeError):
            fail(f"unresolvable schema reference: {ref}")
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
    return True


def validate_schema(value: Any, schema: Any, root: Any, path: str = "$") -> None:
    """Validate the JSON-Schema/OpenAPI subset used by the pinned contracts."""
    if isinstance(schema, bool):
        if not schema:
            fail(f"{path}: value rejected by schema")
        return
    if not isinstance(schema, dict):
        fail(f"{path}: malformed schema node")
    if "$ref" in schema:
        validate_schema(value, pointer(root, schema["$ref"]), root, path)
        return
    if value is None and schema.get("nullable"):
        return
    for subschema in schema.get("allOf", []):
        validate_schema(value, subschema, root, path)
    if "anyOf" in schema:
        successes = 0
        for subschema in schema["anyOf"]:
            try:
                validate_schema(value, subschema, root, path)
                successes += 1
            except VerificationError:
                pass
        if not successes:
            fail(f"{path}: does not match any allowed schema")
    if "oneOf" in schema:
        successes = 0
        for subschema in schema["oneOf"]:
            try:
                validate_schema(value, subschema, root, path)
                successes += 1
            except VerificationError:
                pass
        if successes != 1:
            fail(f"{path}: must match exactly one allowed schema")
    if "not" in schema:
        try:
            validate_schema(value, schema["not"], root, path)
        except VerificationError:
            pass
        else:
            fail(f"{path}: matches a forbidden schema")
    if "const" in schema and value != schema["const"]:
        fail(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        fail(f"{path}: {value!r} is not in the allowed enum")

    expected = schema.get("type")
    if expected is not None:
        choices = expected if isinstance(expected, list) else [expected]
        if not any(json_type_matches(value, choice) for choice in choices):
            fail(f"{path}: expected type {expected!r}, got {type(value).__name__}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [name for name in required if name not in value]
        if missing:
            fail(f"{path}: missing required properties {missing}")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for name, item in value.items():
            child_path = f"{path}.{name}"
            if name in properties:
                validate_schema(item, properties[name], root, child_path)
            elif additional is False:
                fail(f"{child_path}: additional property is not allowed")
            elif isinstance(additional, dict):
                validate_schema(item, additional, root, child_path)
        if len(value) < schema.get("minProperties", 0):
            fail(f"{path}: too few properties")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            fail(f"{path}: too many properties")

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            fail(f"{path}: too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            fail(f"{path}: too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(encoded) != len(set(encoded)):
                fail(f"{path}: items must be unique")
        if "items" in schema:
            for index, item in enumerate(value):
                validate_schema(item, schema["items"], root, f"{path}[{index}]")

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            fail(f"{path}: string is shorter than minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            fail(f"{path}: string is longer than maxLength")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], value)
            except re.error as exc:
                fail(f"{path}: invalid schema pattern: {exc}")
            if matched is None:
                fail(f"{path}: string does not match {schema['pattern']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            fail(f"{path}: number is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            fail(f"{path}: number is above maximum")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            fail(f"{path}: number is not above exclusiveMinimum")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            fail(f"{path}: number is not below exclusiveMaximum")


def validate_sddc_first(artifact: Any, openapi: dict[str, Any], label: str) -> None:
    """The installer's own SddcSpec validation is deliberately the first gate."""
    try:
        sddc = artifact["sddc_spec"]
    except (KeyError, TypeError):
        fail(f"{label}: missing top-level sddc_spec for installer-schema validation")
    try:
        schema = openapi["components"]["schemas"]["SddcSpec"]
    except (KeyError, TypeError):
        fail("pinned installer specification does not define components.schemas.SddcSpec")
    try:
        validate_schema(sddc, schema, openapi, "$.sddc_spec")
    except VerificationError as exc:
        fail(f"{label}: installer SddcSpec validation failed first: {exc}")


def validate_standard_library_only() -> None:
    """Reject third-party imports in the only candidate-editable Python module."""
    try:
        source = PLANNER.read_text(encoding="utf-8")
    except FileNotFoundError:
        fail("missing required file: vcf_architect/planner.py")
    try:
        tree = ast.parse(source, filename=str(PLANNER.relative_to(ROOT)))
    except SyntaxError as exc:
        fail(f"vcf_architect/planner.py is not valid Python: {exc}")

    definitions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "build_architecture"
    ]
    if len(definitions) != 1 or isinstance(definitions[0], ast.AsyncFunctionDef):
        fail("vcf_architect.planner must define one synchronous build_architecture function")
    function = definitions[0]
    positional = [*function.args.posonlyargs, *function.args.args]
    if (
        [argument.arg for argument in positional] != ["inventory", "compatibility"]
        or function.args.posonlyargs
        or function.args.vararg is not None
        or function.args.kwonlyargs
        or function.args.kwarg is not None
        or function.args.defaults
    ):
        fail("build_architecture must have exactly (inventory, compatibility) parameters")

    def is_dict_annotation(annotation: ast.expr | None) -> bool:
        if isinstance(annotation, ast.Name):
            return annotation.id == "dict"
        return (
            isinstance(annotation, ast.Subscript)
            and isinstance(annotation.value, ast.Name)
            and annotation.value.id == "dict"
        )

    if not all(is_dict_annotation(argument.annotation) for argument in positional):
        fail("build_architecture inventory and compatibility parameters must be typed as dict")
    if not is_dict_annotation(function.returns):
        fail("build_architecture return type must be dict")

    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules = [node.module]
        for module in modules:
            root_module = module.partition(".")[0]
            if root_module not in sys.stdlib_module_names and root_module != "vcf_architect":
                fail(
                    "vcf_architect/planner.py must be standard-library-only; "
                    f"found import {module!r}"
                )


def validate_research(artifact: dict[str, Any], label: str) -> None:
    """Validate deterministic, structured records of genuine official research."""
    research = artifact.get("research")
    if not isinstance(research, list) or not research:
        fail(f"{label}: research must be a nonempty array")
    required = {"title", "publisher", "url", "accessed_at", "conclusion"}
    seen_urls: set[str] = set()
    for index, source in enumerate(research):
        path = f"{label}: research[{index}]"
        if not isinstance(source, dict):
            fail(f"{path} must be an object")
        missing = required - set(source)
        if missing:
            fail(f"{path} is missing fields {sorted(missing)}")
        for field in required:
            if not isinstance(source[field], str) or not source[field].strip():
                fail(f"{path}.{field} must be a nonempty string")
        publisher = source["publisher"].lower()
        if "broadcom" not in publisher and "vmware" not in publisher:
            fail(f"{path}.publisher must identify Broadcom or VMware")
        try:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", source["accessed_at"]) is None:
                raise ValueError
            date.fromisoformat(source["accessed_at"])
        except ValueError:
            fail(f"{path}.accessed_at must use ISO YYYY-MM-DD format")
        parsed = urlsplit(source["url"])
        hostname = (parsed.hostname or "").lower()
        official = (
            hostname == "broadcom.com"
            or hostname.endswith(".broadcom.com")
            or hostname == "vmware.com"
            or hostname.endswith(".vmware.com")
            or hostname == "vmware.github.io"
        )
        if parsed.scheme != "https" or not official:
            fail(f"{path}.url must be an HTTPS Broadcom/VMware-published source")
        if source["url"] in seen_urls:
            fail(f"{path}.url duplicates an earlier research source")
        seen_urls.add(source["url"])


def validate_password_placeholders(sddc: dict[str, Any], label: str) -> None:
    """Ensure any supplied password values are visibly documentation placeholders."""
    pending: list[Any] = [sddc]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            for key, item in value.items():
                if "password" in key.lower() and isinstance(item, str):
                    if re.fullmatch(r"<[^<>]+>", item) is None:
                        fail(f"{label}: {key} must be an angle-bracketed placeholder")
                pending.append(item)
        elif isinstance(value, list):
            pending.extend(value)

    vcenter_password = sddc["vcenterSpec"]["rootVcenterPassword"]
    if not (
        15 <= len(vcenter_password) <= 20
        and vcenter_password.isascii()
        and not any(character.isspace() for character in vcenter_password)
        and any(character.isupper() for character in vcenter_password)
        and any(character.islower() for character in vcenter_password)
        and any(character.isdigit() for character in vcenter_password)
        and any(not character.isalnum() for character in vcenter_password)
    ):
        fail(f"{label}: rootVcenterPassword does not satisfy new-deployment rules")


def exact_ids(items: list[dict[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        identifier = item[key]
        if identifier in result:
            fail(f"{label}: duplicate {key} {identifier!r}")
        result[identifier] = item
    return result


def semantic_checks(
    artifact: dict[str, Any],
    inventory: dict[str, Any],
    snapshot: dict[str, Any],
) -> None:
    sddc = artifact["sddc_spec"]
    plan = artifact["migration_plan"]
    topo = snapshot["topology"]
    targets = snapshot["component_targets"]

    if artifact.get("schema_version") != 1:
        fail("artifact schema_version must be 1")
    if sddc.get("workflowType") != "VCF":
        fail("greenfield SddcSpec workflowType must be VCF")
    if sddc.get("version") != snapshot["target_release"]:
        fail("SddcSpec version does not match the pinned target release")
    if sddc.get("vcfInstanceName") != inventory["site"]["id"]:
        fail("SddcSpec vcfInstanceName must identify the inventory site")

    hosts = sddc.get("hostSpecs", [])
    minimum = topo["minimum_management_vsan_hosts"]
    if len(hosts) != minimum:
        fail(f"SddcSpec must contain exactly the pinned minimum of {minimum} hosts")
    expected_hostnames = {host["short_hostname"] for host in inventory["hosts"]}
    actual_hostnames = {host.get("hostname") for host in hosts}
    if actual_hostnames != expected_hostnames or len(actual_hostnames) != len(hosts):
        fail("SddcSpec hostSpecs must contain each fixture host exactly once")

    if sddc["vcenterSpec"].get("useExistingDeployment") is not False:
        fail("greenfield vCenter must be a new deployment")
    target_vcenter = targets["vcenter-chi01"]["target_version"]
    if sddc["vcenterSpec"].get("version") != target_vcenter:
        fail("greenfield vCenter target version is not pinned")
    if sddc.get("sddcManagerSpec", {}).get("useExistingDeployment") is not False:
        fail("greenfield SDDC Manager must be a new deployment")
    if sddc["sddcManagerSpec"].get("version") != snapshot["platform_targets"]["sddc_manager"]:
        fail("SDDC Manager target version is not pinned")
    if sddc.get("vcfOperationsSpec", {}).get("useExistingDeployment") is not False:
        fail("greenfield VCF Operations must be a new deployment")
    if sddc["vcfOperationsSpec"].get("version") != snapshot["platform_targets"]["vcf_operations"]:
        fail("VCF Operations target version is not pinned")
    if sddc.get("licenseServerSpec", {}).get("version") != snapshot["platform_targets"]["license_server"]:
        fail("License Server target version is not pinned")
    if sddc.get("licenseServerSpec", {}).get("useExistingDeployment") is not False:
        fail("greenfield License Server must be a new deployment")
    if sddc.get("nsxtSpec", {}).get("useExistingDeployment") is not False:
        fail("greenfield NSX must be a new deployment")
    if sddc["nsxtSpec"].get("version") != targets["nsx-chi01"]["target_version"]:
        fail("greenfield NSX target version is not pinned")

    dvs_specs = sddc.get("dvsSpecs", [])
    if len(dvs_specs) != topo["distributed_switch_count"]:
        fail("consolidated design must use the pinned distributed-switch count")
    dvs = dvs_specs[0]
    if dvs.get("mtu") != topo["mtu"]:
        fail("distributed-switch MTU does not match the pinned design")
    dvs_networks = dvs.get("networks", [])
    if (
        set(dvs_networks) != set(topo["required_network_types"])
        or len(dvs_networks) != len(topo["required_network_types"])
    ):
        fail("the single distributed switch must carry every required network type")
    network_specs = sddc.get("networkSpecs", [])
    by_network = {entry.get("networkType"): entry for entry in network_specs}
    if (
        set(by_network) != set(topo["required_network_types"])
        or len(network_specs) != len(topo["required_network_types"])
    ):
        fail("networkSpecs must define exactly the required consolidated networks")
    if any(entry.get("mtu") != topo["mtu"] for entry in by_network.values()):
        fail("every consolidated network must use the pinned MTU")
    vsan = sddc.get("datastoreSpec", {}).get("vsanSpec", {})
    if vsan.get("failuresToTolerate") != topo["vsan_failures_to_tolerate"]:
        fail("the greenfield management datastore must be vSAN at the pinned FTT")

    if plan["estate_id"] != inventory["estate_id"]:
        fail("migration plan estate_id does not match the fixture")
    if plan["target_release"] != snapshot["target_release"]:
        fail("migration plan target_release does not match the snapshot")
    expected_plan_topology = {
        "kind": topo["kind"],
        "site_count": inventory["site"]["site_count_in_scope"],
        "management_host_count": minimum,
        "stretched": inventory["site"]["stretched_cluster"],
    }
    if plan["topology"] != expected_plan_topology:
        fail("migration plan topology does not match the single-site minimum design")

    inventory_components = {item["id"]: item for item in inventory["components"]}
    planned_components = exact_ids(plan["components"], "component_id", "components")
    if set(planned_components) != set(inventory_components):
        fail("migration plan must preserve every inventory component exactly once")
    for component_id, source in inventory_components.items():
        planned = planned_components[component_id]
        target = targets.get(component_id)
        if target is None:
            fail(f"snapshot has no target for {component_id}")
        expected = {
            "component_id": component_id,
            "component_name": source["name"],
            "current_version": source["version"],
            "target_product": target["target_product"],
            "target_version": target["target_version"],
        }
        for key, value in expected.items():
            if planned[key] != value:
                fail(f"component {component_id}: {key} is not inventory/snapshot-derived")
        if set(target["required_gate_ids"]) != set(planned["gate_ids"]):
            fail(f"component {component_id}: compatibility gates are not pinned")

    plan_gates = exact_ids(plan["gates"], "gate_id", "gates")
    required_gate_ids = {gate["id"] for gate in snapshot["gates"]}
    if required_gate_ids != set(plan_gates):
        fail("migration plan gates do not exactly match the pinned gates")
    for gate in snapshot["gates"]:
        if plan_gates[gate["id"]]["condition"] != gate["fact"]:
            fail(f"gate {gate['id']}: condition is not the pinned compatibility fact")
    for component in plan["components"]:
        unknown = set(component["gate_ids"]) - set(plan_gates)
        if unknown:
            fail(f"component {component['component_id']} references unknown gates {sorted(unknown)}")

    steps = plan["steps"]
    if [step["sequence"] for step in steps] != list(range(1, len(steps) + 1)):
        fail("migration step sequence values must be contiguous and ordered from 1")
    steps_by_id = exact_ids(steps, "step_id", "steps")
    steps_by_rule = exact_ids(steps, "rule_id", "steps")
    required_rule_ids = {
        transition["rule_id"] for transition in snapshot["required_transitions"]
    }
    if set(steps_by_rule) != required_rule_ids:
        fail("migration steps do not exactly match the pinned transitions")
    known_components = set(inventory_components)
    seen_components: set[str] = set()
    for step in steps:
        if not set(step["component_ids"]).issubset(known_components):
            fail(f"step {step['step_id']} references a component outside the fixture")
        seen_components.update(step["component_ids"])
        if not set(step["gate_ids"]).issubset(plan_gates):
            fail(f"step {step['step_id']} references an undefined gate")
        if not set(step["depends_on"]).issubset(steps_by_id):
            fail(f"step {step['step_id']} has an undefined dependency")
        prior_ids = {item["step_id"] for item in steps if item["sequence"] < step["sequence"]}
        if not set(step["depends_on"]).issubset(prior_ids):
            fail(f"step {step['step_id']} depends on a non-prior step")
    if seen_components != known_components:
        fail("every inventory component must participate in at least one ordered step")

    for transition in snapshot["required_transitions"]:
        rule_id = transition["rule_id"]
        step = steps_by_rule.get(rule_id)
        if step is None:
            fail(f"missing required transition rule {rule_id}")
        if step["kind"] != transition["kind"]:
            fail(f"transition {rule_id} has the wrong kind")
        if set(step["component_ids"]) != set(transition["affected_component_ids"]):
            fail(f"transition {rule_id} has the wrong affected components")
        if set(transition["required_gate_ids"]) != set(step["gate_ids"]):
            fail(f"transition {rule_id} does not use exactly the pinned gates")
        expected_from = {change["component_id"]: change["from_version"] for change in transition["changes"]}
        expected_target = {change["component_id"]: change["target_version"] for change in transition["changes"]}
        if step["from_versions"] != expected_from or step["target_versions"] != expected_target:
            fail(f"transition {rule_id} does not use the pinned version path")
        for predecessor_rule in transition["after_rules"]:
            predecessor = steps_by_rule.get(predecessor_rule)
            if predecessor is None:
                fail(f"transition {rule_id} references missing predecessor {predecessor_rule}")
            if predecessor["sequence"] >= step["sequence"]:
                fail(f"transition {rule_id} is not ordered after {predecessor_rule}")
            if predecessor["step_id"] not in step["depends_on"]:
                fail(f"transition {rule_id} does not explicitly depend on {predecessor_rule}")
        expected_dependencies = {
            steps_by_rule[predecessor_rule]["step_id"]
            for predecessor_rule in transition["after_rules"]
        }
        if set(step["depends_on"]) != expected_dependencies:
            fail(f"transition {rule_id} dependencies are not exactly pinned")


def validate_after_installer(
    artifact: Any,
    plan_schema: dict[str, Any],
    inventory: dict[str, Any],
    snapshot: dict[str, Any],
    label: str,
) -> None:
    if not isinstance(artifact, dict):
        fail(f"{label}: artifact must be a JSON object")
    try:
        plan = artifact["migration_plan"]
    except KeyError:
        fail(f"{label}: missing migration_plan")
    try:
        validate_schema(plan, plan_schema, plan_schema, "$.migration_plan")
    except VerificationError as exc:
        fail(f"{label}: migration plan schema validation failed: {exc}")
    validate_research(artifact, label)
    validate_password_placeholders(artifact["sddc_spec"], label)
    semantic_checks(artifact, inventory, snapshot)


def main() -> int:
    # Load only the candidate and installer document, then run SddcSpec validation
    # before loading or checking any fixture-specific grading authority.
    artifact = load_json(ARTIFACT)
    openapi = load_json(OPENAPI)
    if not isinstance(openapi, dict):
        fail("pinned installer specification must be a JSON object")
    validate_sddc_first(artifact, openapi, "checked-in artifact")
    validate_standard_library_only()

    inventory = load_json(INVENTORY)
    snapshot = load_json(SNAPSHOT)
    plan_schema = load_json(PLAN_SCHEMA)
    if not all(isinstance(item, dict) for item in (inventory, snapshot, plan_schema)):
        fail("protected fixture, snapshot, and migration schema must be JSON objects")
    validate_after_installer(artifact, plan_schema, inventory, snapshot, "checked-in artifact")

    with tempfile.TemporaryDirectory(prefix="vcf-architecture-") as temp_dir:
        generated_path = Path(temp_dir) / "architecture.json"
        command = [
            sys.executable,
            "-B",
            "-S",
            "-m",
            "vcf_architect",
            "--inventory",
            str(INVENTORY),
            "--compatibility",
            str(SNAPSHOT),
            "--output",
            str(generated_path),
        ]
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
        if completed.returncode != 0:
            fail(
                "package CLI failed to regenerate architecture.json: "
                + (completed.stderr.strip() or completed.stdout.strip())
            )
        generated = load_json(generated_path)
        validate_sddc_first(generated, openapi, "regenerated artifact")
        validate_after_installer(generated, plan_schema, inventory, snapshot, "regenerated artifact")
        if generated != artifact:
            fail("checked-in architecture.json is not the package's deterministic output")

    print("PASS: installer-valid SddcSpec and pinned brownfield VCF migration architecture")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
