#!/usr/bin/env python3
"""Protected acceptance oracle for the VCF workload-domain architecture seed."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
ARTIFACT = ROOT / "architecture" / "migration_plan.json"
OPENAPI = ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
MIGRATION_SCHEMA = ROOT / "schemas" / "migration-plan.schema.json"
INVENTORY = ROOT / "fixtures" / "estate_inventory.json"
SNAPSHOT = ROOT / "fixtures" / "compatibility_snapshot.json"
RESEARCH_SOURCES = ROOT / "architecture" / "research_sources.json"


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


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
    raise AssertionError(f"unsupported schema type in protected validator: {expected}")


def resolve_local_ref(document, ref: str):
    if not ref.startswith("#/"):
        raise AssertionError(f"non-local $ref in protected schema: {ref}")
    value = document
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        value = value[part]
    return value


def schema_errors(instance, schema, document, path="$", seen=None):
    """Validate the JSON-Schema keywords used by the vendored SddcSpec and plan."""
    errors = []
    seen = set() if seen is None else seen

    if "$ref" in schema:
        ref = schema["$ref"]
        marker = (id(instance), ref)
        if marker in seen:
            return errors
        seen.add(marker)
        errors.extend(schema_errors(instance, resolve_local_ref(document, ref), document, path, seen))
        seen.remove(marker)
        return errors

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: value {instance!r} is not in enum")

    expected_type = schema.get("type")
    if expected_type is not None:
        choices = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(json_type_matches(instance, choice) for choice in choices):
            errors.append(f"{path}: expected type {expected_type}, got {type(instance).__name__}")
            return errors

    if isinstance(instance, dict):
        for name in schema.get("required", []):
            if name not in instance:
                errors.append(f"{path}: missing required property {name!r}")
        properties = schema.get("properties", {})
        for name, value in instance.items():
            if name in properties:
                errors.extend(schema_errors(value, properties[name], document, f"{path}.{name}", seen))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected property {name!r}")

    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: more than {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            serialized = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(set(serialized)) != len(serialized):
                errors.append(f"{path}: array items are not unique")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, value in enumerate(instance):
                errors.extend(schema_errors(value, item_schema, document, f"{path}[{index}]", seen))

    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength {schema['maxLength']}")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            errors.append(f"{path}: does not match pattern {schema['pattern']!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: less than minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: greater than maximum {schema['maximum']}")

    return errors


def validate_installer_schema_first():
    """This is intentionally the first acceptance check and has no fixture dependency."""
    if not ARTIFACT.is_file():
        raise AssertionError("architecture/migration_plan.json is missing")
    artifact = load_json(ARTIFACT)
    openapi = load_json(OPENAPI)
    if openapi.get("info", {}).get("version") != "9.1.0.0":
        raise AssertionError("vendored installer specification is not version 9.1.0.0")
    root_schema = {"$ref": "#/components/schemas/SddcSpec"}
    errors = schema_errors(artifact, root_schema, openapi)
    if errors:
        raise AssertionError("installer SddcSpec validation failed:\n" + "\n".join(errors[:20]))
    return artifact


def flatten_inventory(inventory):
    flattened = []
    for domain in inventory["domains"]:
        for component in domain["components"]:
            flattened.append((domain, component))
    return flattened


def find_domain(inventory, domain_id):
    matches = [domain for domain in inventory["domains"] if domain["domainId"] == domain_id]
    assert len(matches) == 1, f"inventory must contain domain {domain_id!r} exactly once"
    return matches[0]


def semantic_checks(artifact):
    migration_schema = load_json(MIGRATION_SCHEMA)
    errors = schema_errors(artifact, migration_schema, migration_schema)
    assert not errors, "migration schema validation failed:\n" + "\n".join(errors[:20])

    inventory = load_json(INVENTORY)
    snapshot = load_json(SNAPSHOT)
    envelope = inventory["installerEnvelope"]
    for field in ("sddcId", "version", "vcenterSpec", "networkSpecs", "dnsSpec"):
        assert artifact[field] == envelope[field], f"top-level SddcSpec field {field} drifted from inventory"

    assert artifact["estateId"] == inventory["estateId"]
    assert artifact["managementDomainId"] == inventory["managementDomainId"]
    assert artifact["workloadDomainId"] == inventory["targetWorkloadDomainId"]
    assert artifact["targetVcfVersion"] == inventory["targetVcfVersion"] == snapshot["targetVcfVersion"]
    assert artifact["version"] == artifact["targetVcfVersion"]
    assert artifact["inventorySha256"] == hashlib.sha256(INVENTORY.read_bytes()).hexdigest()
    assert artifact["managementDomainMode"] == "preserve"

    paths = [
        path
        for path in snapshot["domainUpgradePaths"]
        if path["from"] == find_domain(inventory, artifact["workloadDomainId"])["vcfVersion"]
        and path["to"] == artifact["targetVcfVersion"]
        and path["supported"] is True
    ]
    assert len(paths) == 1, "pinned snapshot does not authorize exactly one workload-domain path"
    selected_path = paths[0]
    assert artifact["upgradePath"] == selected_path["hops"], "artifact inserted or omitted an upgrade hop"

    assert artifact["gates"] == snapshot["gateDefinitions"], "gate catalog must come from pinned authority"
    gate_ids = [gate["id"] for gate in artifact["gates"]]
    assert len(gate_ids) == len(set(gate_ids)), "gate IDs must be unique"
    known_gates = set(gate_ids)

    flattened = flatten_inventory(inventory)
    inventory_by_id = {component["id"]: (domain, component) for domain, component in flattened}
    assert len(inventory_by_id) == len(flattened), "fixture contains duplicate component IDs"
    plans = artifact["componentPlans"]
    plan_by_id = {plan["componentId"]: plan for plan in plans}
    assert len(plan_by_id) == len(plans), "component plan IDs must be unique"
    assert set(plan_by_id) == set(inventory_by_id), "plan must name every inventory component exactly once"
    assert [plan["order"] for plan in plans] == list(range(1, len(plans) + 1)), "orders must be contiguous"

    management = find_domain(inventory, inventory["managementDomainId"])
    workload = find_domain(inventory, inventory["targetWorkloadDomainId"])
    assert management["changeAllowed"] is False
    assert workload["changeAllowed"] is True and workload["onboardingState"] == "ADDED"

    management_ids = [component["id"] for component in management["components"]]
    for component_id in management_ids:
        component = inventory_by_id[component_id][1]
        plan = plan_by_id[component_id]
        assert plan["domainId"] == management["domainId"]
        assert plan["componentType"] == component["type"]
        assert plan["currentVersion"] == component["version"]
        assert plan["targetVersion"] == component["version"], f"management component {component_id} changed"
        assert plan["action"] == "preserve", f"management component {component_id} is not preserved"
        assert plan["blockedBy"] == []
        assert plan["gateIds"] == ["management-domain-frozen"]

    workload_ids = [component["id"] for component in workload["components"]]
    transitions = {
        (item["componentType"], item["from"]): item
        for item in snapshot["componentTransitions"]
        if item["supported"] is True
    }
    unsupported = {
        (item["componentType"], item["from"], item["to"])
        for item in snapshot["unsupportedTransitions"]
    }
    for component_id in workload_ids:
        component = inventory_by_id[component_id][1]
        plan = plan_by_id[component_id]
        transition = transitions.get((component["type"], component["version"]))
        assert transition is not None, f"no supported pinned transition for {component_id}"
        assert plan["domainId"] == workload["domainId"]
        assert plan["componentType"] == component["type"]
        assert plan["currentVersion"] == component["version"]
        assert plan["targetVersion"] == transition["to"]
        assert (component["type"], component["version"], plan["targetVersion"]) not in unsupported
        assert plan["action"] == "upgrade"
        assert plan["gateIds"] == transition["requiredGateIds"]
        assert set(plan["gateIds"]) <= known_gates

    workload_plans = [plan for plan in plans if plan["domainId"] == workload["domainId"]]
    rank = {component_type: index for index, component_type in enumerate(selected_path["componentOrder"])}
    assert [rank[plan["componentType"]] for plan in workload_plans] == sorted(
        rank[plan["componentType"]] for plan in workload_plans
    ), "workload components violate the pinned component sequence"

    nsx_ids = [item["id"] for item in workload["components"] if item["type"] == "NSX_T_MANAGER"]
    vc_ids = [item["id"] for item in workload["components"] if item["type"] == "VCENTER"]
    esx_ids = [item["id"] for item in workload["components"] if item["type"] == "ESX_HOST"]
    assert len(nsx_ids) == len(vc_ids) == 1 and esx_ids
    assert plan_by_id[nsx_ids[0]]["blockedBy"] == []
    assert plan_by_id[vc_ids[0]]["blockedBy"] == nsx_ids
    ordered_esx_plans = [plan for plan in workload_plans if plan["componentType"] == "ESX_HOST"]
    assert {plan["componentId"] for plan in ordered_esx_plans} == set(esx_ids)
    previous = vc_ids[0]
    for esx_plan in ordered_esx_plans:
        assert esx_plan["blockedBy"] == [previous], "ESX rolling chain is incomplete"
        previous = esx_plan["componentId"]

    position = {plan["componentId"]: plan["order"] for plan in plans}
    for plan in plans:
        for blocker in plan["blockedBy"]:
            assert blocker in position, f"unknown blocker {blocker!r}"
            assert position[blocker] < plan["order"], f"component {plan['componentId']} precedes blocker {blocker}"


def research_manifest_checks():
    assert RESEARCH_SOURCES.is_file(), "architecture/research_sources.json is missing"
    manifest = load_json(RESEARCH_SOURCES)
    assert set(manifest) == {"consulted"}, "research source manifest must contain only 'consulted'"
    assert isinstance(manifest["consulted"], list) and manifest["consulted"], "no consulted sources recorded"
    for index, source in enumerate(manifest["consulted"]):
        label = f"consulted[{index}]"
        assert isinstance(source, dict), f"{label} must be an object"
        assert set(source) == {"title", "url", "accessed", "usedFor"}, f"{label} fields do not match the prompt"
        assert isinstance(source["title"], str) and source["title"].strip(), f"{label}.title is empty"
        assert isinstance(source["url"], str), f"{label}.url must be a string"
        parsed = urlparse(source["url"])
        assert parsed.scheme in {"http", "https"} and parsed.netloc, f"{label}.url is not a public HTTP(S) URL"
        hostname = (parsed.hostname or "").lower()
        assert hostname not in {"localhost", "127.0.0.1", "::1"} and not hostname.endswith(".invalid"), (
            f"{label}.url is a fixture or local URL"
        )
        assert isinstance(source["accessed"], str) and re.fullmatch(
            r"\d{4}-\d{2}-\d{2}", source["accessed"]
        ), f"{label}.accessed is not YYYY-MM-DD"
        assert isinstance(source["usedFor"], list) and source["usedFor"], f"{label}.usedFor is empty"
        assert all(isinstance(item, str) and item.strip() for item in source["usedFor"]), (
            f"{label}.usedFor contains an empty purpose"
        )


def run_cli_checks(artifact):
    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temp_name:
        temp = Path(temp_name)
        generated = temp / "generated.json"
        command = [
            sys.executable,
            "-m",
            "vcfarch",
            "--inventory",
            str(INVENTORY),
            "--compatibility",
            str(SNAPSHOT),
            "--output",
            str(generated),
        ]
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=30)
        assert completed.returncode == 0, f"generator failed: {completed.stderr}"
        assert generated.is_file(), "generator did not create requested output"
        assert load_json(generated) == artifact, "committed artifact is not the deterministic package output"

        variant_inventory = copy.deepcopy(load_json(INVENTORY))
        variant_inventory["estateId"] = "chi-private-cloud-variant"
        variant_workload = find_domain(variant_inventory, variant_inventory["targetWorkloadDomainId"])
        for component in variant_workload["components"]:
            component["id"] += "-variant"
        variant_workload["components"] = list(reversed(variant_workload["components"]))
        variant_path = temp / "variant-inventory.json"
        variant_path.write_text(json.dumps(variant_inventory), encoding="utf-8")
        variant_output = temp / "variant-output.json"
        variant_command = command.copy()
        variant_command[variant_command.index(str(INVENTORY))] = str(variant_path)
        variant_command[variant_command.index(str(generated))] = str(variant_output)
        variant_run = subprocess.run(variant_command, cwd=ROOT, text=True, capture_output=True, timeout=30)
        assert variant_run.returncode == 0, f"generator failed for an equivalent inventory: {variant_run.stderr}"
        variant_plan = load_json(variant_output)
        assert variant_plan["estateId"] == variant_inventory["estateId"], "generator hard-coded the estate ID"
        assert variant_plan["inventorySha256"] == hashlib.sha256(variant_path.read_bytes()).hexdigest()
        variant_components = {component["id"]: component for component in variant_workload["components"]}
        variant_plans = [
            plan for plan in variant_plan["componentPlans"] if plan["domainId"] == variant_workload["domainId"]
        ]
        assert {plan["componentId"] for plan in variant_plans} == set(variant_components), (
            "generator hard-coded workload component IDs"
        )
        variant_ranks = {
            component_type: index
            for index, component_type in enumerate(load_json(SNAPSHOT)["domainUpgradePaths"][0]["componentOrder"])
        }
        assert [variant_ranks[plan["componentType"]] for plan in variant_plans] == sorted(
            variant_ranks[plan["componentType"]] for plan in variant_plans
        ), "equivalent inventory violated component order"
        variant_vcenter_id = next(
            component["id"] for component in variant_workload["components"] if component["type"] == "VCENTER"
        )
        variant_esx_plans = [plan for plan in variant_plans if plan["componentType"] == "ESX_HOST"]
        expected_variant_esx_ids = {
            component["id"] for component in variant_workload["components"] if component["type"] == "ESX_HOST"
        }
        assert {plan["componentId"] for plan in variant_esx_plans} == expected_variant_esx_ids
        previous = variant_vcenter_id
        for esx_plan in variant_esx_plans:
            assert esx_plan["blockedBy"] == [previous], "variant ESX rolling chain is incomplete"
            previous = esx_plan["componentId"]

        rejection_index = 0

        def assert_rejected(candidate, reason):
            nonlocal rejection_index
            rejection_index += 1
            candidate_path = temp / f"rejected-inventory-{rejection_index}.json"
            candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
            rejected_output = temp / f"must-not-exist-{rejection_index}.json"
            bad_command = command.copy()
            bad_command[bad_command.index(str(INVENTORY))] = str(candidate_path)
            bad_command[bad_command.index(str(generated))] = str(rejected_output)
            rejected_run = subprocess.run(bad_command, cwd=ROOT, text=True, capture_output=True, timeout=30)
            assert rejected_run.returncode != 0, f"{reason} was accepted"
            assert not rejected_output.exists(), f"{reason} left an output artifact"

        snapshot = load_json(SNAPSHOT)
        for unsupported in snapshot["unsupportedTransitions"]:
            bad_inventory = copy.deepcopy(load_json(INVENTORY))
            workload = find_domain(bad_inventory, bad_inventory["targetWorkloadDomainId"])
            component = next(
                item for item in workload["components"] if item["type"] == unsupported["componentType"]
            )
            component["version"] = unsupported["from"]
            assert_rejected(bad_inventory, f"{unsupported['reason']} {unsupported['componentType']} transition")

        unknown_component_transition = copy.deepcopy(load_json(INVENTORY))
        unknown_workload = find_domain(
            unknown_component_transition, unknown_component_transition["targetWorkloadDomainId"]
        )
        unknown_workload["components"][0]["version"] = "0.0.0-unsupported"
        assert_rejected(unknown_component_transition, "unsupported component transition")

        unknown_domain_transition = copy.deepcopy(load_json(INVENTORY))
        unknown_domain = find_domain(unknown_domain_transition, unknown_domain_transition["targetWorkloadDomainId"])
        unknown_domain["vcfVersion"] = "5.2.1.2"
        assert_rejected(unknown_domain_transition, "unsupported workload-domain transition")

        wrong_fleet = copy.deepcopy(load_json(INVENTORY))
        wrong_fleet["fleetVersion"] = "9.0.2.0"
        assert_rejected(wrong_fleet, "fleet below the target")


def stdlib_only_check():
    package = ROOT / "vcfarch"
    assert package.is_dir(), "vcfarch package is missing"
    python_files = sorted(package.rglob("*.py"))
    assert python_files, "vcfarch package has no Python modules"
    stdlib = set(sys.stdlib_module_names)
    for path in python_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".", 1)[0]]
            else:
                continue
            for name in names:
                assert name in stdlib or name == "vcfarch", f"non-stdlib import {name!r} in {path.relative_to(ROOT)}"


def main():
    checks = 0
    try:
        artifact = validate_installer_schema_first()
        checks += 1
        semantic_checks(artifact)
        checks += 1
        research_manifest_checks()
        checks += 1
        run_cli_checks(artifact)
        checks += 1
        stdlib_only_check()
        checks += 1
    except (AssertionError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"FAIL after {checks} completed check group(s): {exc}")
        return 1
    print(f"PASS {checks} protected VCF architecture check groups")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
