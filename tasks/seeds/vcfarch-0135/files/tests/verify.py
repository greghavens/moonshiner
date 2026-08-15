#!/usr/bin/env python3
"""Offline, deterministic verifier for the emitted VCF architecture."""
from __future__ import annotations

import json
import math
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class ValidationError(AssertionError):
    pass


def fail(message: str) -> None:
    raise ValidationError(message)


def resolve_ref(root: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        fail(f"external schema reference is not allowed: {ref}")
    node: Any = root
    for raw in ref[2:].split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or key not in node:
            fail(f"unresolvable schema reference: {ref}")
        node = node[key]
    if not isinstance(node, dict):
        fail(f"schema reference is not an object: {ref}")
    return node


def type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def validate(instance: Any, schema: dict[str, Any], root: dict[str, Any], path: str = "$") -> None:
    if "$ref" in schema:
        validate(instance, resolve_ref(root, schema["$ref"]), root, path)
        return

    if "allOf" in schema:
        for sub in schema["allOf"]:
            validate(instance, sub, root, path)
    if "anyOf" in schema:
        successes = 0
        for sub in schema["anyOf"]:
            try:
                validate(instance, sub, root, path)
                successes += 1
            except ValidationError:
                pass
        if not successes:
            fail(f"{path}: does not satisfy anyOf")
    if "oneOf" in schema:
        successes = 0
        for sub in schema["oneOf"]:
            try:
                validate(instance, sub, root, path)
                successes += 1
            except ValidationError:
                pass
        if successes != 1:
            fail(f"{path}: satisfies {successes} oneOf branches")
    if "not" in schema:
        try:
            validate(instance, schema["not"], root, path)
        except ValidationError:
            pass
        else:
            fail(f"{path}: matches forbidden schema")

    if instance is None and schema.get("nullable") is True:
        return
    if "const" in schema and instance != schema["const"]:
        fail(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        fail(f"{path}: value is not in enum")

    expected = schema.get("type")
    if isinstance(expected, list):
        if not any(type_matches(instance, item) for item in expected):
            fail(f"{path}: wrong type")
    elif isinstance(expected, str) and not type_matches(instance, expected):
        fail(f"{path}: expected {expected}, got {type(instance).__name__}")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        missing = [key for key in required if key not in instance]
        if missing:
            fail(f"{path}: missing required properties {missing}")
        if len(instance) < schema.get("minProperties", 0):
            fail(f"{path}: too few properties")
        if len(instance) > schema.get("maxProperties", math.inf):
            fail(f"{path}: too many properties")
        properties = schema.get("properties", {})
        for key, value in instance.items():
            child = f"{path}.{key}"
            if key in properties:
                validate(value, properties[key], root, child)
            elif schema.get("additionalProperties") is False:
                fail(f"{child}: additional property is not allowed")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate(value, schema["additionalProperties"], root, child)

    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            fail(f"{path}: too few items")
        if len(instance) > schema.get("maxItems", math.inf):
            fail(f"{path}: too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(encoded) != len(set(encoded)):
                fail(f"{path}: items are not unique")
        if isinstance(schema.get("items"), dict):
            for index, value in enumerate(instance):
                validate(value, schema["items"], root, f"{path}[{index}]")

    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            fail(f"{path}: string is too short")
        if len(instance) > schema.get("maxLength", math.inf):
            fail(f"{path}: string is too long")
        pattern = schema.get("pattern")
        if pattern is not None and re.search(pattern, instance) is None:
            fail(f"{path}: does not match pattern {pattern!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if instance < schema.get("minimum", -math.inf):
            fail(f"{path}: below minimum")
        if instance > schema.get("maximum", math.inf):
            fail(f"{path}: above maximum")
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            fail(f"{path}: below exclusive minimum")
        if "exclusiveMaximum" in schema and instance >= schema["exclusiveMaximum"]:
            fail(f"{path}: above exclusive maximum")


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def main() -> int:
    artifact_path = Path(sys.argv[1])
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))

    # This is deliberately the first artifact check: validate the greenfield
    # payload directly against the installer specification's SddcSpec schema.
    openapi = json.loads(Path("specifications/vcf-installer/vcf-installer-openapi.json").read_text(encoding="utf-8"))
    try:
        sddc_spec = artifact["sddcSpec"]
    except (TypeError, KeyError):
        fail("artifact must contain sddcSpec")
    sddc_schema = openapi["components"]["schemas"]["SddcSpec"]
    validate(sddc_spec, sddc_schema, openapi, "$.sddcSpec")

    inventory = json.loads(Path("fixtures/estate-inventory.json").read_text(encoding="utf-8"))
    snapshot = json.loads(Path("compatibility/compatibility-snapshot.json").read_text(encoding="utf-8"))
    plan_schema = json.loads(Path("contracts/migration-plan-schema.json").read_text(encoding="utf-8"))

    require(isinstance(artifact, dict), "artifact must be an object")
    require("migrationPlan" in artifact, "artifact must contain migrationPlan")
    plan = artifact["migrationPlan"]
    validate(plan, plan_schema, plan_schema, "$.migrationPlan")

    research = artifact.get("researchConsulted")
    require(isinstance(research, list) and research, "artifact must contain a non-empty researchConsulted array")
    research_fields = {"source", "url", "checkedAt", "finding"}
    for index, record in enumerate(research):
        require(isinstance(record, dict), f"researchConsulted[{index}] must be an object")
        require(set(record) == research_fields, f"researchConsulted[{index}] must contain exactly {sorted(research_fields)}")
        for field in research_fields:
            require(isinstance(record[field], str) and record[field].strip(), f"researchConsulted[{index}].{field} must be a non-empty string")
        parsed_url = urlparse(record["url"])
        require(parsed_url.scheme == "https" and bool(parsed_url.hostname), f"researchConsulted[{index}].url must be an https URL")
        require(not parsed_url.hostname.endswith(".invalid"), f"researchConsulted[{index}].url must not use a reserved invalid host")
        require(re.fullmatch(r"\d{4}-\d{2}-\d{2}", record["checkedAt"]) is not None, f"researchConsulted[{index}].checkedAt must use YYYY-MM-DD")
        try:
            date.fromisoformat(record["checkedAt"])
        except ValueError:
            fail(f"researchConsulted[{index}].checkedAt is not a calendar date")

    primary = inventory["greenfieldPrimary"]
    require(sddc_spec.get("sddcId") == primary["sddcId"], "SddcSpec sddcId does not match inventory")
    require(sddc_spec.get("workflowType") == "VCF", "greenfield primary must use VCF workflow")
    require(sddc_spec.get("version") == inventory["targetVcfVersion"], "SddcSpec target version mismatch")
    require(sddc_spec["vcenterSpec"].get("vcenterHostname") == primary["vcenterHostname"], "vCenter hostname mismatch")
    require(sddc_spec.get("sddcManagerSpec", {}).get("hostname") == primary["sddcManagerHostname"], "SDDC Manager hostname mismatch")
    require(sddc_spec["dnsSpec"] == primary["dns"], "DNS design mismatch")
    require(sddc_spec.get("ntpServers") == primary["ntpServers"], "NTP design mismatch")

    expected_hosts = [host["hostname"] for host in primary["hosts"]]
    actual_hosts = [host.get("hostname") for host in sddc_spec.get("hostSpecs", [])]
    require(actual_hosts == expected_hosts, "SddcSpec must contain the four inventory hosts in order")
    expected_networks = {(item["networkType"], item["vlanId"], item["subnet"], item["gateway"], item["mtu"]) for item in primary["networks"]}
    actual_networks = {(item.get("networkType"), item.get("vlanId"), item.get("subnet"), item.get("gateway"), item.get("mtu")) for item in sddc_spec["networkSpecs"]}
    require(actual_networks == expected_networks, "SddcSpec networks do not match inventory")
    require(sddc_spec.get("nsxtSpec", {}).get("vipFqdn") == primary["nsxVipFqdn"], "NSX VIP mismatch")
    require([node.get("hostname") for node in sddc_spec.get("nsxtSpec", {}).get("nsxtManagers", [])] == primary["nsxManagerHostnames"], "NSX managers mismatch")
    ops = sddc_spec.get("vcfOperationsSpec", {})
    require(ops.get("version") == "9.1.0", "VCF Operations target must be 9.1.0")
    require(ops.get("useExistingDeployment") is True, "the upgraded shared Operations deployment must be reused")
    require([node.get("hostname") for node in ops.get("nodes", [])] == [primary["vcfOperationsHostname"]], "VCF Operations node mismatch")
    require(sddc_spec.get("licenseServerSpec") == {"hostname": primary["licenseServerHostname"], "version": "9.1.0", "useExistingDeployment": False}, "9.1 License Server design mismatch")

    topology = artifact.get("topology")
    require(isinstance(topology, dict), "artifact must contain topology object")
    require(topology.get("fleetId") == inventory["fleetId"], "fleet id mismatch")
    require(topology.get("selectedTopologyId") == snapshot["entitlementRule"]["allowedTopologyId"], "wrong selected topology")
    require(topology.get("vcfInstances") == [{"id": primary["vcfInstanceId"], "role": "PRIMARY"}], "selected topology must have one primary VCF instance")
    expected_domains = [{"id": item["id"], "site": item["site"], "vcfInstanceId": primary["vcfInstanceId"]} for item in inventory["workloadDomains"]]
    require(topology.get("workloadDomains") == expected_domains, "both workload domains must join the primary VCF instance")

    existing = sum(item["existingCores"] for item in inventory["workloadDomains"])
    management = sum(item["cores"] for item in primary["hosts"])
    licensed = inventory["entitlement"]["licensedCoreCapacity"]
    capacity = topology.get("capacity", {})
    require(capacity == {"licensedCores": licensed, "existingWorkloadCores": existing, "primaryManagementCores": management, "selectedTotalCores": existing + management, "dualSiteRejectedTotalCores": existing + 2 * management}, "topology core calculation mismatch")
    require(existing + management <= licensed < existing + 2 * management, "fixture must remove only the dual-instance topology")
    require(topology.get("rejectedTopologies") == [{"id": snapshot["entitlementRule"]["rejectedTopologyId"], "gateId": snapshot["entitlementRule"]["gateId"], "requiredCores": existing + 2 * management, "licensedCores": licensed}], "dual-site topology rejection mismatch")

    require(plan["schemaVersion"] == "1.0", "migration schema version mismatch")
    require(plan["targetVcfVersion"] == snapshot["targetVcfVersion"], "migration target mismatch")
    inventory_components = {item["id"]: item for item in inventory["components"]}
    planned_components = {item["id"]: item for item in plan["components"]}
    require(len(planned_components) == len(plan["components"]), "duplicate component ids in migration manifest")
    require(set(planned_components) == set(inventory_components), "migration manifest must name every inventory component exactly once")
    rules = {item["componentType"]: item for item in snapshot["componentRules"]}
    gate_ids = {item["id"] for item in snapshot["gates"]}
    for component_id, current in inventory_components.items():
        planned = planned_components[component_id]
        rule = rules[current["componentType"]]
        require(planned["componentType"] == current["componentType"], f"{component_id}: component type mismatch")
        require(planned["currentProduct"] == current["product"], f"{component_id}: current product mismatch")
        require(planned["currentVersion"] == current["version"], f"{component_id}: current version mismatch")
        require(current["version"] in rule["currentVersions"], f"{component_id}: source version unsupported by snapshot")
        require(planned["targetProduct"] == rule["targetProduct"], f"{component_id}: target product mismatch")
        require(planned["targetVersion"] == rule["targetVersion"], f"{component_id}: target version mismatch")
        require(planned["upgradePath"] == rule["upgradePath"], f"{component_id}: upgrade path mismatch")
        require(set(planned["gateIds"]) == set(rule["requiredGateIds"]), f"{component_id}: gate set mismatch")
        require(set(planned["gateIds"]) <= gate_ids, f"{component_id}: unknown gate")

    steps = plan["steps"]
    require([item["order"] for item in steps] == list(range(1, len(steps) + 1)), "migration step order must be contiguous from 1")
    required_steps = snapshot["requiredStepSequence"]
    require([item["id"] for item in steps] == [item["id"] for item in required_steps], "migration step sequence mismatch")
    for actual, expected in zip(steps, required_steps):
        require(actual["componentIds"] == expected["componentIds"], f"{actual['id']}: component sequence mismatch")
        require(set(actual["gateIds"]) == set(expected["requiredGateIds"]), f"{actual['id']}: gate set mismatch")
        require(set(actual["componentIds"]) <= set(inventory_components), f"{actual['id']}: unknown component")
        require(set(actual["gateIds"]) <= gate_ids, f"{actual['id']}: unknown gate")

    print("VCF architecture artifact is valid")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValidationError, json.JSONDecodeError, KeyError, IndexError, TypeError) as error:
        print(f"verification failed: {error}", file=sys.stderr)
        raise SystemExit(1)
